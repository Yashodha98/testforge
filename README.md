# TestForge: Confluence Requirements → AI Test Cases → AI Automation Scripts

IT6070 Emerging Topics in IT, Assignment II (SLIIT MSc/PGD IT 2026)

TestForge is a proof of concept for a **Senior QA Engineer working on a Hospital Management System**. Today they read requirements in Confluence, write test cases by hand (about 1 day per epic), manage them in TestRail and automate them in Cypress. The problem and requirements come from a real interview (anonymised; see the evidence PDF).

**Workflow: Confluence page → ① test cases → ② automation scripts**

| | |
|---|---|
| Emerging technology | Generative AI / large language model (Anthropic Claude API; Google Gemini also supported), with masking of patient and confidential data and static checks on AI-generated code |
| Step 1: Confluence document → test cases | Upload a Confluence page export (Word `.docx`, Confluence "Export to Word" `.doc`, PDF or HTML) or paste the page text. TestForge finds every user story and its acceptance criteria (ACs) and computes exact boundary values. Gemini then designs Positive, Negative, Boundary, Edge and Security test cases for each story. These are validated, de-duplicated and guaranteed to cover every AC. The QA engineer reviews and edits them, then downloads a **TestRail CSV** (plus Gherkin files). |
| Step 2: test cases → automation scripts | From the ticked test cases, the LLM (Claude) writes one **Cypress** spec per story (Playwright is optional). Each spec is checked for syntax (`node --check`), structure and unsafe calls before it is shown, and it is never executed. All specs download as a ZIP. |
| Fallback | A rule-based test generator (one Positive and one Negative test per AC, plus boundary-value tests) and template scripts. These are used when there is no key, the quota is exhausted, the network fails, the JSON is invalid, or the generated code fails the checks. |

## 1. Software requirements
- macOS, Windows or Linux
- Python **3.9+**
- Internet access for the AI API (optional; the app works offline in fallback mode)
- An Anthropic Claude API key with credit (optional), or a free Google Gemini API key
- Node.js (optional) enables the JavaScript syntax check of generated Cypress specs
- To *run* the generated specs (optional): copy them into a Cypress project's `cypress/e2e/` and run `npx cypress run`

## 2. Installation
```bash
git clone <your-repo-url> testforge
cd testforge
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then paste your Claude (or Gemini) key into .env
```

## 3. Running
```bash
streamlit run app.py
```
1. Open http://localhost:8501.
2. In tab ①, choose **Use sample → Confluence_HMS_Outpatient_Requirements.docx** (or upload your own Confluence export) and click **Read document**. Three user stories are found.
3. Click **Generate test cases**, review or edit the table, and download the TestRail CSV.
4. In tab ②, choose **Cypress** and click **Generate automation scripts**, then download the ZIP.

Toggle **Force offline mode** in the sidebar to demonstrate the fallback.

**How to export a page from Confluence:** open the page, then choose ••• (More actions) → Export → Word or PDF.

## 4. API / configuration
| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | No (fallback without it) | Claude key (`sk-ant-...`) from https://console.anthropic.com/settings/keys; prepaid credit needed. Used first if set. You can also paste it in the sidebar; it is held in memory only. |
| `CLAUDE_MODEL` | No | Default `claude-opus-5-5`. Set `claude-haiku-5-5` for a much cheaper, faster model. |
| `GEMINI_API_KEY` | No (fallback without it) | Free key from https://aistudio.google.com/apikey. You can also paste it in the sidebar; it is held in memory only. |
| `GEMINI_MODEL` | No | Default `gemini-2.5-flash`. Change it if Google renames or retires the model. |

No keys or passwords are included in this repository. `.env` is git-ignored.

## 5. Login details
Not applicable. The PoC is a single-user local app.

## 6. Tests
```bash
python -m pytest -q      # 40 automated unit tests (AI mocked, no key needed)
python evaluate.py       # 5 formal test cases for the evidence document -> docs/test_results.md
```

## 7. Project structure
```
app.py                      Streamlit UI: Step 1 (document -> test cases), Step 2 (test cases -> scripts)
testforge/document.py       Reads Confluence exports (.docx/.doc/.pdf/.html/.txt) and splits them into user stories
testforge/story_parser.py   Parses role/goal/benefit and acceptance criteria (AC1:, bullets, tables, Given/When/Then)
testforge/boundaries.py     Boundary Value Analysis from numeric constraints (ranges, min/max, exact length)
testforge/privacy.py        Masks MRN, National ID/Iqama, e-mails, phones, credentials, API keys, internal URLs, IPs
testforge/ai.py             AI test design (Claude or Gemini): prompt, JSON validation, de-duplication, coverage-gap filling
testforge/fallback.py       Offline rule-based test generator
testforge/automation.py     TestRail CSV / Gherkin export, Cypress & Playwright generation + static safety checks
sample_docs/                SYNTHETIC Confluence exports of one requirements page (docx, doc, pdf, html) + generator
sample_stories/             SYNTHETIC single-story text files
tests/                      pytest suite
evaluate.py                 Formal test cases
```

## 8. Simulated / external components (declared)
- **All sample documents are synthetic.** The Confluence page "HMS Release 4.2 – Outpatient Module" was written for this PoC and contains no real client requirements or patient data. The `.doc` sample imitates Confluence's MIME-based "Export to Word" format.
- **Anthropic Claude** (or Google Gemini) is an external, third-party LLM. The provider is chosen from the key: keys starting `sk-ant-` use Claude. Its test cases and scripts are drafts that the QA engineer must review.
- The generated Cypress/Playwright specs are **skeletons**. Selectors marked `TODO: confirm selector` must be adapted to the real application. There is no demo system under test in this repository.
- There is no direct Confluence API connection. The page is exported or pasted by hand.

## 9. Known limitations
- The LLM can miss business or clinical rules, duplicate scenarios, or invent behaviour that is not in the story. TestForge mitigates this with validation, de-duplication, coverage-gap filling and human review, but cannot remove it entirely.
- Story detection relies on common Confluence layouts: story keys such as `US-101`, "As a … I want …" sentences, AC lists or tables. Unusual page layouts may be read as a single story.
- Scanned (image) PDFs have no text layer and cannot be read. Export the page as Word or HTML instead.
- Boundary extraction uses patterns (e.g. "between 2 and 60 characters", "exactly 10 digits"), so unusual wording can be missed.
- Generated scripts are only checked statically. They are not executed, because the PoC has no application to test.
- Masking is regex-based. Identifiers such as MRN, National ID/Iqama and phone numbers are masked, but patient *names* in free text are not detected.
- Live AI runs in this project used Claude because Google blocked Gemini API-key creation for the student account. The Gemini free tier may use submitted data to improve Google products, so real client (healthcare) requirements need the paid tier under a data-processing agreement, or a self-hosted model.

## 10. AI use declaration
Claude (Anthropic) via Claude Code was used to help design and write the code, tests, sample documents and documentation. All output was reviewed, run and tested by the student. See the Technical Evidence Document, page 3.
