"""TestForge - Confluence requirements -> AI test cases -> AI automation scripts.

Run:  streamlit run app.py
"""
import io
import os
import warnings
import zipfile
from pathlib import Path

warnings.filterwarnings("ignore")

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()  # before importing testforge, which reads CLAUDE_MODEL / CLAUDE_EFFORT

from testforge.ai import CLAUDE_EFFORT, DEFAULT_MODEL, design_tests, model_for, provider  # noqa: E402
from testforge.automation import generate_script, to_csv, to_gherkin, traceability
from testforge.boundaries import extract_boundaries
from testforge.document import DocumentError, extract_text, split_stories
from testforge.models import PRIORITIES, TEST_TYPES, TestCase
from testforge.privacy import mask
from testforge.story_parser import StoryParseError, parse_story

ROOT = Path(__file__).parent
SAMPLES = sorted(list((ROOT / "sample_docs").glob("Confluence_*")) + list((ROOT / "sample_stories").glob("*.txt")))
MAX_STORIES = 6
MAX_TESTS_PER_SCRIPT = 10

st.set_page_config(page_title="TestForge - AI Test Cases & Automation", page_icon="🧪", layout="wide")

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("⚙️ Settings")
    env_key = os.getenv("ANTHROPIC_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
    api_key = st.text_input("AI API key (Claude or Gemini)", type="password",
                            placeholder="Loaded from .env" if env_key else "Paste key or set it in .env",
                            help="Claude keys start with sk-ant- (console.anthropic.com); Gemini keys with AIza "
                                 "(aistudio.google.com/apikey). Kept in memory only.") or env_key
    if provider(api_key) == "claude":
        model = model_for(api_key)
        st.caption("Model: `{}` · effort: `{}`".format(model, CLAUDE_EFFORT))
    else:
        model = st.text_input("Gemini model", value=DEFAULT_MODEL)
    offline = st.toggle("Force offline mode (rule-based only)", value=False,
                        help="Simulates the AI being unavailable to demonstrate the fallback.")
    if offline:
        st.warning("AI disabled - rule-based generator will be used.")
    elif api_key:
        st.success("{} key configured".format("Claude" if provider(api_key) == "claude" else "Gemini"))
    else:
        st.info("No API key - TestForge will use the rule-based fallback.")
    st.divider()
    st.caption("Privacy: patient MRNs, National ID / Iqama numbers, e-mails, phone numbers, credentials, API keys, "
               "internal URLs and IPs are masked **before** any text is sent to the AI.")

st.title("🧪 TestForge")
st.caption("Confluence requirements → AI test cases → AI automation scripts  ·  IT6070 proof of concept")
tab1, tab2 = st.tabs(["① Confluence document → Test cases", "② Test cases → Automation scripts"])


# ------------------------------------------------------------------ helpers
def tests_to_df(results):
    rows = []
    for r in results:
        for t in r["tests"]:
            rows.append({"Use": True, "Story": r["key"], "ID": t.tc_id, "Title": t.title, "ACs": ", ".join(t.ac_refs),
                         "Type": t.test_type, "Priority": t.priority, "Preconditions": t.preconditions,
                         "Steps": "\n".join(t.steps), "Test data": t.test_data,
                         "Expected result": t.expected_result, "Engine": t.source})
    return pd.DataFrame(rows)


def df_to_tests(df, results):
    """Edited table -> {story key: [TestCase]} (only ticked, valid rows)."""
    valid = {r["key"]: {c.ac_id for c in r["story"].criteria} for r in results}
    out = {r["key"]: [] for r in results}
    for _, row in df.iterrows():
        key = str(row.get("Story"))
        if not row.get("Use", True) or key not in valid:
            continue
        refs = [a.strip().upper() for a in str(row["ACs"]).split(",") if a.strip().upper() in valid[key]]
        steps = [s.strip() for s in str(row["Steps"]).split("\n") if s.strip()]
        if not refs or not steps or not str(row["Title"]).strip():
            continue
        out[key].append(TestCase(str(row["ID"]), str(row["Title"]), refs, str(row["Type"]), str(row["Priority"]),
                                 str(row["Preconditions"]), steps, str(row["Test data"]),
                                 str(row["Expected result"]), str(row["Engine"])))
    return out


def combined_csv(results, selected):
    parts = []
    for r in results:
        csv_text = to_csv(r["story"], selected.get(r["key"], []))
        parts.append(csv_text if not parts else csv_text.split("\n", 1)[1])
    return "".join(parts)


def zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


# ------------------------------------------------------------------ STEP 1
edited_df = None
with tab1:
    st.markdown("**Step 1.** Give TestForge a Confluence requirements page. It finds each user story and generates test cases.")
    c_in, c_opt = st.columns([3, 1])
    with c_opt:
        src = st.radio("Input", ["Upload Confluence export", "Paste page text", "Use sample"], index=2)
    with c_in:
        name, data = None, None
        if src == "Upload Confluence export":
            up = st.file_uploader("Confluence page export (Word .docx/.doc, PDF or HTML; max 5 MB)",
                                  type=["docx", "doc", "pdf", "html", "htm", "txt", "md"])
            if up is not None:
                name, data = up.name, up.getvalue()
            st.caption("In Confluence: ••• (More actions) → Export → Word or PDF.")
        elif src == "Paste page text":
            pasted = st.text_area("Paste the Confluence page content", height=220,
                                  placeholder="US-101 Register a new patient\nAs a receptionist, I want to ..., so that ...\n"
                                              "Acceptance Criteria\nAC1: ...")
            if pasted.strip():
                name, data = "pasted.txt", pasted.encode("utf-8")
        else:
            pick = st.selectbox("Synthetic sample", [p.name for p in SAMPLES])
            path = next(p for p in SAMPLES if p.name == pick)
            name, data = path.name, path.read_bytes()

    if st.button("📄 Read document", type="primary"):
        for k in ("doc", "results", "scripts"):
            st.session_state.pop(k, None)
        if not data:
            st.error("⚠️ Upload a Confluence export or paste the page text first.")
            st.stop()
        try:
            text = extract_text(name, data)
        except DocumentError as exc:
            st.error("⚠️ " + str(exc))
            st.stop()
        context, sections = split_stories(text)
        parsed, problems = [], []
        for s in sections:
            try:
                parsed.append((s, parse_story(s.text)))
            except StoryParseError as exc:
                problems.append("{}: {}".format(s.key, exc))
        if not parsed:
            st.error("⚠️ No usable user stories or acceptance criteria were found in this document. "
                     + " ".join(problems))
            st.stop()
        st.session_state["doc"] = dict(name=name, text=text, context=context, parsed=parsed, problems=problems)

    D = st.session_state.get("doc")
    if D:
        st.success("Read **{}** - found **{}** user stor{} with **{}** acceptance criteria.".format(
            D["name"], len(D["parsed"]), "y" if len(D["parsed"]) == 1 else "ies",
            sum(len(p.criteria) for _, p in D["parsed"])))
        for p in D["problems"]:
            st.warning("Skipped " + p)
        overview = pd.DataFrame([{"Story": s.key, "Title": s.title, "Acceptance criteria": len(p.criteria),
                                  "Boundaries": len(extract_boundaries(p.criteria)),
                                  "Notes": "; ".join(p.warnings) or "-"} for s, p in D["parsed"]])
        st.dataframe(overview, hide_index=True, use_container_width=True)
        with st.expander("Show text extracted from the document"):
            st.text(D["text"][:6000] + ("\n..." if len(D["text"]) > 6000 else ""))
        keys = [s.key for s, _ in D["parsed"]]
        chosen = st.multiselect("Stories to generate test cases for (max {})".format(MAX_STORIES), keys,
                                default=keys[:MAX_STORIES], max_selections=MAX_STORIES)

        if st.button("🧠 Generate test cases", type="primary", disabled=not chosen):
            st.session_state.pop("scripts", None)
            results, bar = [], st.progress(0.0, text="Generating test cases...")
            for n, (s, p) in enumerate([sp for sp in D["parsed"] if sp[0].key in chosen], 1):
                bar.progress((n - 1) / len(chosen), text="{} - asking the AI to design test cases...".format(s.key))
                bounds = extract_boundaries(p.criteria)
                tests, meta = design_tests(p, bounds, api_key=api_key, model=model, offline=offline, context=D["context"])
                for t in tests:
                    t.tc_id = "{}-{}".format(s.key, t.tc_id)
                results.append(dict(key=s.key, title=s.title, story=p, bounds=bounds, tests=tests, meta=meta))
            bar.empty()
            st.session_state["results"] = results
            st.session_state["editor_v"] = st.session_state.get("editor_v", 0) + 1

    R = st.session_state.get("results")
    if R:
        engines = {r["meta"].source for r in R}
        names = {"claude": "Anthropic Claude", "gemini": "Google Gemini"}
        if len(engines) == 1 and engines <= set(names):
            st.success("🤖 All test cases designed by {} (`{}`).".format(names[engines.pop()], R[0]["meta"].model))
        elif "fallback" in engines:
            reasons = {r["meta"].fallback_reason for r in R if r["meta"].source == "fallback"}
            st.warning("🛟 **Fallback mode** for some or all stories: {} Rule-based test design was used.".format(" ".join(reasons)))
        else:
            st.warning("🤖 AI + fallback: the AI missed some criteria, so rule-based tests were added to cover them.")
        masked = mask(D["text"])[1] if D else {}
        if masked:
            st.info("🔒 Masked before AI processing: " + ", ".join("{} × {}".format(k.lower(), v) for k, v in masked.items()))

        st.dataframe(pd.DataFrame([{
            "Story": r["key"], "Title": r["title"], "Test cases": len(r["tests"]),
            "AC coverage": "{:.0f}%".format(traceability(r["story"], r["tests"])[1]),
            "Types": ", ".join(sorted({t.test_type for t in r["tests"]})),
            "Engine": r["meta"].source + (" ({:,} ms)".format(r["meta"].latency_ms) if r["meta"].latency_ms else "")}
            for r in R]), hide_index=True, use_container_width=True)

        st.subheader("Test cases - review, edit and untick any you reject")
        edited_df = st.data_editor(
            tests_to_df(R), key="editor_{}".format(st.session_state.get("editor_v", 0)),
            hide_index=True, use_container_width=True, height=440,
            column_config={
                "Use": st.column_config.CheckboxColumn(width="small"),
                "Story": st.column_config.TextColumn(disabled=True, width="small"),
                "Type": st.column_config.SelectboxColumn(options=list(TEST_TYPES)),
                "Priority": st.column_config.SelectboxColumn(options=list(PRIORITIES)),
                "Steps": st.column_config.TextColumn(width="large"),
                "Expected result": st.column_config.TextColumn(width="large"),
                "Engine": st.column_config.TextColumn(disabled=True),
            })
        selected = df_to_tests(edited_df, R)
        d1, d2 = st.columns(2)
        d1.download_button("⬇️ Test cases for TestRail (.csv)", combined_csv(R, selected), file_name="test_cases_testrail.csv",
                           mime="text/csv")
        d2.download_button("⬇️ Gherkin feature files (.zip)",
                           zip_bytes({"{}.feature".format(r["key"]): to_gherkin(r["story"], selected[r["key"]]) for r in R}),
                           file_name="features.zip")
        with st.expander("Traceability: acceptance criteria → test cases"):
            for r in R:
                st.markdown("**{} - {}**".format(r["key"], r["title"]))
                st.dataframe(pd.DataFrame(traceability(r["story"], selected[r["key"]])[0]), hide_index=True,
                             use_container_width=True)
        st.info("➡️ Next: open tab **② Test cases → Automation scripts**.")

# ------------------------------------------------------------------ STEP 2
with tab2:
    R = st.session_state.get("results")
    if not R or edited_df is None:
        st.info("Generate test cases in tab ① first - automation scripts are built from the reviewed test cases.")
    else:
        st.markdown("**Step 2.** Generate automation scripts from the ticked test cases (one spec file per user story).")
        selected = df_to_tests(edited_df, R)
        c1, c2, c3 = st.columns([1.2, 1, 1.4])
        framework = c1.radio("Framework", ["cypress", "playwright"], horizontal=True,
                             format_func=lambda f: "Cypress (JavaScript)" if f == "cypress" else "Playwright (Python)")
        scope = c2.radio("Which test cases", ["High priority", "All ticked"], horizontal=True)
        base_url = c3.text_input("Application base URL", "http://localhost:3000")
        plan = {}
        for r in R:
            tests = selected[r["key"]]
            if scope == "High priority":
                tests = [t for t in tests if t.priority == "High"] or tests
            plan[r["key"]] = tests[:MAX_TESTS_PER_SCRIPT]
        st.caption(" · ".join("{}: {} test(s)".format(k, len(v)) for k, v in plan.items())
                   + "  (max {} per script)".format(MAX_TESTS_PER_SCRIPT))

        if st.button("⚙️ Generate automation scripts", type="primary"):
            if not base_url.startswith(("http://", "https://")):
                st.error("Base URL must start with http:// or https://")
            elif not any(plan.values()):
                st.error("No test cases are ticked in tab ①.")
            else:
                scripts, bar = {}, st.progress(0.0, text="Writing scripts...")
                todo = [r for r in R if plan[r["key"]]]
                for n, r in enumerate(todo, 1):
                    bar.progress((n - 1) / len(todo), text="{} - writing and checking the {} script...".format(r["key"], framework))
                    scripts[r["key"]] = generate_script(r["story"], plan[r["key"]], base_url, api_key, model, offline,
                                                        framework=framework)
                bar.empty()
                st.session_state["scripts"] = scripts

        S = st.session_state.get("scripts")
        if S:
            fw = next(iter(S.values()))[1]["framework"]
            ext = ".cy.js" if fw == "cypress" else ".py"
            names = {k: ("{}{}".format(k, ext) if fw == "cypress" else "test_{}.py".format(k.replace("-", "_").lower()))
                     for k in S}
            st.download_button("⬇️ Download all scripts (.zip)", zip_bytes({names[k]: c for k, (c, _) in S.items()}),
                               file_name="{}_scripts.zip".format(fw), type="primary")
            for k, (code, info) in S.items():
                ok_ai = info["source"] in ("claude", "gemini")
                with st.expander("{} {} - {}".format("🤖" if ok_ai else "🛟", names[k],
                                                     "written by " + info["source"].title() if ok_ai else "template fallback"),
                                 expanded=True):
                    if not ok_ai:
                        st.warning("Template fallback used: " + (info["reason"] or ""))
                    for msg in info["messages"]:
                        st.caption(("⚠ " if msg.startswith(("Warning", "Note")) else "✔ ") + msg)
                    st.code(code, language="javascript" if fw == "cypress" else "python")
                    st.download_button("⬇️ " + names[k], code, file_name=names[k], key="dl_" + k)
            st.caption("Cypress: copy the files to cypress/e2e/, set baseUrl in cypress.config.js, run `npx cypress run`.  "
                       "Playwright: `pip install pytest-playwright && playwright install`, then `pytest`.")
