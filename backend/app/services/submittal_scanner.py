"""Find the project's material submittals in its archive folder and read the
consultant's reply off each.

A submittal is its form: the first page carries "MAS Reference No.", the
revision, what it covers and, once it comes back, the consultant's approval
status. The form is a page of a package as often as a file of its own, so
every PDF's first page is checked, and the same submittal is usually filed
twice -- once where it was prepared, once under the approval folder.

**The reply is a stamp, not text.** The form's own checkboxes ("Approved
(A) / Approved as Noted (B) / Re-Submit (C)") stay unticked in the PDF's
text; the consultant's decision arrives as a stamp image over the page. So
the page is OCR'd and the stamp read from it -- "(B) Approved As Noted",
"(C) Revise & Resubmit". The empty checkbox row names all three codes at
once, so a line naming more than one is never read as the answer; a line
naming exactly one, next to its word, is.

Without a reply the submittal is out for review, which is what the register
shows -- never an approval that was not given.
"""

import io
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pymupdf

from app.core.config import get_settings

# Reading every PDF's first page is the cost of finding forms filed anywhere
# in the project; these keep a big archive from turning it into a crawl.
MAX_PDFS = 2000
OCR_DPI = 200

_REFERENCE_RE = re.compile(r"MAS\s*Reference\s*No\.?\s*:?\s*\n?\s*([A-Z0-9][A-Z0-9\-/]{6,})", re.IGNORECASE)
_REVISION_RE = re.compile(r"MAS\s*Rev\.?\s*:?\s*\n?\s*([A-Za-z0-9]{1,4})", re.IGNORECASE)
_DESCRIPTION_RE = re.compile(r"(Material\s+Submittal\s+for\s+[^\n]+)", re.IGNORECASE)
_DATE_RE = re.compile(r"MAS\s*\n?\s*Date:?\s*\n?\s*([0-9]{1,2}\s+\w+\s+[0-9]{4}|[0-9./-]{6,12})", re.IGNORECASE)
_SUPPLIER_RE = re.compile(r"M/[Ss]\.?\s*([A-Za-z][A-Za-z0-9&.\- ]{2,60})")

# The consultant's stamp: a code beside its word, either way round.
_CODE_WORDS = {"A": r"approved", "B": r"approved\s*as\s*noted", "C": r"revise|re-?\s*submit"}
_TICKED_RE = {
    "A": re.compile(r"☒\s*Approved\s*\(A\)", re.IGNORECASE),
    "B": re.compile(r"☒\s*Approved\s*as\s*Noted\s*\(B\)", re.IGNORECASE),
    "C": re.compile(r"☒\s*Re-?\s*Submit\s*\(C\)", re.IGNORECASE),
}

SYSTEM_KEYWORDS = [
    ("FRC", r"fire\s*(rated|resistant)\s*cable|\bFRC\b|\bcables?\b"),
    ("ELS", r"emergency\s*light|self\s*contained|central\s*battery"),
    ("FAS", r"fire\s*alarm|voice\s*evacuation|fire\s*telephone"),
]
# The folder a form sits in says it plainer than its title does.
FOLDER_SYSTEMS = [("FRC", r"\bFRC\b"), ("ELS", r"\bEML\b|\bELS\b|\bCBS\b"), ("FAS", r"\bFA\b|fire\s*alarm")]
APPROVAL_FOLDER_RE = re.compile(r"approval|approved", re.IGNORECASE)


@dataclass
class ScannedForm:
    path: Path
    relative: str
    reference: str
    revision: str  # "R00"
    title: str
    system_code: str | None
    supplier: str | None
    submitted: str | None
    reply_code: str | None  # "A", "B" or "C"
    reply_text: str | None  # the line it was read from
    ocr_used: bool
    in_approval_folder: bool
    modified: datetime

    @property
    def status(self) -> str:
        """A and B are approvals (B with comments to take up); C is sent
        back; nothing read means it is still with the consultant."""
        return {"A": "approved", "B": "approved", "C": "rejected"}.get(self.reply_code or "", "under_review")


def ocr_available() -> bool:
    """Tesseract is a system binary, not a pip package: it may not be there."""
    try:
        import pytesseract

        settings = get_settings()
        if settings.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
        pytesseract.get_tesseract_version()
        return True
    except Exception:  # noqa: BLE001
        return False


def read_reply_code(text: str) -> tuple[str | None, str | None]:
    """(code, the line it was read from) for the consultant's reply."""
    for line in text.splitlines():
        ticked = "☒" in line
        # An unticked checkbox is the blank form, not an answer.
        if "☐" in line and not ticked:
            continue
        codes = set(re.findall(r"\(\s*([ABC])\s*\)", line))
        # A row naming all three codes (the checkbox row as OCR reads it,
        # its boxes lost) answers nothing either.
        if len(codes) != 1:
            continue
        code = codes.pop()
        words = _CODE_WORDS[code]
        if ticked and re.search(words, line, re.IGNORECASE):
            return code, line.strip()
        # The consultant's stamp puts the code first ("(B) Approved As
        # Noted"); the blank form prints the label with the code after it
        # ("Approved as Noted (B)"), which is no answer at all -- so that
        # order counts only when the line carries more than the label.
        if re.search(rf"\(\s*{code}\s*\)\s*(?:{words})\b", line, re.IGNORECASE):
            return code, line.strip()
        label = re.search(rf"(?:{words})\s*\(\s*{code}\s*\)", line, re.IGNORECASE)
        if label and len(re.sub(r"[^A-Za-z0-9]", "", line)) > len(re.sub(r"[^A-Za-z0-9]", "", label.group(0))) + 3:
            return code, line.strip()
    # Some consultants tick the form rather than stamping it, the tick and
    # the label falling on separate lines.
    for code, pattern in _TICKED_RE.items():
        if pattern.search(text):
            return code, f"Ticked on the form ({code})"
    return None, None


