"""Content-based document control. File/folder names never establish approval."""
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import os
import re
import io
import subprocess
import tempfile
import threading
import time
from threading import RLock
import pymupdf
from app.services.submittal_scanner import ocr_available

_SCAN_LOCK = RLock()


# --- where a document's reading spends its time ----------------------------------------
#
# Document Processing V2, phase 0: the reading is measured before it is
# changed. A reader (document_sync.extract) starts a clock for the document
# it is about to read; the steps below add to it as they run -- opening the
# PDF, extracting a page's text, parsing it, looking for filled boxes,
# rendering a page for OCR, Tesseract itself -- and count what they did
# (pages scanned, OCR pages attempted and used, cache hits, PDF opens). The
# clock is thread-local, so readers in one process never mix documents, and
# a step run with no clock costs one attribute lookup.

_TELEMETRY = threading.local()


class StageClock:
    """Milliseconds per stage and counts per event for one document."""

    def __init__(self):
        self.ms: dict[str, float] = defaultdict(float)
        self.counts: dict[str, int] = defaultdict(int)

    @contextmanager
    def stage(self, name: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.ms[name] += (time.perf_counter() - started) * 1000

    def count(self, name: str, n: int = 1) -> None:
        self.counts[name] += n

    def set(self, name: str, value: int) -> None:
        self.counts[name] = value

    def snapshot(self) -> dict:
        return {"stages_ms": {name: round(value, 1) for name, value in self.ms.items()}, "counts": dict(self.counts)}


def begin_stage_clock() -> StageClock:
    clock = StageClock()
    _TELEMETRY.clock = clock
    return clock


def stage_clock() -> StageClock | None:
    return getattr(_TELEMETRY, "clock", None)


@contextmanager
def timed(name: str):
    """Add the time spent in the block to the current document's clock, if any."""
    clock = stage_clock()
    if clock is None:
        yield
        return
    with clock.stage(name):
        yield


def counted(name: str, n: int = 1) -> None:
    clock = stage_clock()
    if clock is not None:
        clock.count(name, n)

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
    counted("pdf_opens")
    with timed("pdf_open"):
        if not name.startswith(_LONG_PATH_PREFIX):
            return pymupdf.open(path)
        with open(name, "rb") as handle:
            return pymupdf.open(stream=handle.read(), filetype="pdf")

def _tesseract():
    import pytesseract

    # Tesseract is not on PATH on the engineers' machines; where it is runs
    # in the settings. Set here rather than relied on: every other reader
    # sets it at import, so whether this one could OCR came down to which
    # module happened to be imported first -- and when none had been, every
    # stamp went unread and every document came back "under review".
    from app.core.config import get_settings

    configured = get_settings().tesseract_cmd
    if configured:
        pytesseract.pytesseract.tesseract_cmd = configured
    return pytesseract


def _render_scale(page) -> float:
    """The render scale OCR has always used: 200 dpi, or less on a sheet
    whose long side would pass 2600 pixels."""
    dpi = min(200, 2600 * 72 / max(page.rect.width, page.rect.height))
    return dpi / 72


def _render_page(page):
    """The page as OCR sees it. Rendered once per page and reused between
    the OCR tiers (`renders`, kept by the caller for the current page only)."""
    from PIL import Image

    with timed("ocr_render"):
        scale = _render_scale(page)
        pixels = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
        return Image.open(io.BytesIO(pixels.tobytes("png")))


# Tesseract is given this long for one image, and this much more for each
# further image of one run, before the page is noted as not OCRed.
OCR_TIMEOUT_S = 25
OCR_TIMEOUT_PER_IMAGE_S = 5


def _ocr_images(page, images: list) -> list[str]:
    """The OCR text of each of `images` (renders or crops of `page`), in
    one Tesseract run -- the one OCR seam: every OCR of the reader comes
    through here, and the tests replace it. Every Tesseract call is a
    process, and on an engineer's PC a process can cost seconds to start
    (antivirus, a loaded machine: on this one, during the phase-2
    benchmarks, ~12 s each, whether the image was a stamp crop or an A1
    sheet), so a page's crops are read in one run, not one run each."""
    pytesseract = _tesseract()
    counted("ocr_runs")
    with timed("ocr_engine"):
        if len(images) == 1:
            return [pytesseract.image_to_string(images[0], timeout=OCR_TIMEOUT_S)]
        return _ocr_batch(pytesseract.pytesseract.tesseract_cmd, images)


def _ocr_batch(command: str, images: list) -> list[str]:
    """One Tesseract run over several images: it takes a text file listing
    image files and reads them as the pages of one document, each with its
    own layout analysis, the texts separated by form feeds on stdout."""
    with tempfile.TemporaryDirectory(prefix="ep-ocr-") as folder:
        names = []
        for index, image in enumerate(images):
            name = os.path.join(folder, f"{index}.png")
            image.save(name, format="PNG")
            names.append(name)
        listing = os.path.join(folder, "images.txt")
        with open(listing, "w", encoding="utf-8") as handle:
            handle.write("\n".join(names) + "\n")
        try:
            run = subprocess.run([command, listing, "stdout"], capture_output=True,
                                 timeout=OCR_TIMEOUT_S + OCR_TIMEOUT_PER_IMAGE_S * len(images),
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise RuntimeError("Tesseract process timeout") from None
    if run.returncode:
        raise RuntimeError("Tesseract failed: " + run.stderr.decode("utf-8", "replace").strip()[-200:])
    parts = run.stdout.decode("utf-8", "replace").replace("\r\n", "\n").split("\f")
    if len(parts) < len(images):
        raise RuntimeError(f"Tesseract returned {len(parts)} texts for {len(images)} images")
    return parts[:len(images)]


def _ocr_page(page, image=None):
    """The whole page's OCR text (`image`: its render, when the caller has it)."""
    if image is None:
        image = _render_page(page)
    return _ocr_images(page, [image])[0]


# --- OCR in tiers --------------------------------------------------------------------------
#
# Document Processing V2, phase 2. On EP-30784, OCR was 80 % of the reading
# time, and most of it went on pages that did not need it: a CAD shop
# drawing carries its text in the text layer and a small logo image on
# every sheet, and "the page has an image" sent the whole sheet -- 2600
# pixels wide -- through Tesseract to look for a stamp. What a page's text
# layer cannot hold is only what is inside its images, so on a page with
# text the images are OCRed, not the page (tier 1); the whole page is
# still OCRed where it always mattered: a scanned page (little or no
# text), a page the images cover (a scan with a thin text layer), and a
# page whose images read like a stamp without settling the decision, where
# the full page is the safe second look (tier 2, the old reader unchanged).

OCR_REGIONS_VARIANT = "regions-2"     # page_cache key for what tier 1 read (bumped: composite reading)
# An image narrower or shorter than this, in points, cannot hold a stamp's
# words legibly; a logo or a rule is not OCRed.
MIN_REGION_PT = 14.0
# Images covering this share of the page: the page is a scan with a text
# layer laid over it, and the whole page is read.
FULL_PAGE_IMAGE_SHARE = 0.5
# Images closer than this, in points, are one region: a stamp is often
# several image blocks side by side.
REGION_GAP_PT = 8.0
# More image clusters than this on one page -- a sheet of hatch patterns
# and symbol bitmaps -- and the regions cost more Tesseract runs than the
# page itself: the whole page is read, as before.
MAX_REGIONS_PER_PAGE = 8
# The regions' pixels, laid out for one Tesseract run, may not exceed this
# share of the page render's pixels, or the page itself is the cheaper read.
COMPOSITE_SHARE_LIMIT = 0.6
def _image_regions(page) -> list:
    """The rectangles of the images on the page worth OCRing."""
    regions = []
    try:
        infos = page.get_image_info()
    except Exception:  # noqa: BLE001 -- a page MuPDF cannot list: no regions, the full page decides
        return []
    page_rect = page.rect
    for info in infos:
        rect = pymupdf.Rect(info["bbox"]) & page_rect
        if rect.is_empty or rect.width < MIN_REGION_PT or rect.height < MIN_REGION_PT:
            continue
        regions.append(rect)
    return regions


def _image_share(page, regions) -> float:
    area = page.rect.width * page.rect.height
    return sum(r.width * r.height for r in regions) / area if area else 0.0


def _clustered(regions: list, gap: float = REGION_GAP_PT) -> list:
    """The regions with the ones touching or within `gap` of each other
    merged: a stamp pasted as several image blocks reads as one."""
    merged = [pymupdf.Rect(r) for r in regions]
    changed = True
    while changed:
        changed = False
        out: list = []
        for rect in merged:
            grown = pymupdf.Rect(rect.x0 - gap, rect.y0 - gap, rect.x1 + gap, rect.y1 + gap)
            hit = next((i for i, other in enumerate(out) if grown.intersects(other)), None)
            if hit is None:
                out.append(pymupdf.Rect(rect))
            else:
                out[hit] |= rect
                changed = True
        merged = out
    return merged


def _prefer_full_page(page, regions) -> bool:
    """Whether the whole page is the right read before any region is tried:
    the images cover it (a scan with a text layer laid over it), or there
    are so many of them that the page itself is the cheaper read."""
    return _image_share(page, regions) >= FULL_PAGE_IMAGE_SHARE or len(_clustered(regions)) > MAX_REGIONS_PER_PAGE


def _ocr_regions(page, regions, image):
    """OCR of the images on the page, each cluster cropped from the page's
    render and read as an image of its own, all of them in one Tesseract
    run (`_ocr_images`, the one OCR seam). A small crop is enlarged first: at the page's own render scale a stamp
    on an A1 sheet is a few hundred pixels wide and Tesseract misses its
    heading; enlarged, it reads "(C) Revise & Resubmit" where the whole-
    page read found nothing (EP-30784's FAVE R1 sheets). None when the
    crops come to more than the page itself would: the caller then reads
    the whole page. One canvas for all the crops was tried and dropped --
    Tesseract's layout analysis lost the heading of a stacked block."""
    scale = image.width / page.rect.width if page.rect.width else 1.0
    crops = []
    for rect in _clustered(regions):
        box = (max(0, int(rect.x0 * scale) - 4), max(0, int(rect.y0 * scale) - 4),
               min(image.width, int(rect.x1 * scale) + 4), min(image.height, int(rect.y1 * scale) + 4))
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        crop = image.crop(box)
        if crop.width < 600:
            factor = 2 if crop.width >= 300 else 3
            crop = crop.resize((crop.width * factor, crop.height * factor))
        crops.append(crop)
    if not crops:
        return ""
    if sum(c.width * c.height for c in crops) > COMPOSITE_SHARE_LIMIT * image.width * image.height:
        return None
    counted("ocr_regions", len(crops))
    texts = _ocr_images(page, crops)
    return "\n".join(t for t in texts if t and t.strip())


def _ocr_regions_text(page, regions, sha256: str | None, index: int, renders: dict):
    """Tier 1, from the page cache when this content's images were read
    before. None when the regions are not the cheaper read (`_ocr_regions`)."""
    from app.services import page_cache

    cached = page_cache.get_ocr(sha256, index, OCR_REGIONS_VARIANT)
    if cached is not None:
        counted("ocr_cache_hits")
        return cached
    image = renders.get(index)
    if image is None:
        image = renders[index] = _render_page(page)
    text = _ocr_regions(page, regions, image)
    if text is not None:
        page_cache.put_ocr(sha256, index, text, OCR_REGIONS_VARIANT)
    return text


# How pages are parsed into records: the grammar below, `parse_page`,
# `submission_cover`, the folding of a reply into the submission it
# answers. Kept on every reading (extracted["parser_version"]); a reading
# made under an earlier version is read again when its row is next
# processed (document_processing._previous_sha), and the targeted repair
# (scripts/repair_extraction.py) re-reads the rows a change is known to
# affect -- without bumping INDEX_VERSION, which re-reads every project.
#   parse-2026-09-28.1  a date is not a reference; a reference keeps the
#                       sheets it lists past a slash; a submission cover is
#                       read as a cover; a reply is folded into the
#                       submission whose sheets it names; a framed option
#                       (an annotation) is a decision.
# parse-2026-09-28.2 (M2): a reference broken across a line at a hyphen is
# read whole; a drawing cover of a system the platform does not track is
# still a record (system_code None, `raw_system` kept); a decision framed
# by a drawn rectangle or a highlight is read (box-3); a scanned PDF
# transmittal is read through its OCR; the printed revision and where the
# revision came from are kept beside the revision; a reader defect raises
# instead of standing as an empty "unreadable" reading.
# parse-2026-09-28.3 (M2 review 01): every page of a file is visited or
# listed as skipped/failed (page ledger); marks of every method are
# validated alike and collected before a decision, disagreement is a
# conflict; a mark on a folder-revision sheet that prints another revision
# is held as a candidate; drawn/highlighted frames, untracked-discipline
# covers and scanned transmittals are promoted to records only on the
# evaluation path (EXTRACTION_PROMOTE_OBSERVATIONS), else kept as
# observations; a reference cut at a hyphen is flagged incomplete.
PARSER_VERSION = "parse-2026-09-28.4"

# The codes a controlled document's reference carries. Contractors number
# them their own way: MAS and MAR are both a material submittal (material
# approval request), SDW, DWG and SD a shop drawing, SAR a sample. MS is a
# method statement and is not one of these.
_CODES = r"(MAS|MAR|SDW|DWG|SD|SAR)"
# A reference may go on past a slash with the sheets it covers -- the
# submission "…-SD-MEP-FA-0054" lists "…-SD-MEP/FA-104 A~104 M", the reply
# answers "…-SD-MEP/FA-100,101,102,104&105", a sheet is "…-SD-MEP/FA-104-M".
# Read whole: cut at the slash, every submission, sheet and reply of a
# project read as one reference, and the register folded them into one.
_SHEETS = r"(?:/[A-Z0-9]{1,6}-\d{1,5}(?:[ -]?[A-Z](?![A-Z0-9]))?(?:\s*[,&~]\s*\d{1,5}(?:[ -]?[A-Z](?![A-Z0-9]))?)*)?"
# A segment may continue on the next line after its hyphen: a scanned form
# wraps "R1029-CSM-CO-ELE-FA-MAR-PJW-ZZZ-" / "ZZZ-1004" and OCR keeps the
# break. Only a hyphen carries the reference over a line, and only onto a
# segment that starts with a letter: a bare line break ends it, and a number
# on the next line ("…-SD-MEP-" / "0042", a cover's own fields read by OCR)
# is never glued on -- that made a serial of a base and unfolded the reply
# behind EP-30088's FA-0042 cover.
_SEGMENT = r"(?:-[A-Z0-9]+|-\n[A-Z][A-Z0-9]*)"
REF = re.compile(r"\b[A-Z0-9]+" + _SEGMENT + r"*-" + _CODES + r"-[A-Z0-9]+" + _SEGMENT + r"*-?" + _SHEETS, re.I)
# A date written "6-Mar-2026" has the shape of a MAR reference and is not
# one: a number, a month, a year. A real reference with a numeric prefix
# ("123-MAR-001") has no year where the year would be.
_DATE_SHAPED = re.compile(r"^\d{1,2}-(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)-(?:\d{2}|\d{4})$", re.I)
_CATEGORY_OF_CODE = {"MAS": "submittals", "MAR": "submittals", "SAR": "samples", "SDW": "drawings", "DWG": "drawings",
                     "SD": "drawings"}


def is_date_shaped(reference: str | None) -> bool:
    """Whether a reference is a date the grammar mistook for one."""
    return bool(reference) and bool(_DATE_SHAPED.match(reference.strip()))


@dataclass(frozen=True)
class ReferenceCandidate:
    """A reference the grammar found on a page, with what kind it is."""

    reference: str
    code: str
    start: int
    end: int
    # "serial": ends in a number of its own (a submission or a form);
    # "sheets": lists sheets past a slash (what a submission covers, or a
    # reply answers); "other": anything else.
    kind: str
    # The reference ended at a hyphen with nothing joinable after it
    # ("…-PJW-ZZZ- Rev.02" then "2ZZZ-1004" on the next OCR line): what
    # was read is the start of it, not the whole, and is marked so rather
    # than guessed at.
    incomplete: bool = False

    @property
    def category(self) -> str:
        return _CATEGORY_OF_CODE[self.code]

    @property
    def base(self) -> str:
        return self.reference.split("/", 1)[0].upper()


def reference_candidates(text: str) -> list[ReferenceCandidate]:
    """Every reference on the page, in order, dates left out. The first is
    not thereby the document's: `submission_cover` and `parse_page` choose."""
    out = []
    for match in REF.finditer(text):
        raw = match.group()
        reference = raw.replace("-\n", "-").rstrip("-.")
        if is_date_shaped(reference):
            continue
        base = reference.split("/", 1)[0]
        kind = "sheets" if "/" in reference else "serial" if re.search(r"-\d{2,}[A-Z]?$", base, re.I) else "other"
        out.append(ReferenceCandidate(reference, match.group(1).upper(), match.start(), match.end(), kind,
                                      incomplete=raw.rstrip(".").endswith("-")))
    return out


def first_reference(text: str) -> ReferenceCandidate | None:
    """The first reference on the page that is not a date; None for none."""
    found = reference_candidates(text)
    return found[0] if found else None
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
    # The sheets a submission cover lists ("…/FA-104 A~104 M"): what the
    # submission covers, and what a reply that names them answers. Never
    # the submission's own identity -- that is `reference`.
    listed: tuple = ()
    # The revisions this one replaced, newest first: a drawing is one row
    # at the revision that stands, and these are the records that came
    # before it, kept whole so the log can show each revision's own status
    # and open its own file. Never stored -- the collapse runs when the
    # records are read, not when they are written.
    superseded: tuple = ()
    # Raw facts kept beside the normalised ones (M2). `raw_system`: the
    # discipline the page names when it is not one the platform tracks
    # ("FF", "FIREFIGHTING"), so the record is still read and a consumer
    # decides what to do with it. `printed_revision`: the revision the
    # sheet's own title block prints ("00"), as printed, even where the
    # revision above was taken from elsewhere. `revision_source`: where
    # `revision` came from -- "printed" (the page's REV field), "cover"
    # (the submission cover's Rev field), "suffix" (the reference's -R1),
    # "folder" (the R1 folder the file sits in: an inference, never proof),
    # "default" (nothing said it: R0 assumed).
    raw_system: str | None = None
    printed_revision: str | None = None
    revision_source: str | None = None
    # Marks that name an option but were not made the status: marks that
    # disagree with each other, a mark on a sheet whose revision came from
    # its folder while the sheet prints another (the association is not
    # the reader's to make), a method not yet promoted (see
    # `read_open_pdf`). Each: (status, label, method).
    decision_candidates: tuple = ()
    # What the reader could not settle about this record, by name:
    # "reference_incomplete", "decision_conflict", "decision_revision_unvalidated",
    # and the carry flags "carried_unvisited", "carried_unverified",
    # "carried_other_profile" (document_sync.carry_unvisited).
    flags: tuple = ()
    # For a record a bounded reading carried from an earlier reading of
    # the same file: where that earlier reading was made -- source content
    # hash, parser version, profile and time, None where unknown (M2 review
    # 03). Never restamped by a later envelope.
    retained: dict | None = None


# A submission's cover: the contractor's own form in front of the sheets
# or the datasheets, headed for what it submits, with the submission's
# number in its No / Reference field and the sheets it covers listed
# below. It carries other references too -- the sheets, a number quoted
# in the comments -- and a date beside the number, so the first reference
# on the page is not its identity; the numbered one of the heading's kind is.
_COVER_HEADING = re.compile(r"\b(SHOP\s*DRAWINGS?|MATERIALS?|SAMPLES?)\s+SUBMITTAL\b", re.I)
_COVER_LABELS = re.compile(r"submitting\s+herewith|DRAWING\s*&\s*DESIGN\s*REF|Recommendation|Received\s+By|Submitted\s+By",
                           re.I)
_COVER_CATEGORY = {"SHOP": "drawings", "MATE": "submittals", "SAMP": "samples"}
# A line of the cover that is a label or a code, not the description of
# what is submitted: a field name, a party, an option, a form code ("F2 - SDS").
_LABEL_LINE = re.compile(r"^(?:.*:|Client|Consultant|Main\s+Contractor|MEP\s+Contractor|SOFT\s+COPY|HARD\s+COPY|Remarks"
                         r"|S\.\s*No\.?|TYPE\s*\+?|Technical\s+Submission|\d+|[A-D]\s*-.*|UR-.*|[A-Z]\d+\s*-\s*[A-Z]{1,5})$", re.I)


@dataclass(frozen=True)
class Cover:
    category: str
    reference: str
    revision: str | None
    listed: tuple
    description: str | None


def submission_cover(text: str) -> Cover | None:
    """The page as a submission cover, or None: headed for what it submits,
    labelled as a form, and carrying a numbered reference of that kind."""
    heading = _COVER_HEADING.search(text)
    if heading is None or not _COVER_LABELS.search(text):
        return None
    category = _COVER_CATEGORY[heading.group(1)[:4].upper()]
    candidates = reference_candidates(text)
    serials = [c for c in candidates if c.kind == "serial" and c.category == category]
    if not serials:
        return None
    # The submission's own number: written by its label where the form
    # keeps one ("No: …"), else the first numbered reference of the kind.
    labelled = next((c for c in serials
                     if re.search(r"(?:\bNo\.?|Reference|Ref\.?)\s*:?\s*$", text[max(0, c.start - 24):c.start], re.I)), None)
    cover = labelled or serials[0]
    listed = tuple(dict.fromkeys(" ".join(c.reference.split()) for c in candidates if c.kind == "sheets"))
    revision_match = REV.search(text)
    revision = f"R{int(revision_match.group(1))}" if revision_match else None
    description = None
    first_sheet = next((c for c in candidates if c.kind == "sheets"), None)
    if first_sheet is not None:
        for line in text[first_sheet.end:].splitlines()[1:6]:
            line = line.strip()
            if not line or _LABEL_LINE.match(line) or not re.search(r"[A-Za-z]{3}", line) or REF.search(line):
                continue
            description = " ".join(line.split())
            break
    return Cover(category, cover.reference, revision, listed, description)


_RAW_DISCIPLINE = re.compile(
    r"\b(FIRE\s*FIGHTING|FIREFIGHTING|SPRINKLERS?|PLUMBING|DRAINAGE|HVAC|LPG|ELV|ACCESS\s+CONTROL|CCTV|BMS|SMOKE\s+MANAGEMENT|"
    r"LIGHTING|ELECTRICAL|MECHANICAL|STRUCTUR\w*|ARCHITECTUR\w*)\b", re.I)


def raw_system_of(reference: str, *texts: str) -> str | None:
    """What discipline a page names when it is not a system the platform
    tracks: the words of the title ("FIREFIGHTING LAYOUT"), else the
    reference's infix ("…-SD-MEP-FF-0047" -> "FF"). Kept raw, never
    mapped to a system code."""
    for text in texts:
        found = _RAW_DISCIPLINE.search(text or "")
        if found:
            return " ".join(found.group(1).upper().split())
    infix = re.search(r"[-/]([A-Z]{2,4})-\d{2,5}\b", reference or "", re.I)
    return infix.group(1).upper() if infix else None


def _reference_system(reference: str) -> str | None:
    """The system a reference's infix names ("-FA-", "-EM-"), or None."""
    if re.search(r"[-/](?:FA|FAS|VE|FT)-", reference, re.I):
        return "FAS"
    if re.search(r"[-/](?:LI|ELM|EML|ELS|CBS|EM|EL)-", reference, re.I):
        return "ELS"
    if re.search(r"[-/]FRC-", reference, re.I):
        return "FRC"
    return None


# The options a shop drawing submittal form offers, and what each means.
# Longest first: "approved as noted" is not an approval.
# Written as they read once the punctuation is out of the way: the forms
# spell it "Re- Submit", "Re-Submit" and "Resubmit" between them.
_OPTIONS: tuple[tuple[str, str], ...] = (
    ("approved as noted", "ANN"),
    ("as noted", "ANN"),
    # The SAMANA form's B option: "Approved With Comments" is approved as
    # noted, not approved (read_decision already read it so; the framed and
    # boxed readers did not, and called a framed B an approval).
    ("approved with comments", "ANN"),
    ("with comments", "ANN"),
    # The boxed reader's clip stops short of the label's end ("B - Approved
    # With"): still the B option, not the A.
    ("approved with", "ANN"),
    # "C - Revise & Re-Submit", "REVISION & RESUBMIT", "Revise and Resubmit":
    # one answer, whatever the form calls it.
    ("revise re submit", "rejected"),
    ("revise resubmit", "rejected"),
    ("revise and resubmit", "rejected"),
    ("revision resubmit", "rejected"),
    ("revision and resubmit", "rejected"),
    ("revise", "rejected"),
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


# How boxes are read: `_OPTIONS`, `_is_mark`, `boxed_decision` and
# `annotated_decision`. What a page's boxes said is cached under this
# (app.services.page_cache) -- change any of them, bump it, or the old
# readings are reused.
#   box-2  a framed option -- an annotation drawn round the choice -- is read
#   box-3  a frame drawn into the page itself (a stroked rectangle round the
#          option, a highlight-sized fill over it) is read too
#   box-4  every mark (filled box, annotation, drawn frame) is validated alike
#          and collected before anything is decided; the cache holds the
#          marks, and disagreeing marks are a conflict, not a choice
BOX_VERSION = "box-4"
# Annotation types a reviewer draws round or over an option: a stamp, a
# square, an ink stroke, a highlight. Not a note, not a link.
_FRAME_ANNOTS = {12, 13, 15, 8, 4}   # Ink, Stamp, Ink-like FreeText... see pymupdf PDF_ANNOT_*: Square=4, Highlight=8, Ink=12? kept broad below by name


def _option_in(label: str) -> tuple[str, str] | None:
    """The one option a label names, as (status, label); None for none or several."""
    plain = " ".join(re.sub(r"[^a-z0-9]+", " ", label.lower()).split())
    found = {}
    for words, status in _OPTIONS:
        if words in plain:
            found.setdefault(status, label.strip())
            break
    return next(iter(found.items())) if len(found) == 1 else None


def _annotation_frames(page) -> list:
    """Label-sized annotations a reviewer laid over an option: a stamp, a
    square, an ink stroke, a highlight. Not a note, not a link."""
    try:
        annots = list(page.annots())
    except Exception:  # noqa: BLE001 -- a page whose annotations MuPDF cannot list
        return []
    out = []
    for annot in annots:
        kind = (annot.type[1] if isinstance(annot.type, tuple) and len(annot.type) > 1 else str(annot.type)).lower()
        if kind not in ("stamp", "square", "ink", "highlight", "freetext", "polygon", "rectangle"):
            continue
        rect = annot.rect
        if 20 <= rect.width <= 320 and 6 <= rect.height <= 48:
            out.append((rect, "annotation"))
    return out


def _words_inside(words, rect) -> str:
    return " ".join(w[4] for w in words if w[0] >= rect.x0 - 2 and w[2] <= rect.x1 + 2 and w[1] >= rect.y0 - 3 and w[3] <= rect.y1 + 3)


def _frame_marks(page, frames, words) -> list[dict]:
    """The option each frame *is* (one option and little else under it,
    `_label_is_one_option`), whatever drew the frame. A frame over a row
    of options, over a comment that mentions one, or over nothing names
    no option and is not a mark."""
    marks = []
    for rect, method in frames:
        inside = _words_inside(words, rect)
        if not inside or len(inside) > 80:
            continue
        status = _label_is_one_option(inside)
        if status is None:
            continue
        marks.append({"status": status, "label": inside.strip(), "method": method,
                      "rect": [round(v, 1) for v in (rect.x0, rect.y0, rect.x1, rect.y1)]})
    return marks


def _filled_box_marks(page, shapes, words) -> list[dict]:
    """The option beside each filled box (`_is_mark`), read from the page's
    text to the right of the box and validated like every other mark."""
    marks = []
    for shape in shapes:
        if not _is_mark(shape):
            continue
        rect = shape["rect"]
        middle = (rect.y0 + rect.y1) / 2
        # The words on the box's own line, to its right: a page's text
        # clipped to a rectangle bleeds the lines above and below into the
        # label ("Consultant Recommendation A - Approved B - Approved With
        # Comments"), which names two answers and settles nothing.
        line = sorted((w for w in words if rect.x1 - 2 <= w[0] <= rect.x1 + 150 and w[1] - 1 <= middle <= w[3] + 1), key=lambda w: w[0])
        near = " ".join(w[4] for w in line)
        if not _may_hold_an_option(near):
            continue
        # Only as far as this option's own letter: the next option's box
        # sits a little further along the same line.
        label = " ".join(near.split())
        cut = re.search(r"\([ABCD]\)", label)
        if cut:
            label = label[: cut.end()]
        # The next option's letter may still be in the clip ("B - Approved
        # With Comments C -"): the label is read up to it.
        label = re.sub(r"\s+[ABCD]\s*-\s*$", "", label)
        status = _label_is_one_option(label)
        if status is None:
            continue
        marks.append({"status": status, "label": label.strip(), "method": "filled_box",
                      "rect": [round(v, 1) for v in (rect.x0, rect.y0, rect.x1, rect.y1)]})
    return marks


def decision_marks(page, text: str | None = None, shapes=None) -> list[dict]:
    """Every mark on the page that selects one option, whatever made it:
    a filled box, a PDF annotation over the label, a rectangle stroked or
    filled into the page. All of them validated the same way
    (`_label_is_one_option`) and all collected before anything is decided,
    so one method cannot hide another's answer. Each mark: status, the
    label under it, the method and its rectangle."""
    if text is not None and not _may_hold_an_option(text):
        return []
    if shapes is None:
        try:
            shapes = page.get_drawings()
        except Exception:  # noqa: BLE001 -- a page whose drawings MuPDF cannot list
            shapes = []
    annotated = _annotation_frames(page)
    # An annotation's appearance is among the page's drawings too: a shape
    # that sits where an annotation sits is that annotation, not a second
    # frame drawn into the page.
    def is_annotation(rect) -> bool:
        return any(abs(rect.x0 - a.x0) <= 3 and abs(rect.y0 - a.y0) <= 3 and abs(rect.x1 - a.x1) <= 3 and abs(rect.y1 - a.y1) <= 3
                   for a, _method in annotated)
    frames = annotated + [(shape["rect"], "drawn_frame") for shape in shapes if _is_label_frame(shape) and not is_annotation(shape["rect"])]
    if not frames and not any(_is_mark(s) for s in shapes):
        return []
    words = page.get_text("words")
    return _filled_box_marks(page, shapes, words) + _frame_marks(page, frames, words)


def resolve_marks(marks: list[dict]) -> tuple[str, str | None, str | None]:
    """What the marks settle, as (outcome, status, evidence): "none" for no
    mark; "resolved" when every mark names the same answer (the evidence is
    the first label); "conflict" when marks name different answers -- both
    kept in the evidence text, nothing chosen. Two marks on one answer are
    one answer; two on different answers say no more than none."""
    if not marks:
        return "none", None, None
    statuses = {m["status"] for m in marks}
    if len(statuses) == 1:
        return "resolved", marks[0]["status"], marks[0]["label"]
    evidence = "Conflicting marks: " + "; ".join(f"{m['label']} [{m['method']}]" for m in marks)
    return "conflict", None, evidence


def boxed_decision(page, text: str | None = None) -> tuple[str, str] | None:
    """The consultant's decision on the page as (status, evidence), or None
    when no mark selects an option -- or when marks disagree (a conflict is
    not a decision; `decision_marks` keeps the evidence). Marks are filled
    boxes, annotations over an option and rectangles drawn into the page,
    read and validated alike (see `decision_marks`)."""
    outcome, status, evidence = resolve_marks(decision_marks(page, text))
    return (status, evidence) if outcome == "resolved" else None


def annotated_decision(page, text: str | None = None) -> tuple[str, str] | None:
    """The decision the page's annotations alone select (see `decision_marks`)."""
    marks = [m for m in decision_marks(page, text) if m["method"] == "annotation"]
    outcome, status, evidence = resolve_marks(marks)
    return (status, evidence) if outcome == "resolved" else None


def _is_label_frame(shape) -> bool:
    """Whether a drawn shape is a frame the size of one option's label: a
    stroked rectangle round it (a coloured outline, not the form's own
    black rules) or a highlight-sized fill over it (a colour, not the
    white of the form). Sized to one label, so a table border or a frame
    round the whole comments block is not one."""
    rect = shape["rect"]
    if not (20 <= rect.width <= 320 and 6 <= rect.height <= 48):
        return False
    kind = shape.get("type") or ""
    colour = shape.get("color")
    fill = shape.get("fill")
    if "s" in kind and colour and len(colour) >= 3 and (shape.get("width") or 0) >= 0.8 and max(colour[:3]) > 0.15:
        return True
    if fill and len(fill) >= 3 and min(fill[:3]) <= 0.85 and max(fill[:3]) > 0.15 and (rect.width / max(rect.height, 0.01)) > 2:
        return True
    return False


def drawn_frame_decision(page, words=None, shapes=None) -> tuple[str, str] | None:
    """The decision the rectangles drawn into the page alone select (see
    `decision_marks`): a green rectangle stroked round "C - Revise &
    Re-Submit", an orange highlight laid over it."""
    marks = [m for m in decision_marks(page, None, shapes) if m["method"] == "drawn_frame"]
    outcome, status, evidence = resolve_marks(marks)
    return (status, evidence) if outcome == "resolved" else None


def _label_is_one_option(inside: str) -> str | None:
    """The option a framed label *is*, or None: the words under the frame
    must be one option and little else -- its letter, a dash. A comment
    box on a drawing ("Follow the approved builders work drawings for
    riser location") is a coloured rectangle round words that mention an
    option; it is not the option, and reading it as one called EP-30784's
    QA/QC comments an approval. The same rule for every method: an
    annotation round that comment is no more an approval than a rectangle."""
    named = _options_named(inside)
    if len(named) != 1:
        return None
    plain = " ".join(re.sub(r"[^a-z0-9]+", " ", inside.lower()).split())
    for words, _status in _OPTIONS:
        plain = plain.replace(words, " ")
    # Nothing may remain but the option's letter and the form's own words:
    # "Approved supplier", "Noted & Complied", a comment, a receipt label
    # are not the option however they are framed.
    residue = [w for w in plain.split() if w not in ("a", "b", "c", "d", "ur", "re", "review", "under", "recommendation", "consultant", "and", "status")]
    if residue:
        return None
    return next(iter(named))


def _options_named(label: str) -> set[str]:
    """The distinct answers a label names. "B - Approved With Comments" is one
    answer (ANN), not two, although "approved" is in it too: each option
    phrase found is consumed before shorter phrases are tried, so a row of
    options ("A - Approved B - Approved With Comments C - Re-Submit") still
    names several."""
    plain = " ".join(re.sub(r"[^a-z0-9]+", " ", label.lower()).split())
    named: set[str] = set()
    for words, status in _OPTIONS:
        if words in plain:
            named.add(status)
            plain = plain.replace(words, " ")
    return named

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


def printed_revision(text: str, reference: str) -> str | None:
    """The revision a CAD title block prints, as printed ("00", "01"), or
    None. The block's values follow the drawing reference as one run (see
    title_block): title, layout, date, sheet size, revision -- so the
    revision is the one- or two-digit value right after the sheet size
    (A0..A4). Only that: a number anywhere else in the run is a scale, a
    sheet count or a reference drawing's revision."""
    index = text.find(reference)
    if index < 0:
        return None
    values = [line.strip() for line in text[index + len(reference):].splitlines() if line.strip()][:8]
    for size, after in zip(values, values[1:]):
        if re.fullmatch(r"A[0-4]", size, re.I) and re.fullmatch(r"\d{1,2}", after):
            return after
    return None


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
    candidate = first_reference(text)
    drawing_match = DRAW_REF.search(text)
    # A reply sheet quotes the reference it answers. Registering it would
    # displace the submission form carrying that reference, so it is only
    # ever read for the decision it may carry (source="reply"), and
    # _scan_document_control attaches that to the submission itself.
    if REPLY_SHEET.search(text) and not SUBMISSION_FORM.search(text):
        if candidate is None: return []
        decision, evidence = read_decision(text)
        reference = re.sub(r"-R\d+$", "", candidate.reference, flags=re.I)
        revision_match = REV.search(text) or re.search(r"\bR\.?\s*(\d{1,3})\b", text)
        return [ControlledDocument(
            None, "Reply to consultant comments", path, modified, reference,
            f"R{int(revision_match.group(1))}" if revision_match else "R0",
            decision, None, evidence, page, source="reply", category="reply",
            revision_source="printed" if revision_match else "default")]
    cover = submission_cover(text)
    if cover is not None:
        # The contractor's cover in front of what it submits: its number is
        # the submission's, the sheets it lists are what it covers, the
        # description is the title, and the date beside the number is a date.
        reference, category, listed = cover.reference, cover.category, cover.listed
        revision = cover.revision or (folder_revision(path) if category == "drawings" else None) or "R0"
        title = cover.description or reference
        named = f"{title} " + " ".join(listed)
        code = ("FRC" if re.search(r"\bcables?\b", title, re.I) else None) \
            or next((code for code, pattern in SYSTEMS if re.search(pattern, named, re.I)), None) \
            or next((s for s in (_reference_system(r) for r in (reference, *listed)) if s), None)
        raw_system = None
        if code is None and category == "drawings":
            # A cover of a discipline the platform does not track (fire
            # fighting, plumbing) is still a submission with a number, a
            # revision and a decision on it: read, with the discipline
            # kept raw, for whoever asks; no system record is built from it.
            # From the title and the reference only: the form's own
            # discipline checklist (Architecture, Structure, HVAC ...) is
            # printed on every cover and names nothing.
            raw_system = raw_system_of(reference, named)
        if code is None and category != "drawings":
            code = next((code for code, pattern in SYSTEMS if re.search(pattern, text, re.I)), None)
        decision, evidence = read_decision(text)
        floor = floor_name(title) or floor_name(Path(path).stem, whole=False)
        revision_source = "cover" if cover.revision else "folder" if category == "drawings" and folder_revision(path) else "default"
        return [ControlledDocument(code, title, path, modified, reference, revision, decision, floor, evidence, page,
                                   category=category, listed=listed, raw_system=raw_system, revision_source=revision_source)]
    match = candidate
    if match:
        reference = candidate.reference
        category = candidate.category
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
    suffix = re.search(r"-R(\d+)$", reference.split("/", 1)[0], re.I)
    # The drawing's own revision first; the folder only where it prints
    # none. A resubmission is filed with the comments it answers -- a
    # floor's R1 folder holds the R1 drawing *and* the stamped R0 sheet --
    # so taking the revision off the folder made that sheet an R1 and its
    # "revise and resubmit" the verdict on a revision no one had seen.
    # Settled by the platform owner on 2026-09-24, reversing the rule that
    # had the folder win.
    if revision_match:
        revision, revision_source = f"R{int(revision_match.group(1))}", "printed"
    elif suffix:
        revision, revision_source = f"R{int(suffix.group(1))}", "suffix"
    elif category == "drawings" and folder_revision(path):
        revision, revision_source = folder_revision(path), "folder"
    else:
        revision, revision_source = "R0", "default"
    reference = re.sub(r"-R\d+$", "", reference, flags=re.I)
    layout = None
    printed = None
    if category == "drawings" and not SUBMISSION_FORM.search(text):
        # A drawing sheet: read the title block by position (see title_block).
        block_title, layout = title_block(text, reference)
        # The revision the block prints, kept as printed beside the one
        # above: on a sheet filed under R1 that still says 00, both are facts.
        printed = printed_revision(text, reference)
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
    # A drawing sheet of no system the platform tracks stays no record, as
    # before: the sheet behind a cover ("…/FA-110", "…/FF-110") is not the
    # submission -- the cover is, and the cover of an untracked discipline
    # is read above with its discipline kept raw. Reading every such sheet
    # would have made a register row of each sheet number (M2, tested).
    if code is None and category == "drawings": return []
    if code is None: code = next((code for code, pattern in SYSTEMS if re.search(pattern, text, re.I)), None)
    decision, evidence = read_decision(text)
    # The floor off the sheet, and failing that off the file it came in.
    # A submission covering several floors is titled for the drawing and
    # names them only on the wrapper -- "Shop Drawing for Basement 4, 3, 2
    # Floor Plan" -- so without this it reached the log as a drawing of no
    # floor at all, and those basements looked undrawn.
    floor = floor_name(title) or floor_name(Path(path).stem, whole=False)
    flags = ("reference_incomplete",) if candidate is not None and candidate.incomplete else ()
    return [ControlledDocument(code, title, path, modified, reference, revision, decision, floor, evidence, page, category=category,
                               printed_revision=printed, revision_source=revision_source, flags=flags)]


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


def _marks(page, text: str, sha256: str | None, index: int) -> list[dict]:
    """`decision_marks`, from the page cache when this file's content has
    been looked at under the same box rules (`BOX_VERSION`)."""
    if not _may_hold_an_option(text):
        return []            # nothing to look for, and nothing worth keeping
    from app.services import page_cache

    cached = page_cache.get_box(sha256, index, BOX_VERSION)
    if cached is not page_cache.MISSING:
        counted("box_cache_hits")
        return list(cached or [])
    with timed("boxes"):
        marks = decision_marks(page, text)
    page_cache.put_box(sha256, index, BOX_VERSION, marks or None)
    return marks


def _boxed(page, text: str, sha256: str | None, index: int) -> tuple[str, str] | None:
    """The resolved decision of `_marks`, or None (no mark, or a conflict)."""
    outcome, status, evidence = resolve_marks(_marks(page, text, sha256, index))
    return (status, evidence) if outcome == "resolved" else None


def _ocr_text(page, sha256: str | None, index: int, renders: dict | None = None) -> str:
    """The page's OCR text, from the page cache when this file's content
    has been OCRed before (app.services.page_cache). `renders`: the
    document's page renders, so a page tier 1 rendered is not rendered again."""
    from app.services import page_cache

    cached = page_cache.get_ocr(sha256, index)
    if cached is not None:
        counted("ocr_cache_hits")
        return cached
    counted("ocr_full_pages")
    image = renders.get(index) if renders else None
    text = _ocr_page(page, image) if image is not None else _ocr_page(page)
    page_cache.put_ocr(sha256, index, text)
    return text


def page_text(page, index: int, cache: dict | None) -> str:
    """The page's text layer, extracted once per document: `cache` (page
    index -> text) is the document's own, shared by whoever needs the same
    page -- classification, the parse, the OCR decision -- so the PDF
    library is asked for a page once."""
    if cache is not None and index in cache:
        counted("page_text_reused")
        return cache[index]
    counted("page_text_extractions")
    with timed("pdf_text_extract"):
        text = page.get_text()
    if cache is not None:
        cache[index] = text
    return text


def open_failure_notes(path: Path, exc: BaseException) -> tuple[str, ...]:
    """The note a document gets when its PDF could not be opened or read."""
    if isinstance(exc, OSError) and (exc.errno == 22 or "cloud" in str(exc).lower()):
        # A OneDrive file that is still online-only cannot be read at all.
        # It is not a corrupt document and saying so sends the reader to the
        # wrong problem: the fix is to make the folder available offline.
        return (f"{path.name} {NOT_DOWNLOADED}; make the project folder available offline, then refresh.",)
    return (f"{UNREADABLE}{path.name}.",)


@lru_cache(maxsize=1024)
def _read_pdf(filename: str, stamp: int, size: int, use_ocr: bool,
              sha256: str | None = None) -> tuple[tuple[ControlledDocument, ...], tuple[str, ...]]:
    """The document-control records a PDF holds. `sha256`, the file's
    content hash when the caller knows it, lets OCR text be reused from an
    earlier reading of the same content. Opens the file itself; a caller
    that already has it open (document_sync.extract) uses `read_open_pdf`."""
    path = Path(filename)
    modified = datetime.fromtimestamp(stamp / 1e9, timezone.utc)
    counted("read_pdf")
    try:
        pdf = _open_pdf(path)
    except Exception as exc:  # noqa: BLE001 -- an unreadable or online-only file is a note, not a failure
        return (), open_failure_notes(path, exc)
    # Opened: what the reader does with it is the reader's own, and a
    # defect there raises (the caller keeps the previous reading).
    with pdf:
        return read_open_pdf(pdf, filename, modified, use_ocr, sha256)


def read_pdf_full(filename: str, stamp: int, size: int, use_ocr: bool, sha256: str | None = None,
                  promote: bool | None = None) -> "Reading":
    """`_read_pdf` with the page ledger and the observations (`Reading`), not
    cached: what `document_sync.process` reads when no reader process handed
    it the reading. An unopenable or online-only file is a Reading with no
    records, the note it always got and an "unavailable"/"failed" ledger."""
    path = Path(filename)
    modified = datetime.fromtimestamp(stamp / 1e9, timezone.utc)
    counted("read_pdf")
    try:
        pdf = _open_pdf(path)
    except Exception as exc:  # noqa: BLE001 -- an unreadable or online-only file is a note, not a failure
        notes = open_failure_notes(path, exc)
        return Reading((), notes, {"outcome": "unavailable" if NOT_DOWNLOADED in notes[0] else "failed", "pages_total": None,
                                   "pages_visited": [], "pages_skipped": [], "pages_failed": [], "ocr_failed_pages": [],
                                   "stop_reason": "open_failed", "error": f"{type(exc).__name__}: {exc}"[:300],
                                   "parser_version": PARSER_VERSION}, [])
    with pdf:
        return read_open_pdf(pdf, filename, modified, use_ocr, sha256, full=True, promote=promote)


# How far the reader looks into a file that has shown it nothing yet: a
# catalogue or a specification is not a register, and reading every product
# page of one looking for a form is not worth it -- but a separator sheet,
# a blank page or a transmittal in front of the cover must not hide it
# either. The first PAGE_SCAN_LIMIT pages are read (text layer, no OCR
# beyond the scanned-page rule); pages past it are recorded as skipped,
# by number and reason, never silently counted as checked.
PAGE_SCAN_LIMIT = 12
# Pages checked for the consultant's reply behind a form or a cover.
REPLY_SEARCH_LIMIT = 12
# Pages OCRed per document.
OCR_PAGE_LIMIT = 12


@dataclass
class Reading:
    """A document as read: the records, the notes, the page ledger and the
    observations that are not records (see `read_open_pdf`)."""

    records: tuple = ()
    notes: tuple = ()
    coverage: dict = field(default_factory=dict)
    observations: list = field(default_factory=list)


def extraction_profile(promote: bool | None = None) -> str:
    """The extraction policy a reading was, or would be, made under: "promoted"
    (the evaluation path: every method and component becomes a record) or
    "default" (the compatibility path). Part of a reading's identity beside
    the parser version and the content hash (M2 review 02, C): a reading made
    under one profile is not the reading of the other, whatever the bytes."""
    if promote is None:
        promote = _promote_default()
    return "promoted" if promote else "default"


def _promote_default() -> bool:
    """Whether observations the M2 reader can make but earlier readers did
    not are promoted to records and statuses (`EXTRACTION_PROMOTE_OBSERVATIONS`).
    Off by default: the ordinary path reads as the accepted reader did and
    keeps the new observations beside the records, for evaluation; the
    isolated evaluation path turns it on."""
    try:
        from app.core.config import get_settings

        return bool(get_settings().extraction_promote_observations)
    except Exception:  # noqa: BLE001 -- no settings (a script, a test without the app): keep the accepted behaviour
        return False


def observed_record(row: "ControlledDocument") -> dict:
    """A record held as an observation, as JSON stores it: the stored-record
    shape (document_sync._record_dict) with the datetime and the path as
    text. `asdict` alone left `modified` a datetime and the row's JSON column
    refused it -- 35 of 878 rows of the M2 review-01 clone repair failed on
    exactly that (every untracked-discipline cover and scanned transmittal),
    and the ordinary writer would have failed the same rows."""
    data = asdict(row)
    modified = data.get("modified")
    if hasattr(modified, "isoformat"):
        data["modified"] = modified.isoformat()
    if data.get("path") is not None:
        data["path"] = str(data["path"])
    return data


def untracked_sheet_observation(text: str) -> dict | None:
    """A drawing sheet of a discipline the platform does not track, as an
    observation: the sheet number and the discipline it names. Read the
    way `parse_page` reads a sheet, kept apart from the records so no
    register row is made of it."""
    candidate = first_reference(text)
    if candidate is None or candidate.category != "drawings":
        return None
    if not re.search(r"drawing\s*(?:title|no|number|submittal)|shop\s*drawing", text, re.I):
        return None
    if SUBMISSION_FORM.search(text) or REPLY_SHEET.search(text):
        return None
    reference = re.sub(r"-R\d+$", "", candidate.reference, flags=re.I)
    named = f"{reference} " + " ".join(text.split()[:80])
    if any(re.search(pattern, named, re.I) for _code, pattern in SYSTEMS) or _reference_system(reference):
        return None   # a tracked system's sheet: parse_page reads it as a record
    return {"kind": "drawing_sheet", "reference": reference, "raw_system": raw_system_of(reference, text[:400])}


def _candidates_of(marks: list[dict]) -> tuple:
    return tuple((m["status"], m["label"], m["method"]) for m in marks)


def _revision_unvalidated(row: ControlledDocument) -> bool:
    """A decision read off a drawing sheet whose revision was taken from its
    folder while the sheet prints another: which revision the mark answers
    is not the reader's to decide (M2 -> M4)."""
    if row.category != "drawings" or row.revision_source != "folder" or not row.printed_revision:
        return False
    return _revision_number(row.printed_revision) != _revision_number(row.revision)


# Methods the accepted reader did not read: promoted only on the evaluation
# path. A rectangle drawn or highlighted into the page (`drawn_frame`).
UNPROMOTED_METHODS = frozenset({"drawn_frame"})


def settle_decision(rows: list, marks: list[dict], ocr_candidates: list[dict], promote: bool) -> tuple[list, list[dict]]:
    """The page's decision settled from every candidate at once (M2 review
    02, B): the status its text carries (method "text"), the marks on it
    (filled boxes, annotations, drawn frames and highlights, `decision_marks`)
    and what OCR read (method "ocr"). Candidates that name different answers
    are a conflict: status UR, every candidate kept, flag `decision_conflict`
    -- no method wins by running later. Candidates that agree settle the
    status when at least one of them is a promoted method (text, filled box,
    annotation, OCR) or the evaluation path is on; agreement among held
    methods alone is a candidate, not a status (`decision_method_unpromoted`).
    No precedence between sources is applied: the stamp-over-tick rule the
    reader once applied implicitly is withdrawn until an explicit policy is
    authorised. Returns (rows, page observations without their page number)."""
    settled: list = []
    conflict: list[dict] | None = None
    held: list[dict] | None = None
    for row in rows:
        own = ([{"status": row.status, "label": row.reply_text or "status read from the page text", "method": "text", "rect": None}]
               if row.status != "UR" else [])
        candidates = own + list(marks) + list(ocr_candidates)
        if not candidates:
            settled.append(row)
            continue
        unpromoted = [c for c in candidates if c["method"] in UNPROMOTED_METHODS and not promote]
        usable = [c for c in candidates if c["method"] not in UNPROMOTED_METHODS or promote]
        if len({c["status"] for c in candidates}) > 1:
            evidence = "Conflicting evidence: " + "; ".join(f"{c['label']} [{c['method']}]" for c in candidates)
            flags = (*row.flags, "decision_conflict", *(("decision_method_unpromoted",) if unpromoted else ()))
            settled.append(replace(row, status="UR", reply_text=evidence, decision_candidates=_candidates_of(candidates),
                                   flags=tuple(dict.fromkeys(flags))))
            conflict = candidates
        elif usable:
            label = next((c["label"] for c in usable if c["method"] != "text"), usable[0]["label"])
            changes = {"status": usable[0]["status"]}
            if row.status == "UR":
                changes["reply_text"] = label
            if len({c["method"] for c in candidates}) > 1 or unpromoted:
                changes["decision_candidates"] = _candidates_of(candidates)
            settled.append(replace(row, **changes))
        else:
            kept = replace(row, status="UR")
            for c in unpromoted:
                kept = _hold_decision(kept, c["status"], c["label"], c["method"], "decision_method_unpromoted")
            settled.append(kept)
            held = unpromoted
    observations = []
    if conflict:
        observations.append({"kind": "decision_conflict", "marks": conflict})
    if held:
        observations.append({"kind": "decision_unpromoted", "marks": held})
    return settled, observations


def _hold_decision(row: ControlledDocument, status: str, evidence: str | None, method: str, flag: str) -> ControlledDocument:
    """The mark kept beside the record, not made its status."""
    return replace(row, decision_candidates=(*row.decision_candidates, (status, evidence or "", method)),
                   flags=tuple(dict.fromkeys((*row.flags, flag))))


def read_open_pdf(pdf, filename: str, modified: datetime, use_ocr: bool, sha256: str | None = None,
                  page_texts: dict | None = None, *, full: bool = False, promote: bool | None = None):
    """`_read_pdf` over a document already open: the one pass over its pages
    -- text, the parse, marks, OCR where the evidence calls for it, the
    reply attached to a form. `page_texts` is the document's page text
    cache (`page_text`), so a page the caller has read is not read again.

    Returns (records, notes); with `full=True` a `Reading`, whose `coverage`
    is the page ledger -- every page of the file is visited, skipped (by
    reason) or failed (by reason), exclusively, and the outcome says whether
    the reading is "complete", "bounded" (complete within the reader's page
    scope, the rest listed as skipped), "partial" (a page or its OCR
    failed) or "unavailable" (the file itself could not be read) -- and
    whose `observations` are what the reader saw but did not make a record
    of: a sheet of an untracked discipline, a scanned transmittal or a
    decision method not promoted (`promote`), the marks it held back.
    """
    records, warnings, observations = [], [], []
    path = Path(filename)
    clock = stage_clock()
    if page_texts is None:
        page_texts = {}
    if promote is None:
        promote = _promote_default()
    total = pdf.page_count
    visited: list[int] = []
    skipped: list[dict] = []
    failed: list[dict] = []
    ocr_failed: list[int] = []
    ocr_failures: list[dict] = []
    ocr_skipped: list[int] = []
    stop_reason = None
    try:
        if clock is not None:
            clock.set("page_count", total)
        pending = None
        ocr_count = 0
        for index, page in enumerate(pdf):
            number = index + 1
            # A catalogue/specification is not a register: a file that has
            # shown nothing in PAGE_SCAN_LIMIT pages is not read further --
            # but every page up to there is, so a separator in front of the
            # cover hides nothing, and the pages left are recorded.
            if index >= PAGE_SCAN_LIMIT and not records and pending is None:
                stop_reason = "page_scan_limit"
                skipped.extend({"page": n, "reason": "page scan limit: nothing read in the first pages"} for n in range(number, total + 1))
                break
            if index >= REPLY_SEARCH_LIMIT and records and all(row.category != "drawings" for row in records):
                warnings.append(f"{path.name}: only the first {REPLY_SEARCH_LIMIT} pages were checked for submission replies.")
                stop_reason = "reply_search_limit"
                skipped.extend({"page": n, "reason": "reply search limit"} for n in range(number, total + 1))
                break
            counted("pages_scanned")
            visited.append(number)
            try:
                text = page_text(page, index, page_texts)
                with timed("deterministic_extract"):
                    found = parse_page(text, filename, modified, number)
                # The approval block lists every option and fills the box
                # beside the one chosen. As text that is a list of choices
                # and settles nothing -- rightly, since a list is not an
                # answer -- so where nothing was decided the drawing is
                # asked, before any OCR is attempted. Every mark on the page
                # is collected first; marks that disagree are a conflict.
                # The page's marks are collected whatever its text says (M2
                # review 02, B): a status printed in the text and a frame drawn
                # round another option are two candidates, settled together
                # with what OCR adds, below -- never the first one found.
                marks = _marks(page, text, sha256, index) if found else []
                ocr_candidates: list[dict] = []
                # OCR title blocks of scanned pages and image stamps on forms.
                # Also a page whose own text or ticked box already gave a
                # decision: the consultant's stamp, pasted on as an image, can
                # say otherwise -- EP-30784's emergency lighting sample
                # (BBY006-GME-SAR-EL-LI-0001) has "Approved as Noted (B)"
                # ticked and a "(C) Revise & Resubmit" stamp beside it. The
                # stamp used to override the tick implicitly; since M2 review
                # 02 (B) the two are a conflict the record shows, until an
                # explicit precedence policy is authorised (none is). What OCR
                # finds is cached by content
                # (`_ocr_text`), so a page is OCRed once, not on every re-read.
                # A scanned first or second page is read whatever the file is
                # called: a transmittal, a certificate or a stamped cover filed
                # under a plain name was otherwise never looked at (bounded: two
                # pages, cached by content).
                scan = len(text.strip()) < 80
                candidate = bool(found) or pending is not None or (scan and index < 2) \
                    or (scan and bool(re.search(r"approval|submittal|drawing|[/\\]MS[/\\]", filename, re.I)))
                regions = _image_regions(page) if (use_ocr and candidate and not scan) else []
                # A text page whose images are all too small to hold a word (a
                # logo, a rule) is not OCRed at all: nothing on it could be a stamp.
                if use_ocr and candidate and (scan or regions):
                    if ocr_count < OCR_PAGE_LIMIT:
                        try:
                            ocr_count += 1
                            counted("ocr_pages_attempted")
                            renders: dict = {}
                            if scan or _prefer_full_page(page, regions):
                                # A scanned page, or one its images cover or
                                # crowd: the whole of it, as always.
                                ocr_text = _ocr_text(page, sha256, index, renders)
                            else:
                                # A text page with a few images: the images
                                # (tier 1), in one run; the whole page where
                                # they would cost more than it.
                                ocr_text = _ocr_regions_text(page, regions, sha256, index, renders)
                                if ocr_text is None:
                                    ocr_text = _ocr_text(page, sha256, index, renders)
                            renders.clear()
                            with timed("deterministic_extract"):
                                ocr_found = parse_page(ocr_text, filename, modified, number)
                                decision, evidence = read_decision(ocr_text)
                                if not found and not ocr_found and scan and index < 2:
                                    # A scanned copy of a document transmittal
                                    # (the signed acknowledgement filed as a
                                    # PDF): the same sample submissions the
                                    # Word original gives, read off its OCR.
                                    from app.services import transmittals

                                    if transmittals.looks_like_transmittal(ocr_text):
                                        sent = transmittals.from_ocr(ocr_text, filename, modified, page=number)
                                        if promote:
                                            ocr_found = sent
                                        elif sent:
                                            observations.append({"page": number, "kind": "transmittal", "records": [observed_record(r) for r in sent]})
                            if not found: found = ocr_found
                            if ocr_found or decision != "UR":
                                counted("ocr_pages_used")   # the OCR text changed what was read
                            if decision != "UR":
                                ocr_candidates.append({"status": decision, "label": evidence or "", "method": "ocr", "rect": None})
                            text += "\n" + ocr_text
                        except Exception as exc:  # noqa: BLE001 -- OCR failed on this page: noted, the page's text stands
                            warnings.append(f"Could not OCR {path.name}, page {number}.")
                            ocr_failed.append(number)
                            ocr_failures.append({"page": number, "reason": f"OCR failed: {type(exc).__name__}: {exc}"[:200]})
                    else:
                        warnings.append(f"OCR limit reached in {path.name}; some replies may need verification.")
                        ocr_skipped.append(number)
                # Every decision candidate the page gave -- its text, its marks,
                # its OCR -- settled together, once (M2 review 02, B).
                if found:
                    found, page_observations = settle_decision(found, marks, ocr_candidates, promote)
                    observations.extend({"page": number, **o} for o in page_observations)
                # A decision read off a sheet whose revision came from the
                # folder while the sheet prints another is a mark, not yet
                # that revision's status: kept beside the record.
                found = [_hold_decision(replace(row, status="UR"), row.status, row.reply_text, "sheet_mark", "decision_revision_unvalidated")
                         if row.status != "UR" and _revision_unvalidated(row) else row for row in found]
                # A cover of a discipline the platform does not track is a
                # record only on the evaluation path; otherwise an observation.
                if not promote:
                    held = [row for row in found if row.category == "drawings" and row.system_code is None]
                    if held:
                        observations.extend({"page": number, "kind": "cover_untracked", "record": observed_record(row)} for row in held)
                        found = [row for row in found if row not in held]
                if not found and not scan:
                    sheet = untracked_sheet_observation(text)
                    if sheet is not None:
                        observations.append({"page": number, **sheet})
                if found and pending is not None and all(r.category == "reply" for r in found) \
                        and _answers(records[pending], found[0]):
                    # The reply behind a submission, naming the sheets it covers:
                    # the consultant's word on that submission, folded into it.
                    reply = found[0]
                    if reply.status != "UR":
                        records[pending] = replace(records[pending], status=reply.status, reply_text=reply.reply_text,
                                                   page=number)
                    counted("replies_folded")
                    # The reply page stays a record of its own as well (M2): a
                    # contractor's reply sheet is a component of the file, and
                    # a page that answered nothing must not vanish from the
                    # reading. The register never lists a reply (combine drops
                    # them after using their decision), so nothing is displaced.
                    records.extend(found)
                elif found:
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
                        records[pending] = replace(records[pending], status=decision, reply_text=evidence, page=number)
                    observations.append({"page": number, "kind": "consultant_comments", "decision": decision, "answers_revision": said})
                elif not scan and records:
                    # A page behind the records that is none of the above: a
                    # datasheet, a certificate, a drawing sheet. Kept in the
                    # ledger as visited; nothing is made of it here.
                    pending = None
                else:
                    pending = None
            except OSError:
                raise
            except Exception as exc:  # noqa: BLE001 -- this page failed the reader: recorded; the pages before it stand
                if number in visited:
                    visited.remove(number)   # visited, skipped and failed are exclusive: this page failed
                failed.append({"page": number, "reason": f"{type(exc).__name__}: {exc}"[:200]})
                warnings.append(f"Page {number} of {path.name} could not be read.")
                pending = None
    except OSError as exc:
        # The file itself could not be read (online-only, gone): the note it
        # always left, and every page not yet visited is unavailable.
        warnings.extend(open_failure_notes(path, exc))
        skipped.extend({"page": n, "reason": "file unavailable"} for n in range(len(visited) + 1, total + 1) if n not in visited)
        stop_reason = "unavailable"
    if stop_reason == "unavailable":
        outcome = "unavailable"
    elif failed or ocr_failed:
        outcome = "partial"
    elif skipped or stop_reason or ocr_skipped:
        outcome = "bounded"
    else:
        outcome = "complete"
    # Two dimensions, each exclusive within itself (M2 review 02, D): a page
    # was visited, skipped (by reason) or failed (by reason), and never two
    # of these; OCR, attempted on some of the visited pages, either ran,
    # failed or was left out by the OCR budget -- listed under "ocr", not
    # among the pages' own outcomes (a page whose OCR failed was visited).
    coverage = {"outcome": outcome, "pages_total": total, "pages_visited": visited, "pages_skipped": skipped,
                "pages_failed": failed, "ocr_failed_pages": ocr_failed,
                "ocr": {"attempted": ocr_count, "failed": ocr_failures, "skipped_budget": ocr_skipped},
                "stop_reason": stop_reason, "parser_version": PARSER_VERSION, "promoted": bool(promote),
                "profile": extraction_profile(promote)}
    reading = Reading(tuple(records), tuple(dict.fromkeys(warnings)), coverage, observations)
    return reading if full else (reading.records, reading.notes)


def _sheet_numbers(reference: str) -> set[str]:
    """The sheet numbers a reference lists past its slash ("…/FA-100,101,104&105" -> 100, 101, 104, 105)."""
    _base, slash, sheets = reference.partition("/")
    return set(re.findall(r"\d{2,5}", sheets)) if slash else set()


def _answers(submission: ControlledDocument, reply: ControlledDocument) -> bool:
    """Whether a reply on the page after a submission's cover answers that
    submission: it names its number, one of the sheets it lists, or sheets
    of the same series under the submission's own base number. A reply
    naming another contractor's number, or another series, does not."""
    def norm(value: str) -> str:
        return " ".join(value.upper().split())

    names = {norm(submission.reference), *(norm(s) for s in submission.listed)}
    if norm(reply.reference) in names:
        return True
    base = reply.reference.split("/", 1)[0].upper().rstrip("-")
    if "/" not in reply.reference or not submission.reference.upper().startswith(base + "-"):
        return False
    listed = set().union(*(_sheet_numbers(s) for s in submission.listed)) if submission.listed else set()
    return not listed or bool(listed & _sheet_numbers(reply.reference))


# The notes `_read_pdf` leaves on a document, as File Sync reports them
# (document_sync.file_status): what kind of trouble each is, and what to say.
NOT_DOWNLOADED = "is not downloaded from OneDrive"
UNREADABLE = "Could not read "


def describe_note(note: str) -> tuple[str, str]:
    """(kind, reason) for a reading note. `kind` is "unavailable" (online
    only in OneDrive: nothing could be read), "failed" (the file could not
    be opened as a PDF) or "partial" (read, but not all of it)."""
    if NOT_DOWNLOADED in note:
        return "unavailable", "File is online-only in OneDrive and could not be processed."
    if note.startswith(UNREADABLE):
        return "failed", "The file could not be opened as a PDF (damaged or protected)."
    page = re.match(r"Could not OCR .*, page (\d+)\.$", note)
    if page:
        return "partial", f"Page {page[1]} could not be OCRed; a stamp on it may be unread."
    page = re.match(r"Page (\d+) of .* could not be read\.$", note)
    if page:
        return "partial", f"Page {page[1]} could not be read; the reading is partial and will be retried."
    if "only the first 12 pages were checked" in note:
        return "partial", "Only the first 12 pages were checked for consultant replies."
    if note.startswith("OCR limit reached"):
        return "partial", "Only the first 12 scanned pages were OCRed; some replies may need verification."
    return "partial", note


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
            named = reply.reference.upper() == row.reference.upper() \
                or " ".join(reply.reference.upper().split()) in {" ".join(s.upper().split()) for s in row.listed}
            if named and row.revision == reply.revision and row.status == "UR":
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
        # A schedule entry plans a drawing; it is not a revision of it, and
        # folding it into the drawing's history lost it. It stays its own
        # record, evidence on the drawing (app.services.shop_drawings).
        if row.category != "drawings" or row.source == "drawing schedule":
            continue
        key = drawing_key(row)
        history.setdefault(key, []).append(row)
        held = standing.get(key)
        if held is None or _revision_number(row.revision) > _revision_number(held.revision):
            standing[key] = row
    if standing:
        collapsed = []
        for row in rows:
            if row.category != "drawings" or row.source == "drawing schedule":
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
            earlier = [r for r in history[key] if r is not row and r.status not in ("UR", "SUPERSEDED")]
            # An earlier revision with no answer read off any of its files is
            # still that revision, submitted and answered -- the later one
            # proves it. It stays in the history, once, as SUPERSEDED (its
            # answer not found) with its own file, rather than dropping out
            # and leaving the log to call it "not submitted".
            answered = {_revision_number(r.revision) for r in earlier}
            unanswered: dict[int, ControlledDocument] = {}
            for r in history[key]:
                number = _revision_number(r.revision)
                if (r is row or r.status not in ("UR", "SUPERSEDED") or number in answered
                        or number >= _revision_number(row.revision)):
                    continue
                if number not in unanswered or r.modified > unanswered[number].modified:
                    unanswered[number] = replace(r, status="SUPERSEDED")
            earlier = sorted([*earlier, *unanswered.values()],
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
