"""Reads a Confluence requirements page and splits it into user stories.

Confluence pages can be exported as Word (.docx, or the HTML-based .doc that
"Export to Word" produces), PDF or HTML. This module turns any of those into
plain text, keeping headings, bullet lists and table rows, and then finds
the individual user stories on the page.
"""
from __future__ import annotations

import email
import io
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import List, Tuple

MAX_BYTES = 5 * 1024 * 1024
SUPPORTED = (".docx", ".doc", ".pdf", ".html", ".htm", ".txt", ".md")


class DocumentError(ValueError):
    """Raised when a document cannot be read."""


@dataclass
class StorySection:
    key: str  # e.g. "US-101" or "S1"
    title: str
    text: str


# ------------------------------------------------------------------ text extraction
def extract_text(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if not data:
        raise DocumentError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise DocumentError("The file is larger than 5 MB. Export only the requirements page.")
    if not name.endswith(SUPPORTED):
        raise DocumentError("Unsupported file type. Use a Confluence export: .docx, .doc, .pdf or .html (or .txt/.md).")
    try:
        if name.endswith(".docx"):
            text = _from_docx(data)
        elif name.endswith(".pdf"):
            text = _from_pdf(data)
        elif name.endswith((".html", ".htm", ".doc")):
            text = _from_html(_html_payload(data))
        else:
            text = _decode(data)
    except DocumentError:
        raise
    except Exception as exc:  # corrupt or password-protected files
        raise DocumentError("Could not read the document ({}). Re-export it from Confluence and try again.".format(
            type(exc).__name__))
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(re.findall(r"[A-Za-z]{3,}", text)) < 5:
        raise DocumentError("No readable text was found. If the PDF is a scanned image, export the page as Word or HTML instead.")
    return text


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "utf-16", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise DocumentError("The file is not readable text.")


def _table_row(cells: List[str]) -> str:
    cells = [re.sub(r"\s+", " ", c).strip() for c in cells]
    cells = [c for c in cells if c]
    if not cells:
        return ""
    joined = " ".join(cells).lower()
    if re.fullmatch(r"(id|#|no\.?|ac|ac id)?\s*(acceptance criteri(a|on)|criteria|description|scenario|expected result|priority|status|\s)*", joined):
        return ""  # header row
    if re.fullmatch(r"(AC|US)[- ]?\d+", cells[0], re.I) and len(cells) > 1:
        return "{}: {}".format(cells[0].upper().replace(" ", ""), " - ".join(cells[1:]))
    return "- " + " - ".join(cells)


def _from_docx(data: bytes) -> str:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = docx.Document(io.BytesIO(data))
    out: List[str] = []
    for block in doc.element.body.iterchildren():
        tag = block.tag.split("}")[-1]
        if tag == "p":
            p = Paragraph(block, doc)
            t = p.text.strip()
            if not t:
                out.append("")
                continue
            style = (p.style.name or "").lower() if p.style is not None else ""
            is_list = "list" in style or block.find(".//{*}numPr") is not None
            out.append(("- " + t) if is_list and not re.match(r"^(AC\d+|[-*•])", t) else t)
        elif tag == "tbl":
            for row in Table(block, doc).rows:
                line = _table_row([c.text for c in row.cells])
                if line:
                    out.append(line)
            out.append("")
    return "\n".join(out)


def _from_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise DocumentError("The PDF is password-protected.")
    raw = "\n".join((page.extract_text() or "") for page in reader.pages)
    return _reflow(raw)


def _reflow(raw: str) -> str:
    """PDF text breaks sentences at the page width; join wrapped lines back together.

    A line is joined to the previous one when the previous line does not end a
    sentence and either the line starts in lower case or the previous line is
    long (i.e. it was wrapped, not a short heading).
    """
    out: List[str] = []
    for ln in raw.splitlines():
        s = ln.strip()
        if not s:
            out.append("")
            continue
        s = re.sub(r"^[•●▪–]\s*", "- ", s)  # PDF bullet glyphs
        prev = out[-1] if out else ""
        if prev and not prev.endswith((".", "!", "?", ":", ";")) and not s.startswith("- ") \
                and (s[0].islower() or s[0] in "\"'(" or len(prev) > 60):
            out[-1] = prev + " " + s
        else:
            out.append(s)
    return "\n".join(out)


def _html_payload(data: bytes) -> str:
    """Confluence 'Export to Word' (.doc) is MIME/MHTML wrapping HTML."""
    head = data[:2000].lower()
    if b"mime-version" in head or b"multipart/related" in head:
        msg = email.message_from_bytes(data)
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        raise DocumentError("No HTML content found inside the Confluence Word export.")
    if data[:4] == b"\xd0\xcf\x11\xe0":
        raise DocumentError("This is a legacy binary Word file. Save it as .docx and upload again.")
    return _decode(data)


class _HTMLText(HTMLParser):
    BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "ul", "ol", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines: List[str] = []
        self.buf: List[str] = []
        self.cells: List[str] = []
        self.in_cell = False
        self.skip = 0

    def _flush(self, prefix=""):
        t = " ".join("".join(self.buf).split())
        if t:
            self.lines.append(prefix + t)
        self.buf = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self.skip += 1
        elif tag in ("td", "th"):
            self.in_cell, self.buf = True, []
        elif tag == "li":
            self._flush()
            self.buf = ["\x00LI"]
        elif tag in self.BLOCK and not self.in_cell:
            self._flush()

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("td", "th"):
            self.cells.append(" ".join("".join(self.buf).split()))
            self.buf, self.in_cell = [], False
        elif tag == "tr":
            line = _table_row(self.cells)
            if line:
                self.lines.append(line)
            self.cells = []
        elif tag == "li":
            t = " ".join("".join(self.buf).replace("\x00LI", "").split())
            if t:
                self.lines.append("- " + t)
            self.buf = []
        elif tag in self.BLOCK and not self.in_cell:
            self._flush()
            if tag in ("table", "ul", "ol") or tag.startswith("h"):
                self.lines.append("")

    def handle_data(self, d):
        if not self.skip:
            self.buf.append(d)


def _from_html(html: str) -> str:
    p = _HTMLText()
    p.feed(html)
    p._flush()
    return "\n".join(p.lines)


# ------------------------------------------------------------------ story splitting
STORY_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d{1,6})\b")
HEADER_RE = re.compile(r"^\s*(?:#+\s*)?(?:(?:user\s+)?story\b|[A-Z][A-Z0-9]{1,9}-\d{1,6}\b)", re.I)
AS_A_RE = re.compile(r"^\s*(?:[-*]\s*)?as an?\s+.+?\bi\s+(?:want|need|would like)\b", re.I)


