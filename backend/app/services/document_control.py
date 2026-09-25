"""Content-based document control. File/folder names never establish approval."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import os
import re
import io
from threading import RLock
import pymupdf
from app.services.submittal_scanner import ocr_available

_SCAN_LOCK = RLock()

# Windows refuses a path of 260 characters or more through the ordinary file
# APIs, and Path.is_file() answers False for one rather than raising -- so a
# deeply nested archive folder silently loses files instead of reporting
# them. On EP-30784 that was 54 of 287 PDFs, every one of them a shop drawing
# or a consultant reply, i.e. exactly the documents this scan exists to find.
# The \\?\ prefix lifts the limit; it needs a fully-qualified backslash path,
# and UNC paths take a different prefix, so those are left alone.
_LONG_PATH_PREFIX = "\\\\?\\"


def _os_path(path: Path) -> str:
    text = os.fspath(path)
    if os.name != "nt" or len(text) < 250 or text.startswith("\\\\"):
        return text
    return _LONG_PATH_PREFIX + os.path.abspath(text)


def _open_pdf(path: Path):
    r"""Open a PDF, reading it through the long-path API where the ordinary
    one cannot reach it. MuPDF does its own file opening and does not honour
    the \\?\ prefix, so such a file is read here and handed over as bytes."""
    name = _os_path(path)
    if not name.startswith(_LONG_PATH_PREFIX):
        return pymupdf.open(path)
    with open(name, "rb") as handle:
        return pymupdf.open(stream=handle.read(), filetype="pdf")

def _ocr_page(page):
    import pytesseract
    from PIL import Image

    # Tesseract is not on PATH on the engineers' machines; where it is runs
    # in the settings. Set here rather than relied on: every other reader
    # sets it at import, so whether this one could OCR came down to which
    # module happened to be imported first -- and when none had been, every
    # stamp went unread and every document came back "under review".
    from app.core.config import get_settings

    configured = get_settings().tesseract_cmd
    if configured:
        pytesseract.pytesseract.tesseract_cmd = configured
    dpi = min(200, 2600 * 72 / max(page.rect.width, page.rect.height))
    pixels = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72))
    image = Image.open(io.BytesIO(pixels.tobytes("png")))
    return pytesseract.image_to_string(image, timeout=25)


# The codes a controlled document's reference carries. Contractors number
# them their own way: MAS and MAR are both a material submittal (material
# approval request), SDW, DWG and SD a shop drawing, SAR a sample. MS is a
# method statement and is not one of these.
REF = re.compile(r"\b[A-Z0-9]+(?:-[A-Z0-9]+)*-(MAS|MAR|SDW|DWG|SD|SAR)-[A-Z0-9-]+", re.I)
# The trailing guard rejects a numbered list item. A CAD title block keeps
# labels and values in separate text runs, so the line after the "REV" label
# is whatever the export put next -- on every EP-30784 shop drawing that is
# the general notes, whose "1.)" was read as revision 1.
REV = re.compile(r"\b(?:MAS\s+|MAR\s+|SDW\s+|DWG\s+|SD\s+|SAR\s+)?REV(?:ISION)?\.?\s*[:.-]?\s*\n?\s*R?\s*(\d{1,3})\b(?!\s*\.?\))", re.I)

# Shop drawings are filed one folder per submission (.../1.FAVE/R1/05. Ground
# Floor/...). The sheet's own title block carries the *drawing's* revision,
# which contractors routinely leave at 00 across resubmissions -- on EP-30784
# both the R0 and the R1 submission of every FAVE drawing say 00 -- so the
# folder is the only thing separating one submission from the next. Used for
# the revision only: a folder still never establishes approval.
FOLDER_REV = re.compile(r"[\\/]R\.?\s?0*(\d{1,2})(?=[\\/]|$)", re.I)


# Where a drawing we were *given* lives: the consultant's enquiry pack,
# the tender set, the issued-for-construction drawings the contractor
# hands over. A shop drawing is one we produced and submitted, and a
# drawing sheet found in one of these folders is neither -- it is the
# drawing we are designing against.
GIVEN_TO_US = ("enquiry", "enquiries", "tender", "ifc", "issued for construction")


def is_shop_drawing(row) -> bool:
    """Whether a drawing record is one of ours to log.

    The reader recognises a drawing sheet by its title block, and the
    consultant's drawings have one too -- so without this the Drawings
    Log fills with the enquiry pack and reports nineteen shop drawings
    on a project that has produced none.

    Matched on whole folder names rather than anywhere in the path:
    "IFC" is a folder, and a project filed under "Pacific" is not an
    issued-for-construction set.
    """
    if getattr(row, "category", None) != "drawings":
        return True
    path = str(getattr(row, "path", "") or "").replace("\\", "/")
    folders = [part.strip().casefold() for part in path.split("/")[:-1]]
    return not any(word in folder for folder in folders for word in GIVEN_TO_US)


def folder_revision(path: str) -> str | None:
    found = FOLDER_REV.findall(path)
    return f"R{int(found[-1])}" if found else None
DRAW_REF = re.compile(r"(?:DRAWING\s*(?:NO\.?|NUMBER)|DWG\s*NO\.?)\s*:?\s*\n?\s*([A-Z0-9][A-Z0-9 /-]{3,70})", re.I)
TITLE = re.compile(r"(?:DRAWING\s*TITLE|TITLE)\s*:?\s*\n?\s*([^\n]+)", re.I)
SYSTEMS = [
    ("FRC", r"fire[ -]*(?:rated|resistant)\s*cable"),
    ("ELS", r"emergency\s*light|monitored\s*self[ -]*contained|central\s*battery"),
    ("PAVA", r"public\s*address|\bpa\s*/?\s*va\b|\bpava\b|voice\s*alarm|background\s*music|\bbgm\b"),
    ("FAS", r"fire\s*alarm|voice\s*evacuation|fire\s*telephone"),
]

# A contractor's answer to the consultant's comments. It quotes the submittal
# it answers ("Ref No : BBY006-GME-MAS-EL-LI-0001 - R.00"), which used to be
# enough to register it as that submittal -- so the reply sheet displaced the
# Materials Submittal Form it belongs to, and the same reference appeared
# twice. It is evidence attached to a submission, never a submission itself.
REPLY_SHEET = re.compile(
    r"reply\s+to\s+(?:the\s+)?(?:MS\s+|the\s+)?consultant(?:'s|s)?\s*(?:comments?|remarks?)"
    r"|consultant\s+comments?\s*(?:\n|\r|\s)*(?:.{0,40}\s)?reply"
    r"|repl(?:y|ies)\s+to\s+(?:QA\s*/?\s*QC|comments)",
    re.I,
)

# The submission form itself: the controlled form these registers are built
# from. Its own labels, not a document that merely mentions one.
SUBMISSION_FORM = re.compile(
    r"materials?\s+submittal\s+form|MAS\s+Reference\s+No|MAS\s+Rev"
    r"|sample\s+approval\s+(?:request\s+)?form|SAR\s+Reference\s+No"
    r"|shop\s*drawing\s+(?:submittal\s+)?form|SDW\s+Reference\s+No|SDW\s+Rev|SDW\.?\s+Ref\.?\s+No",
    re.I,
)

@dataclass(frozen=True)
class ControlledDocument:
    system_code: str | None
    name: str
    path: str
    modified: datetime
    reference: str
    revision: str
    status: str
    floor: str | None = None
    reply_text: str | None = None
    page: int = 1
    source: str = "document"
    category: str = "submittals"
    group_reference: str | None = None
    # The revisions this one replaced, newest first: a drawing is one row
    # at the revision that stands, and these are the records that came
    # before it, kept whole so the log can show each revision's own status
    # and open its own file. Never stored -- the collapse runs when the
    # records are read, not when they are written.
    superseded: tuple = ()


# The options a shop drawing submittal form offers, and what each means.
# Longest first: "approved as noted" is not an approval.
# Written as they read once the punctuation is out of the way: the forms
# spell it "Re- Submit", "Re-Submit" and "Resubmit" between them.
_OPTIONS: tuple[tuple[str, str], ...] = (
    ("approved as noted", "ANN"),
    ("as noted", "ANN"),
    ("re submit", "rejected"),
    ("resubmit", "rejected"),
    ("not approved", "rejected"),
    ("rejected", "rejected"),
    ("approved", "approved"),
)


def _is_mark(shape) -> bool:
    """Whether a drawn shape is a ticked box rather than part of the form.

    The form draws a box per option and fills the one chosen. An unchosen
    box is filled white; a chosen one is filled with a colour. Sized and
    squared so that a rule, a table border or the sliver a glyph leaves
    behind is not read as a decision.
    """
    fill = shape.get("fill")
    if not fill or len(fill) < 3:
        return False
    rect = shape["rect"]
    if not (5 <= rect.width <= 20 and 5 <= rect.height <= 20):
        return False
    if not (0.5 <= rect.width / max(rect.height, 0.01) <= 2):
        return False
    red, green, blue = fill[:3]
    if min(red, green, blue) > 0.85:      # white: the boxes not chosen
        return False
    return max(red, green, blue) > 0.15   # near-black is the printing, not a mark


# How boxes are read: `_OPTIONS`, `_is_mark` and `boxed_decision`. What a
# page's boxes said is cached under this (app.services.page_cache) -- change
# any of the three, bump it, or the old readings are reused.
BOX_VERSION = "box-1"

# Each option's words, for the screens below: an option is matched in a
# label as a phrase, so each of its words is a run of letters in that label.
_OPTION_PIECES = tuple(tuple(words.split()) for words, _status in _OPTIONS)


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _may_hold_an_option(text: str) -> bool:
    """Whether `text` could contain an option. A box's label is a run of
    the characters of one line of the page, so every word of the option it
    names is a run of letters in the text around it: where no option has
    all its words in `text` (punctuation and spacing ignored), no label
    drawn from it can name one. Never rules out a page or a box the full
    reading would have answered -- it only saves asking."""
    compact = _compact(text)
    return any(all(piece in compact for piece in pieces) for pieces in _OPTION_PIECES)


def boxed_decision(page, text: str | None = None) -> tuple[str, str] | None:
    """The consultant's decision where it is a filled box beside the
    option rather than a word or a tick, as (status, evidence).

    The engineering consultant's approval block lists every option --
    Approved (A), Approved as Noted (B), Re-Submit (C) -- and marks one by
    filling its box. Read as text that is a list of choices and settles
    nothing, which is right: `read_decision` refuses to guess from it. The
    decision is in the drawing, so it is read from there.

    None when no box is filled, or when more than one is against a
    different answer -- two marks say no more than none.

    Asking the page for the text in a rectangle re-reads the whole page each
    time -- about 0.1 s on a CAD sheet -- and a CAD sheet has dozens of small
    coloured squares: 58 on one EP-30784 floor plan, 5.5 s of its 6.5. So
    it is asked only where it could matter. `text`, the page's text when the
    caller has it, rules out a page that names no option at all; and a box
    whose surrounding words (every word touching its label's rectangle,
    taken whole) cannot spell an option is skipped. Both screens can only
    pass over a box whose label would have matched nothing
    (`_may_hold_an_option`); every box that gets past them is read exactly
    as before.
    """
    if text is not None and not _may_hold_an_option(text):
        return None
    found: dict[str, str] = {}
    page_words = None
    for shape in page.get_drawings():
        if not _is_mark(shape):
            continue
        rect = shape["rect"]
        clip = pymupdf.Rect(rect.x1, rect.y0 - 3, rect.x1 + 150, rect.y1 + 3)
        if page_words is None:
            page_words = page.get_text("words")
        # Every word touching the rectangle, whole, with a margin: a superset
        # of the characters the clipped reading below can return.
        near = " ".join(w[4] for w in page_words
                        if w[0] <= clip.x1 + 2 and w[2] >= clip.x0 - 2 and w[1] <= clip.y1 + 2 and w[3] >= clip.y0 - 2)
        if not _may_hold_an_option(near):
            continue
        beside = page.get_text("text", clip=clip)
        # Only as far as this option's own letter: the next option's box
        # sits a little further along the same line.
        label = " ".join(beside.split())
        cut = re.search(r"\([ABCD]\)", label)
        if cut:
            label = label[: cut.end()]
        # "Re- Submit (C)" and "Re-Submit (C)" are the same answer.
        plain = " ".join(re.sub(r"[^a-z0-9]+", " ", label.lower()).split())
        for words, status in _OPTIONS:
            if words in plain:
                found[status] = label.strip()
                break
    if len(found) != 1:
        return None
    status, evidence = next(iter(found.items()))
    return status, evidence


def read_decision(text: str) -> tuple[str, str | None]:
    """Only explicit marked decisions, status fields or stamps, never option lists."""
    decisions = []
    text = re.sub(r"[\u2610]([^\n\u2612\u2713\u2714]*)", "", text)
    for line in text.splitlines():
        line = line.strip()
        if not line or ("\u2610" in line and not any(c in line for c in "\u2612\u2713\u2714")):
            continue
        # OCR can lose boxes. Multiple choices on one line still answer nothing.
        if len(set(re.findall(r"\(([ABCD])\)", line, re.I))) > 1:
            continue
        stamp = re.search(r"\([ABCD]\)\s*(?:approved|revise|reject|re[ -]?submit).*", line, re.I)
        if stamp: line = stamp.group()
        marked = bool(re.search(r"[\u2612\u2713\u2714]|\[\s*[xX]\s*\]|(?:consultant(?:'s)?\s+)?(?:review\s+)?status\s*:|decision\s*:|^\([ABCD]\)", line, re.I))
        if not marked:
            continue
        status = None
        if re.search(r"not\s+approved|reject|revise|re[ -]?submit", line, re.I): status = "rejected"
        elif re.search(r"approved\s*(?:as\s*noted|with\s*comments)|\bANN\b", line, re.I): status = "ANN"
        elif re.search(r"\bapproved\b", line, re.I): status = "approved"
        if status: decisions.append((status, line))
    if len({s for s, _ in decisions}) == 1:
        return decisions[0]
    return "UR", "Conflicting review decisions; verification required." if decisions else None


def title_block(text: str, reference: str) -> tuple[str | None, str | None]:
    """The drawing title and layout from a CAD title block.

    The block is a grid of labels and a grid of values, and the PDF text layer
    carries each grid as its own run -- so the line after the "DRAWING TITLE"
    label is the next *label* ("SCALE"), which is what the register was
    showing as the title of almost every shop drawing. The values sit together
    after the drawing reference instead:

        BBY006-...-010002 | GROUND FLOOR PLAN | FIRE ALARM LAYOUT | 06.08.2026

    so they are read positionally from the reference. This is what recovers
    the floor, which a label-based read never found on a single drawing.
    """
    index = text.find(reference)
    if index < 0: return None, None
    values = [line.strip() for line in text[index + len(reference):].splitlines() if line.strip()]
    # A label ("SDW Date:"), a date or a sheet size is not a drawing title.
    def usable(value: str | None) -> str | None:
        if not value or value.endswith(":") or not re.search(r"[A-Za-z]{3}", value): return None
        return None if re.fullmatch(r"[\d.\-/ ]+|A\d|\d+:\d+", value) else value
    return usable(values[0] if values else None), usable(values[1] if len(values) > 1 else None)


def floor_name(title: str, *, whole: bool = True) -> str | None:
    # A sheet covering a run of floors names the run: keep it whole, so
    # the floors between the ends are not left looking undrawn.
    for pattern in [r"TYPICAL\s+.*?FLOOR", r"BASEMENT[ -]*\d{1,3}(?:\s*(?:,|&|AND)\s*[BPL]?\s*\d{1,3})*", r"PODIUM[ -]*\d{1,3}(?:\s*(?:,|&|AND)\s*[BPL]?\s*\d{1,3})*", r"(?:\d+(?:ST|ND|RD|TH)\s+|GROUND\s+|.*?ROOF\s+)FLOOR", r"\b[BPL]?\d{1,3}\s*(?:TO|&|AND)\s*[BPL]?\s*\d{1,3}\b", r"UNDER\s*GROUND", r"\b(?:B\d+|L\d+|GF|RF)\b"]:
        found = re.search(pattern, title, re.I)
        # A title block exports its runs separately, so a floor can come
        # back with the gaps still in it ("LIFT MACHINE ROOM  FLOOR PLAN").
        if found: return " ".join(found.group().split())
    # Last resort: the whole line, where it is a floor and nothing else
    # ("GROUND FLOOR PLAN"). Never for a file name -- a hundred characters
    # of drawing number and description is not a floor, and printed as one
    # it filled the column.
    return " ".join(title.split()) if whole and "FLOOR" in title.upper() else None


def parse_page(text: str, path: str, modified: datetime, page: int) -> list[ControlledDocument]:
    # A drawing schedule defines required rows, not an approved drawing itself.
    if re.search(r"DWG\s*NO", text, re.I) and "FIRE ALARM" in text.upper() and "EML SUBMISSION" in text.upper():
        schedule = []
        for match in re.finditer(r"(?m)^\s*(FA\s*\d{3,})\s*\n\s*([^\n]+)", text, re.I):
            ref, title = match.groups()
            title = title.strip()
            codes = ["ELS"] if "EMERGENCY SCHEMATIC" in title.upper() else ["FAS"] if "FIRE ALARM SCHEMATIC" in title.upper() else ["FAS", "ELS"]
            for code in codes:
                schedule.append(ControlledDocument(code, title, path, modified, re.sub(r"\s+", " ", ref.strip()), "R0", "UR", floor_name(title), page=page, source="drawing schedule", category="drawings"))
        return schedule
    match = REF.search(text)
    drawing_match = DRAW_REF.search(text)
    # A reply sheet quotes the reference it answers. Registering it would
    # displace the submission form carrying that reference, so it is only
    # ever read for the decision it may carry (source="reply"), and
    # _scan_document_control attaches that to the submission itself.
    if REPLY_SHEET.search(text) and not SUBMISSION_FORM.search(text):
        if not match: return []
        decision, evidence = read_decision(text)
        reference = re.sub(r"-R\d+$", "", match.group().rstrip("-."), flags=re.I)
        revision_match = REV.search(text) or re.search(r"\bR\.?\s*(\d{1,3})\b", text)
        return [ControlledDocument(
            None, "Reply to consultant comments", path, modified, reference,
            f"R{int(revision_match.group(1))}" if revision_match else "R0",
            decision, None, evidence, page, source="reply", category="reply")]
    if match:
        reference = match.group().rstrip("-.")
        category = {"MAS": "submittals", "MAR": "submittals", "SAR": "samples",
                    "SDW": "drawings", "DWG": "drawings", "SD": "drawings"}[match.group(1).upper()]
        # A catalogue quoting a submittal number is not a submission form.
        required = r"material[s]?\s+submittal|MAS\s+Reference" if category == "submittals" else r"sample\s+approval|SAR\s+Reference" if category == "samples" else r"drawing\s*(?:title|no|number|submittal)|shop\s*drawing"
        # A method statement transmittal names its material submittal's
        # number; it is not one (EP-29495 files both under -MS- and -MAR-).
        if category == "submittals" and re.search(r"method\s+statement|risk\s+assessment", text, re.I): return []
        if not re.search(required, text, re.I) and not re.search(r"consultant.*(?:reply|comment|status)|review\s*status", text, re.I): return []
    elif drawing_match and re.search(r"FIRE\s*ALARM|EMERGENCY\s*LIGHT|VOICE\s*EVACUATION", text, re.I):
        reference, category = drawing_match.group(1).strip(), "drawings"
        if not TITLE.search(text): return []
    else:
        return []
    revision_match = REV.search(text)
    suffix = re.search(r"-R(\d+)$", reference, re.I)
    # The drawing's own revision first; the folder only where it prints
    # none. A resubmission is filed with the comments it answers -- a
    # floor's R1 folder holds the R1 drawing *and* the stamped R0 sheet --
    # so taking the revision off the folder made that sheet an R1 and its
    # "revise and resubmit" the verdict on a revision no one had seen.
    # Settled by the platform owner on 2026-09-24, reversing the rule that
    # had the folder win.
    if revision_match:
        revision = f"R{int(revision_match.group(1))}"
    elif suffix:
        revision = f"R{int(suffix.group(1))}"
    elif category == "drawings" and folder_revision(path):
        revision = folder_revision(path)
    else:
        revision = "R0"
    reference = re.sub(r"-R\d+$", "", reference, flags=re.I)
    layout = None
    if category == "drawings" and not SUBMISSION_FORM.search(text):
        # A drawing sheet: read the title block by position (see title_block).
        block_title, layout = title_block(text, reference)
    else:
        block_title = None
    title_match = re.search(r"(?:Material\s+Submittal\s+for|Sample\s+Approval\s+Request\s+for)\s+([^\n]+)", text, re.I) if category != "drawings" else TITLE.search(text)
    title = block_title or (title_match.group(1).strip() if title_match else reference)
    if category != "drawings" and title_match:
        following = text[title_match.end():].splitlines()
        for continuation in following[:4]:
            continuation = continuation.strip()
            if not continuation: continue
            if re.match(r"^(?:\d+|EL\b|M/S|MSF|MAIN|Discipline|Remarks|Pages|SPECS)", continuation, re.I): break
            title += " " + continuation
    if re.search(r"\bcables?\b", title, re.I): code_hint = "FRC"
    else: code_hint = None
    # The title block's layout line ("FIRE ALARM LAYOUT") names the system on
    # the sheet itself, which beats inferring it from the reference.
    named = f"{title} {layout}" if layout else title
    code = code_hint or next((code for code, pattern in SYSTEMS if re.search(pattern, named, re.I)), None)
    if code is None:
        code = "FAS" if re.search(r"-(?:FA|FAS|VE|FT)-", reference, re.I) else "ELS" if re.search(r"-(?:LI|ELM|EML|ELS|CBS)-", reference, re.I) else "FRC" if re.search(r"-FRC-", reference, re.I) else None
    if code is None and category == "drawings": return []
    if code is None: code = next((code for code, pattern in SYSTEMS if re.search(pattern, text, re.I)), None)
    decision, evidence = read_decision(text)
    # The floor off the sheet, and failing that off the file it came in.
    # A submission covering several floors is titled for the drawing and
    # names them only on the wrapper -- "Shop Drawing for Basement 4, 3, 2
    # Floor Plan" -- so without this it reached the log as a drawing of no
    # floor at all, and those basements looked undrawn.
    floor = floor_name(title) or floor_name(Path(path).stem, whole=False)
    return [ControlledDocument(code, title, path, modified, reference, revision, decision, floor, evidence, page, category=category)]


def normalize_floor(value: str) -> str:
    value = value.upper().strip()
    value = re.sub(r"BASEMENT[ -]*(\d+)", r"B\1", value)
    value = re.sub(r"PODIUM[ -]*(\d+)", r"P\1", value)
    value = re.sub(r"(\d+)(?:ST|ND|RD|TH)?\s+FLOOR", r"L\1", value)
    value = value.replace("GROUND FLOOR", "GF")
    value = re.sub(r"[ -]+", "", value)
    # B01 and B1 are the same basement, L01 and the 1st floor the same
    # floor: the drawings pad the number and the schedules do not, and
    # without this they never matched each other.
    return re.sub(r"\b([BPL])0+(\d)", r"\1\2", value)


def _boxed(page, text: str, sha256: str | None, index: int) -> tuple[str, str] | None:
    """`boxed_decision`, from the page cache when this file's content has
    been looked at under the same box rules (`BOX_VERSION`)."""
    if not _may_hold_an_option(text):
        return None          # nothing to look for, and nothing worth keeping
    from app.services import page_cache

    cached = page_cache.get_box(sha256, index, BOX_VERSION)
    if cached is not page_cache.MISSING:
        return cached
    found = boxed_decision(page, text)
    page_cache.put_box(sha256, index, BOX_VERSION, list(found) if found else None)
    return found


def _ocr_text(page, sha256: str | None, index: int) -> str:
    """The page's OCR text, from the page cache when this file's content
    has been OCRed before (app.services.page_cache)."""
    from app.services import page_cache

    cached = page_cache.get_ocr(sha256, index)
    if cached is not None:
        return cached
    text = _ocr_page(page)
    page_cache.put_ocr(sha256, index, text)
    return text


@lru_cache(maxsize=1024)
def _read_pdf(filename: str, stamp: int, size: int, use_ocr: bool,
              sha256: str | None = None) -> tuple[tuple[ControlledDocument, ...], tuple[str, ...]]:
    """The document-control records a PDF holds. `sha256`, the file's
    content hash when the caller knows it, lets OCR text be reused from an
    earlier reading of the same content."""
    records, warnings = [], []
    path = Path(filename)
    modified = datetime.fromtimestamp(stamp / 1e9, timezone.utc)
    try:
        with _open_pdf(path) as pdf:
            pending = None
            ocr_count = 0
            for index, page in enumerate(pdf):
                # A catalogue/specification is not a register. Inspect its cover,
                # but do not OCR or read every product page looking for approvals.
                if index > 0 and not records and pending is None:
                    break
                if index >= 12 and records and all(row.category != "drawings" for row in records):
                    warnings.append(f"{path.name}: only the first 12 pages were checked for submission replies.")
                    break
                text = page.get_text()
                found = parse_page(text, filename, modified, index + 1)
                # The approval block lists every option and fills the box
                # beside the one chosen. As text that is a list of choices
                # and settles nothing -- rightly, since a list is not an
                # answer -- so where nothing was decided the drawing is
                # asked, before any OCR is attempted.
                if found and all(row.status == "UR" for row in found):
                    boxed = _boxed(page, text, sha256, index)
                    if boxed is not None:
                        decision, evidence = boxed
                        found = [replace(row, status=decision, reply_text=evidence) for row in found]
                # OCR title blocks of scanned pages and image stamps on forms.
                # Also a page whose own text or ticked box already gave a
                # decision: the consultant's stamp, pasted on as an image, is
                # the verdict that stands, and it can say otherwise -- EP-30784's
                # emergency lighting sample (BBY006-GME-SAR-EL-LI-0001) has
                # "Approved as Noted (B)" ticked and a "(C) Revise & Resubmit"
                # stamp beside it. What OCR finds is cached by content
                # (`_ocr_text`), so a page is OCRed once, not on every re-read.
                candidate = bool(found) or pending is not None or (len(text.strip()) < 80 and bool(re.search(r"approval|submittal|drawing|[/\\]MS[/\\]", filename, re.I)))
                if use_ocr and candidate and (page.get_images() or len(text.strip()) < 80):
                    if ocr_count < 12:
                        try:
                            ocr_count += 1
                            ocr_text = _ocr_text(page, sha256, index)
                            ocr_found = parse_page(ocr_text, filename, modified, index + 1)
                            if not found: found = ocr_found
                            decision, evidence = read_decision(ocr_text)
                            if decision != "UR": found = [replace(row, status=decision, reply_text=evidence) for row in found]
                            text += "\n" + ocr_text
                        except Exception:
                            warnings.append(f"Could not OCR {path.name}, page {index + 1}.")
                    else:
                        warnings.append(f"OCR limit reached in {path.name}; some replies may need verification.")
                if found:
                    records.extend(found)
                    pending = len(records) - 1 if len(found) == 1 and found[0].source == "document" else None
                elif pending is not None and re.search(r"consultant.*(?:comment|reply|review)|review\s*status", text, re.I):
                    # An attached reply without a different reference belongs to the preceding form.
                    references = REF.findall(text)
                    decision, evidence = read_decision(text)
                    # A resubmission is filed with the comments it answers, so
                    # the reply attached to an R1 form is usually the
                    # consultant's word on R0. Where the comments name the
                    # revision they are on, they settle that one, not this.
                    from app.services.submittal_replies import commented_revision

                    said = commented_revision(text)
                    answers_this = said is None or said == _revision_number(records[pending].revision)
                    if not references and decision != "UR" and answers_this:
                        records[pending] = replace(records[pending], status=decision, reply_text=evidence, page=index + 1)
                else:
                    pending = None
    except OSError as exc:
        # A OneDrive file that is still online-only cannot be read at all.
        # It is not a corrupt document and saying so sends the reader to the
        # wrong problem: the fix is to make the folder available offline.
        if exc.errno == 22 or "cloud" in str(exc).lower():
            warnings.append(f"{path.name} is not downloaded from OneDrive; make the project folder available offline, then refresh.")
        else:
            warnings.append(f"Could not read {path.name}.")
    except Exception:
        warnings.append(f"Could not read {path.name}.")
    return tuple(records), tuple(dict.fromkeys(warnings))


def _merge(old: ControlledDocument, new: ControlledDocument) -> ControlledDocument:
    """One submission, read off several pages, is one register row.

    A shop-drawing submission is a form page followed by the sheet itself, and
    the two carry different halves of the same fact: the form has the
    reference and the consultant's stamp, the sheet has the title and the
    floor. Picking whichever page ranked higher discarded the other half --
    which is why every drawing that was submitted under a form showed the
    reference as its title and no floor at all.
    """
    best = new if (old.status == "UR" and new.status != "UR") else old
    if best.status == old.status and new.source == "document" and old.source != "document":
        best = new
    return replace(
        best,
        # A title that is just the reference is a fallback, not a title.
        name=next((r.name for r in (best, old, new) if r.name and r.name != r.reference), best.name),
        floor=best.floor or old.floor or new.floor,
        reply_text=best.reply_text or old.reply_text or new.reply_text,
        group_reference=best.group_reference or old.group_reference or new.group_reference,
    )


def _scan_document_control(root: Path, use_ocr: bool = True, progress=None) -> tuple[list[ControlledDocument], list[str]]:
    enabled = use_ocr and ocr_available()
    warnings = [] if enabled or not use_ocr else ["OCR is unavailable. Image-only documents or consultant stamps may need verification; no approval is assumed."]
    found = {}
    # os.path.isfile through _os_path, not Path.is_file(), so a path over the
    # Windows limit is still seen -- see _os_path.
    paths = sorted(
        path for path in root.rglob("*")
        if path.suffix.lower() == ".pdf" and os.path.isfile(_os_path(path))
    )
    for index, path in enumerate(paths):
        try:
            stat = os.stat(_os_path(path))
            records, notes = _read_pdf(str(path), stat.st_mtime_ns, stat.st_size, enabled)
            warnings.extend(notes)
            for row in records:
                row = replace(row, path=path.relative_to(root).as_posix())
                key = (row.category, row.system_code, row.reference.upper(), row.revision)
                old = found.get(key)
                found[key] = row if old is None else _merge(old, row)
        except OSError:
            warnings.append(f"Could not access {path.name}.")
        if progress:
            progress(list(found.values()), list(dict.fromkeys(warnings)), index + 1, len(paths))
    return combine(list(found.values())), list(dict.fromkeys(warnings))


def combine(records: list[ControlledDocument]) -> list[ControlledDocument]:
    """The register from the records read off every document -- one row per
    submission, replies folded in, drawings matched to their schedule --
    whether the records were just read or come from the index."""
    # Samples sent under a transmittal have no revision of their own: they
    # are numbered in the order they were sent (transmittals.number).
    from app.services import transmittals

    sent = transmittals.number([row for row in records if row.source == "transmittal"])
    records = [row for row in records if row.source != "transmittal"]
    found: dict = {}
    for row in records:
        key = (row.category, row.system_code, row.reference.upper(), row.revision)
        old = found.get(key)
        found[key] = row if old is None else _merge(old, row)
    # Replies are evidence, not entries: a reply carrying a consultant
    # decision settles the submission it answers, and is then dropped. One
    # that carries no decision (the contractor answering comments, which is
    # the usual case) leaves the submission exactly as it was -- a reply is
    # not itself an approval.
    rows = [row for row in found.values() if row.category != "reply"]
    for reply in (row for row in found.values() if row.category == "reply"):
        if reply.status == "UR": continue
        for i, row in enumerate(rows):
            if row.reference.upper() == reply.reference.upper() and row.revision == reply.revision and row.status == "UR":
                rows[i] = replace(row, status=reply.status, reply_text=reply.reply_text)

    # Match an issued drawing to a unique scheduled floor in the same system.
    # Keep its actual drawing reference, while retaining a stable register row.
    schedules = [r for r in rows if r.source == "drawing schedule"]
    for i, row in enumerate(rows):
        if row.category != "drawings" or row.source != "document" or not row.floor: continue
        candidates = [s for s in schedules if s.system_code == row.system_code and s.floor and normalize_floor(s.floor) == normalize_floor(row.floor)]
        if len(candidates) == 1:
            rows[i] = replace(row, group_reference=candidates[0].reference)
    # An older revision still "under review" when a later one exists was
    # superseded, not left with the consultant: the later revision is the
    # one that stands, and the register says so instead of showing an open
    # review that will never close.
    latest: dict[tuple, int] = {}
    for row in rows:
        key = (row.category, row.system_code, row.reference.upper())
        latest[key] = max(latest.get(key, -1), _revision_number(row.revision))
    for i, row in enumerate(rows):
        key = (row.category, row.system_code, row.reference.upper())
        if row.status == "UR" and _revision_number(row.revision) < latest[key]:
            rows[i] = replace(row, status="SUPERSEDED")

    # A floor has one shop drawing, at the revision that stands. Every
    # revision of it was a row of its own, so a floor whose R0 came back
    # for revision was listed twice -- once rejected and once under review
    # -- and the log read as two drawings for one floor. The earlier
    # revisions go onto the row that replaced them.
    # The floor each drawing number is of, from whichever of its sheets
    # named one: a submission's form page carries the number and no title
    # block, and the sheet behind it carries the floor.
    floor_of_reference: dict[tuple, str] = {}
    for row in rows:
        if row.category != "drawings":
            continue
        floor = normalize_floor(row.floor or "")
        if floor:
            floor_of_reference.setdefault((row.system_code, row.reference.upper()), floor)

    def drawing_key(row) -> tuple:
        """What makes two records the same drawing: the floor it is of.

        Not the drawing number. A sheet re-issued for the next revision
        writes its own title block, and where the first named the floor
        ("...-ZZZ-L22-010009") the second can carry the placeholder
        ("...-ZZZ-ZZZ-010009") -- the same drawing of the same floor under
        two numbers, which the log then showed as two drawings of L22, one
        answered and one under review.

        A floor has one shop drawing per system; a sheet whose floor was
        never read has nothing to be matched on and keeps its own number.
        """
        floor = (normalize_floor(row.floor or "")
                 or floor_of_reference.get((row.system_code, row.reference.upper()), ""))
        return (row.system_code, floor) if floor else (row.system_code, row.reference.upper())

    standing: dict[tuple, ControlledDocument] = {}
    history: dict[tuple, list[ControlledDocument]] = {}
    for row in rows:
        if row.category != "drawings":
            continue
        key = drawing_key(row)
        history.setdefault(key, []).append(row)
        held = standing.get(key)
        if held is None or _revision_number(row.revision) > _revision_number(held.revision):
            standing[key] = row
    if standing:
        collapsed = []
        for row in rows:
            if row.category != "drawings":
                collapsed.append(row)
                continue
            key = drawing_key(row)
            if standing.get(key) is not row:
                continue
            # Only the revisions the consultant actually answered. One that
            # came and went without a decision on it is the same drawing
            # filed again, not a submission of its own, and listing it said
            # the floor had been submitted twice when it had been submitted
            # once and re-issued.
            earlier = sorted((r for r in history[key]
                              if r is not row and r.status not in ("UR", "SUPERSEDED")),
                             key=lambda r: _revision_number(r.revision), reverse=True)
            collapsed.append(replace(row, superseded=tuple(earlier)))
        rows = collapsed
    rows.extend(sent)
    return sorted(rows, key=lambda row: (row.category, row.system_code or "", row.reference, row.revision))


def _revision_number(revision: str | None) -> int:
    digits = re.sub(r"\D", "", revision or "")
    return int(digits) if digits else 0


def scan_document_control(root: Path, use_ocr: bool = True, progress=None) -> tuple[list[ControlledDocument], list[str]]:
    # Avoid duplicate OCR work from concurrent refresh requests.
    with _SCAN_LOCK:
        return _scan_document_control(root, use_ocr, progress)