def _system_code(relative: str, title: str) -> str | None:
    for code, pattern in FOLDER_SYSTEMS:
        if re.search(pattern, str(Path(relative).parent), re.IGNORECASE):
            return code
    for code, pattern in SYSTEM_KEYWORDS:
        if re.search(pattern, title, re.IGNORECASE):
            return code
    return None


def _ocr_page(page: pymupdf.Page) -> str:
    import pytesseract
    from PIL import Image

    image = Image.open(io.BytesIO(page.get_pixmap(dpi=OCR_DPI).tobytes("png")))
    return pytesseract.image_to_string(image)


def read_form(path: Path, root: Path, use_ocr: bool = True) -> ScannedForm | None:
    """The submittal form on a PDF's first page, or None if it has none."""
    try:
        doc = pymupdf.open(path)
    except Exception:  # noqa: BLE001 -- an unreadable or online-only file
        return None
    with doc:
        if not doc.page_count:
            return None
        page = doc[0]
        text = page.get_text()
        reference = _REFERENCE_RE.search(text)
        # "-MAS-" is what makes it a material submittal: the sample approval
        # form quotes the submittal's reference but is not one.
        if not reference or "-MAS-" not in reference.group(1).upper():
            return None
        ref = reference.group(1).strip().rstrip("-")

        description = _DESCRIPTION_RE.search(text)
        revision = _REVISION_RE.search(text)
        date = _DATE_RE.search(text)
        # The submittal's own remarks name the supplier and the manufacturer;
        # below the contractor's confirmation the page is comments and stamps.
        # ... and "Main Contractor:" in the project details above is not that
        # section, so the split is on its heading.
        head = re.split(r"MAIN\s+CONTRACTOR\s+CONFIRMATION", text, maxsplit=1, flags=re.IGNORECASE)[0]
        suppliers = [s.strip(" .") for s in _SUPPLIER_RE.findall(head)]

        code, line = read_reply_code(text)
        ocr_used = False
        if code is None and use_ocr and ocr_available():
            try:
                code, line = read_reply_code(_ocr_page(page))
                ocr_used = True
            except Exception:  # noqa: BLE001 -- OCR is best effort; no reply is "under review"
                code, line = None, None

        relative = str(path.relative_to(root))
        title = (description.group(1).strip() if description else path.stem).strip()
        number = (revision.group(1) if revision else "00").upper().lstrip("R")
        return ScannedForm(
            path=path,
            relative=relative,
            reference=ref,
            revision=f"R{number.zfill(2)}" if number.isdigit() else f"R{number}",
            title=title,
            system_code=_system_code(relative, title),
            # The last "M/s." on the form is the manufacturer, the first the supplier.
            supplier=suppliers[-1] if suppliers else None,
            submitted=date.group(1) if date else None,
            reply_code=code,
            reply_text=line,
            ocr_used=ocr_used,
            in_approval_folder=bool(APPROVAL_FOLDER_RE.search(str(Path(relative).parent))),
            modified=datetime.fromtimestamp(path.stat().st_mtime),
        )


def scan_folder(root: Path, use_ocr: bool = True) -> tuple[list[ScannedForm], list[str]]:
    """Every material submittal in the folder, one per reference and
    revision: the same submittal is filed where it was prepared and again
    under the approval folder, and the copy that came back is the one that
    carries the reply."""
    warnings: list[str] = []
    pdfs = []
    for n, path in enumerate(sorted(root.rglob("*.pdf"))):
        if n >= MAX_PDFS:
            warnings.append(f"Stopped after {MAX_PDFS} PDFs; the folder holds more.")
            break
        pdfs.append(path)
    if use_ocr and not ocr_available():
        warnings.append(
            "OCR is not available, so a consultant's stamp cannot be read; submittals show as under review."
        )

    found: dict[tuple[str, str], ScannedForm] = {}
    for path in pdfs:
        form = read_form(path, root, use_ocr=use_ocr)
        if form is None:
            continue
        key = (form.reference.upper(), form.revision)
        current = found.get(key)
        if current is None or _better(form, current):
            found[key] = form
    return sorted(found.values(), key=lambda f: (f.reference, f.revision)), warnings


def _better(candidate: ScannedForm, current: ScannedForm) -> bool:
    """The copy to keep: the one with a reply, then the one filed under the
    approval folder, then the newer file."""
    if bool(candidate.reply_code) != bool(current.reply_code):
        return bool(candidate.reply_code)
    if candidate.in_approval_folder != current.in_approval_folder:
        return candidate.in_approval_folder
    return candidate.modified > current.modified