def _is_heading(line: str) -> bool:
    s = line.strip()
    return 0 < len(s) <= 90 and not s.endswith((".", ",", ";")) and not s.startswith("-")


def split_stories(text: str) -> Tuple[str, List[StorySection]]:
    """Return (page_context, stories). One story is returned when no boundaries are found."""
    lines = text.splitlines()
    starts: List[int] = []
    for i, ln in enumerate(lines):
        if HEADER_RE.match(ln) and _is_heading(ln):
            starts.append(i)
        elif AS_A_RE.match(ln):
            j = i - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            start = j if j >= 0 and _is_heading(lines[j]) and (not starts or j > starts[-1]) else i
            if not starts or start - starts[-1] > 2:
                starts.append(start)
    starts = sorted(set(starts))
    # merge a header line immediately followed by its own "As a" line
    merged: List[int] = []
    for s in starts:
        if merged and s - merged[-1] <= 2 and not AS_A_RE.match(lines[merged[-1]]):
            continue
        merged.append(s)
    if not merged:
        return "", [StorySection("S1", _title_of(text), text.strip())]

    context = "\n".join(lines[:merged[0]]).strip()
    stories: List[StorySection] = []
    for n, s in enumerate(merged):
        e = merged[n + 1] if n + 1 < len(merged) else len(lines)
        chunk = "\n".join(lines[s:e]).strip()
        if len(chunk) < 25:
            continue
        first = lines[s].strip().lstrip("#").strip()
        m = STORY_KEY_RE.search(first)
        key = m.group(1) if m else "S{}".format(len(stories) + 1)
        title = first if not AS_A_RE.match(first) else _title_of(chunk)
        body = chunk if AS_A_RE.match(first) else "Title: {}\n{}".format(title, "\n".join(chunk.splitlines()[1:]))
        stories.append(StorySection(key, title[:100], body))
    return context[:2000], stories


def _title_of(text: str) -> str:
    for ln in text.splitlines():
        if ln.strip():
            return ln.strip()[:100]
    return "Requirements"
