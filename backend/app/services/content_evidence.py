"""Bounded content evidence for a document: what its first pages say it
is, in words, kept beside its reading (`extracted["evidence"]`) for the
classification to weigh (app.services.document_classification).

The document-control readers store *records* -- the references, revisions
and decisions the registers are built from -- and nothing about a page
that carries none: a certificate, a datasheet, a load schedule, a scanned
transmittal, a single-line diagram all stored as "nothing read", and were
classified from their path alone, or not at all. This stage reads the
first pages for the kind of document they are and stores every finding
with its page, an excerpt, how it was read (the text layer, or OCR of a
scanned page already done by the reader and kept in the page cache), the
content hash it was read from and the rule version -- so a finding can be
checked against the page and is never taken for more than a finding.

Bounded on purpose: the first `PAGES` pages, the text layer only, plus
the OCR text the reader already made of a scanned first page. Nothing is
rendered or OCRed here; a 190-page catalogue is looked at for three pages
and says so (`pages_read`). It runs in the reader process with the PDF
already open (document_sync.extract), never in the fast File Sync.

What it finds is evidence of a *kind* of content, never a business fact:
a receipt signature is a receipt, not an approval; a consultant's comment
sheet is the consultant's, not the contractor's reply; a project code
printed on the page is compared with the project the file is indexed
under and a difference is reported, never acted on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Bumped when a rule changes: what a page was found to hold is then found again.
EVIDENCE_VERSION = "evidence-2026-09-28.2"
PAGES = 3
EXCERPT = 160

# The kinds of content a page can be found to hold, each by the words that
# name it on the page itself. Generic: no project, contractor or file name.
# Two patterns per kind: `strong` names the kind (a title, a form's own
# heading, a certificate's own words); `weak` is a phrase that often goes
# with it but also appears elsewhere ("product data" in a specification's
# submittals clause, "connected load" on a single-line diagram). A strong
# match near the top of the first page is a `title`. The classification
# supports a type from title or strong findings only; weak ones are
# evidence lines, never a type.
_RULES: tuple[tuple[str, re.Pattern, re.Pattern | None], ...] = (
    ("shop_drawing_submittal", re.compile(r"\bshop\s*drawings?\s+submittal\b|\bdrawing\s+submittal\s+form\b", re.I), None),
    ("material_submittal", re.compile(r"\bmaterials?\s+submittal\b|\bMAS\s+reference\b|\bmaterial\s+approval\s+request\b", re.I), None),
    ("sample_submittal", re.compile(r"\bsample\s+(?:approval|submittal|board)\b|\bSAR\s+reference\b", re.I), None),
    ("transmittal", re.compile(r"\bdocument\s+transmitt?al\b|\btransmittal\s+(?:note|form|sheet|letter)\b", re.I),
     re.compile(r"\bAASS\s+Ref\b", re.I)),
    ("receipt", re.compile(r"\breceived\s+by\b|\backnowledg(?:e|ed|ement)\b|\breceipt\b", re.I), None),
    ("consultant_comments", re.compile(r"\badditional\s+comments\s+sheet\b|\bconsultant(?:'s)?\s+comments?\s+sheet\b|\bcomments?\s+sheet\b", re.I),
     re.compile(r"\bconsultant(?:'s)?\s+(?:comments|remarks)\s*:", re.I)),
    ("contractor_reply", re.compile(r"\breply\s+to\s+(?:the\s+)?(?:\w+\s+){0,3}(?:consultant|comments)|\bcontractor(?:'s)?\s+repl(?:y|ies)\b", re.I),
     re.compile(r"\b(?:al\s+arabia|contractor|specialist)\s+(?:ssd\s+)?reply\b", re.I)),
    ("decision_status", re.compile(r"\bstatus\s*[-:]\s*[ABCD]\b|\b(?:code|status)\s*[ABCD]\b", re.I), None),
    ("datasheet", re.compile(r"\bdata\s*sheets?\b|\bdatasheets?\b|\bnot\s+to\s+be\s+used\s+for\s+installation\b", re.I),
     re.compile(r"\btechnical\s+data\b|\bproduct\s+data\b", re.I)),
    ("catalogue", re.compile(r"\bcatalog(?:ue)?s?\b|\bbrochure\b", re.I), None),
    ("certificate", re.compile(r"\bcertificate\s+of\s+(?:approval|conformity|compliance|registration|origin)\b|\bthis\s+is\s+to\s+certify\b"
                               r"|\bhereby\s+(?:certif(?:y|ies)|declares|grants)\b|\bcertificate\s+number\b", re.I),
     re.compile(r"\bcertificate\s+no\.?\b|\bISO\s+\d{4,5}\s*:\s*\d{4}\b|\b(?:ENEC|UL|LPCB|FM)\s+(?:certification|approval|listing)\b", re.I)),
    ("load_schedule", re.compile(r"\bload\s+schedule\b|\bsummary\s+of\s+connected\s+load\b", re.I),
     re.compile(r"\bconnected\s+load\b|\bmaximum\s+demand\b", re.I)),
    ("vendor_list", re.compile(r"\bvendor\s+list\b|\blist\s+of\s+makes\b|\bapproved\s+(?:makes|vendors|manufacturers)\b", re.I),
     re.compile(r"\bproposed\s+vendor\b", re.I)),
    ("scope_matrix", re.compile(r"\bresponsibility\s+matrix\b|\bscope\s+(?:of\s+)?matrix\b|\bscope\s+of\s+work\s+matrix\b", re.I), None),
    ("compliance_statement", re.compile(r"\bcompliance\s+statement\b|\bstatement\s+of\s+compliance\b", re.I), None),
    ("specification", re.compile(r"\bSECTION\s+\d{6}\b|\bSECTION\s+\d{2}\s?\d{2}\s?\d{2}\b|\bPART\s+[123]\s+(?:GENERAL|PRODUCTS|EXECUTION)\b"
                                 r"|\b\d{6}\s*-\s*[A-Z][A-Z ]{3,}\b", re.I),
     re.compile(r"\bthis\s+section\s+(?:of\s+the\s+specification\s+)?includes\b", re.I)),
    ("single_line_diagram", re.compile(r"\bsingle\s+line\s+diagram\b", re.I), re.compile(r"\bschematic\s+diagram\b|\bSLD\b", re.I)),
    ("drawing_title_block", re.compile(r"(?!x)x"), re.compile(r"\bdrawing\s+(?:no\.?|number|title)\b|\bDWG\s*NO\b|\bscale\s*:\s*(?:\d+:\d+|NTS)\b", re.I)),
    ("method_statement", re.compile(r"\bmethod\s+statement\b|\brisk\s+assessment\b", re.I), None),
    ("design_request_form", re.compile(r"\bdesign\s+request\s+form\b", re.I), None),
)
# Kinds that name a controlled document (a form, a letter, a decision) as
# against reference material (a datasheet, a schedule, a specification).
CONTROLLED_KINDS = frozenset({"shop_drawing_submittal", "material_submittal", "sample_submittal", "transmittal",
                              "consultant_comments", "contractor_reply", "design_request_form"})
# Cues for who wrote a page, and to whom: a reviewer's sheet or a contractor's answer.
_CONSULTANT_AUTHOR = re.compile(r"\b(?:engineering\s+)?consultants?\b.{0,40}\b(?:comments|recommendation|review)\b|\bresident\s+engineer\b", re.I | re.S)
_CONTRACTOR_AUTHOR = re.compile(r"\b(?:al\s+arabia|contractor|specialist|supplier|sub-?contractor)(?:'s)?\s+(?:ssd\s+)?repl(?:y|ies)\b"
                                r"|\bnoted\s*(?:&|and)\s+complied\b", re.I)
# A platform project code printed on the page ("EP-30058"): compared with the indexed project, never acted on.
_EP_CODE = re.compile(r"\bEP[- ]?(\d{5})\b")
# A legend explains a form's codes ("TDS - Technical Data Sheet"): it names
# every kind and is evidence of none.
_LEGEND_LINE = re.compile(r"(?im)^.*\blegend\b.*$")
TITLE_ZONE = 350


@dataclass(frozen=True)
class Finding:
    kind: str
    page: int          # 1-based
    excerpt: str
    method: str        # "text" | "ocr"
    rule: str = ""
    grade: str = "strong"   # "title" | "strong" | "weak"

    def to_dict(self, sha256: str | None) -> dict:
        return {"kind": self.kind, "page": self.page, "excerpt": self.excerpt, "method": self.method,
                "rule": self.rule or self.kind, "grade": self.grade, "version": EVIDENCE_VERSION, "content_sha256": sha256}


def _excerpt(text: str, start: int, end: int) -> str:
    left = max(0, start - 50)
    right = min(len(text), end + 90)
    return " ".join(text[left:right].split())[:EXCERPT]


def scan_texts(texts: dict[int, tuple[str, str]]) -> list[Finding]:
    """The findings on pages given as {index: (text, method)}: each kind at
    most once per page, with the excerpt that named it; the project codes
    printed; who appears to have written the page."""
    findings: list[Finding] = []
    for index in sorted(texts):
        text, method = texts[index]
        if not text or not text.strip():
            continue
        text = _LEGEND_LINE.sub("", text)
        for kind, strong, weak in _RULES:
            match = strong.search(text)
            if match:
                grade = "title" if index == 0 and match.start() < TITLE_ZONE else "strong"
                findings.append(Finding(kind, index + 1, _excerpt(text, match.start(), match.end()), method, grade=grade))
                continue
            match = weak.search(text) if weak is not None else None
            if match:
                findings.append(Finding(kind, index + 1, _excerpt(text, match.start(), match.end()), method, grade="weak"))
        for code in dict.fromkeys(_EP_CODE.findall(text)):
            match = re.search(r"\bEP[- ]?" + code + r"\b", text)
            findings.append(Finding("project_code", index + 1, _excerpt(text, match.start(), match.end()), method, rule=f"EP-{code}"))
        if _CONSULTANT_AUTHOR.search(text):
            match = _CONSULTANT_AUTHOR.search(text)
            findings.append(Finding("author_consultant", index + 1, _excerpt(text, match.start(), match.end()), method))
        if _CONTRACTOR_AUTHOR.search(text):
            match = _CONTRACTOR_AUTHOR.search(text)
            findings.append(Finding("author_contractor", index + 1, _excerpt(text, match.start(), match.end()), method))
    return findings


def scan_pdf(pdf, page_texts: dict[int, str], sha256: str | None, *, pages: int = PAGES) -> dict:
    """The evidence of an open PDF: its first `pages` pages' text layers
    (reusing what the reader extracted), and for a scanned page the OCR
    text the reader already made and kept in the page cache -- nothing is
    rendered or OCRed here. Returns {"findings": [...], "pages_read": n,
    "page_count": total, "version": ..., "scanned_pages": [...]}."""
    from app.services import document_control, page_cache

    texts: dict[int, tuple[str, str]] = {}
    scanned: list[int] = []
    unread: list[int] = []
    count = pdf.page_count
    for index in range(min(pages, count)):
        try:
            text = document_control.page_text(pdf[index], index, page_texts)
        except Exception:  # noqa: BLE001 -- a page MuPDF cannot read: not evidence, and said so
            unread.append(index + 1)
            continue
        if len(text.strip()) >= 80:
            texts[index] = (text, "text")
            continue
        scanned.append(index + 1)
        ocr = page_cache.get_ocr(sha256, index) if sha256 else None
        if ocr and ocr.strip():
            texts[index] = (ocr, "ocr")
        elif text.strip():
            texts[index] = (text, "text")
    findings = scan_texts(texts)
    return {"version": EVIDENCE_VERSION, "pages_read": len(texts), "page_count": count,
            "scanned_pages": scanned, "unread_pages": unread,
            "findings": [f.to_dict(sha256) for f in findings]}


def kinds_of(evidence: dict | None, *, grades: tuple[str, ...] = ("title", "strong", "weak")) -> set[str]:
    return {f.get("kind") for f in ((evidence or {}).get("findings") or [])
            if isinstance(f, dict) and (f.get("grade") or "strong") in grades}


def printed_projects(evidence: dict | None) -> list[str]:
    """The project codes printed on the pages read, as "EP-nnnnn"."""
    return list(dict.fromkeys(f["rule"] for f in ((evidence or {}).get("findings") or [])
                              if isinstance(f, dict) and f.get("kind") == "project_code"))
