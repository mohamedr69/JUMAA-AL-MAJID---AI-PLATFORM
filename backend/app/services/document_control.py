"""Content-based document control. File/folder names never establish approval."""
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
import os
import re
import io
from threading import RLock
import pymupdf
from app.services.shop_drawing import normalize_floor as normalize_drawing_floor
from app.services.shop_drawing import read_title_block, system_of
from app.services.submittal_scanner import ocr_available

_SCAN_LOCK = RLock()

# Windows refuses paths beyond 260 characters unless they are asked for by
# their extended name, and project archives nest deeply enough to hit it.
LONG_PATH = chr(92) * 2 + "?" + chr(92)


def extended(path: str) -> str:
    """The name Windows accepts for a file however deeply it is filed."""
    if path.startswith(LONG_PATH):
        return path
    full = os.path.abspath(path)
    return LONG_PATH + full if len(full) > 230 else full


def open_pdf(path: str):
    return pymupdf.open(extended(path))

def _ocr_page(page):
    import pytesseract
    from PIL import Image
    dpi = min(200, 2600 * 72 / max(page.rect.width, page.rect.height))
    pixels = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72))
    image = Image.open(io.BytesIO(pixels.tobytes("png")))
    return pytesseract.image_to_string(image, timeout=25)


REF = re.compile(r"\b[A-Z0-9]+(?:-[A-Z0-9]+)*-(MAS|SDW|DWG|SAR)-[A-Z0-9-]+", re.I)
REV = re.compile(r"\b(?:MAS\s+|SDW\s+|DWG\s+|SAR\s+)?REV(?:ISION)?\.?\s*[:.-]?\s*\n?\s*R?\s*(\d{1,3})\b", re.I)
TRANSMITTAL = re.compile(r"shop\s+drawings?\s+submittal\s+form|SDW\s+Reference", re.I)
DRAW_REF = re.compile(r"(?:DRAWING\s*(?:NO\.?|NUMBER)|DWG\s*NO\.?)\s*:?\s*\n?\s*([A-Z0-9][A-Z0-9 /-]{3,70})", re.I)
TITLE = re.compile(r"(?:DRAWING\s*TITLE|TITLE)\s*:?\s*\n?\s*([^\n]+)", re.I)
SYSTEMS = [("FRC", r"fire[ -]*(?:rated|resistant)\s*cable"), ("EML", r"emergency\s*light|monitored\s*self[ -]*contained"), ("FAS", r"fire\s*alarm|voice\s*evacuation|fire\s*telephone")]

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
    issued: date | None = None
    note: str | None = None


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


def floor_name(title: str) -> str | None:
    for pattern in [r"TYPICAL\s+.*?FLOOR", r"BASEMENT[ -]*\d+", r"PODIUM[ -]*\d+", r"(?:\d+(?:ST|ND|RD|TH)\s+|GROUND\s+|.*?ROOF\s+)FLOOR", r"UNDER\s*GROUND", r"\b(?:B\d+|L\d+|GF|RF)\b"]:
        found = re.search(pattern, title, re.I)
        if found: return found.group().strip()
    return title if "FLOOR" in title.upper() else None


def form_type(text: str) -> str | None:
    """Which submission form a page is, by the form's own heading."""
    if re.search(r"sample\s+approval\s+form|SAF\s+Reference|sample\s+approval\s+request\s+for", text, re.I):
        return "samples"
    if re.search(r"materials?\s+submittal\s+form|MAS\s+Reference|material\s+submittal\s+for", text, re.I):
        return "submittals"
    return None


def parse_page(text: str, path: str, modified: datetime, page: int, document_type: str | None = None) -> list[ControlledDocument]:
    # A schedule lists the drawings a project owes; the log lists the sheets
    # that exist. Only a drawing read from its own title block is a drawing.
    match = REF.search(text)
    if not match:
        return []
    kind = match.group(1).upper()
    if kind not in ("MAS", "SAR"):
        # Drawings are logged from their own title block, never from a page
        # that merely quotes a drawing number.
        return []
    reference = match.group().rstrip("-.")
    category = "submittals" if kind == "MAS" else "samples"
    # The page has to be the form itself. A catalogue quoting a submittal
    # number, or a reply to comments carrying it, is not a submission; and a
    # form bound inside another submission stays part of that submission.
    page_type = form_type(text)
    if page_type != category or (document_type is not None and document_type != category):
        return []
    revision_match = REV.search(text)
    suffix = re.search(r"-R(\d+)$", reference, re.I)
    revision = f"R{int(revision_match.group(1) if revision_match else suffix.group(1) if suffix else 0)}"
    reference = re.sub(r"-R\d+$", "", reference, flags=re.I)
    title_match = re.search(r"(?:Material\s+Submittal\s+for|Sample\s+Approval\s+Request\s+for)\s+([^\n]+)", text, re.I)
    # Without the form's own subject line there is nothing to verify a title
    # against, and the reference alone is not a description of anything.
    if not title_match:
        return []
    title = title_match.group(1).strip()
    following = text[title_match.end():].splitlines()
    for continuation in following[:4]:
        continuation = continuation.strip()
        if not continuation: continue
        if re.match(r"^(?:\d+|EL\b|M/S|MSF|MAIN|Discipline|Remarks|Pages|SPECS)", continuation, re.I): break
        title += " " + continuation
    if re.search(r"\bcables?\b", title, re.I): code_hint = "FRC"
    else: code_hint = None
    code = code_hint or next((code for code, pattern in SYSTEMS if re.search(pattern, title, re.I)), None)
    if code is None:
        code = "FAS" if re.search(r"-(?:FA|FAS|VE|FT)-", reference, re.I) else "EML" if re.search(r"-(?:LI|ELM|EML)-", reference, re.I) else "FRC" if re.search(r"-FRC-", reference, re.I) else None
    if code is None: code = next((code for code, pattern in SYSTEMS if re.search(pattern, text, re.I)), None)
    decision, evidence = read_decision(text)
    return [ControlledDocument(code, title, path, modified, reference, revision, decision, floor_name(title), evidence, page, category=category)]


def ocr_images(pdf, page, limit: int = 10) -> str:
    """Read the stamps and notes pasted onto a sheet as pictures."""
    import pytesseract
    from PIL import Image

    text = []
    for info in page.get_images(full=True)[:limit]:
        try:
            pixels = pymupdf.Pixmap(pdf, info[0])
            if pixels.width < 150 or pixels.width * pixels.height < 40000:
                continue
            if pixels.n > 4:
                pixels = pymupdf.Pixmap(pymupdf.csRGB, pixels)
            image = Image.open(io.BytesIO(pixels.tobytes("png")))
            text.append(pytesseract.image_to_string(image, timeout=25))
        except Exception:  # noqa: BLE001
            continue
    return "\n".join(text)


def read_drawing(pdf, page, text: str, path: str, modified: datetime, number: int, use_ocr: bool) -> ControlledDocument | None:
    """A sheet is logged only when its own title block says what it is."""
    block = read_title_block(page)
    if block is None:
        return None
    code = system_of(block)
    if code is None:
        # Another trade's sheet filed with ours.
        return None
    decision, evidence = read_decision(text)
    if decision == "UR" and use_ocr:
        stamped = ocr_images(pdf, page)
        if stamped.strip():
            decision, evidence = read_decision(stamped)
    return ControlledDocument(
        code, block.title, path, modified, block.number, block.revision, decision,
        block.floor, evidence, number, source="shop drawing", category="drawings",
        issued=block.issued, note=block.note,
    )


def normalize_floor(value: str) -> str:
    value = value.upper().strip()
    value = re.sub(r"BASEMENT[ -]*(\d+)", r"B\1", value)
    value = re.sub(r"PODIUM[ -]*(\d+)", r"P\1", value)
    value = re.sub(r"(\d+)(?:ST|ND|RD|TH)?\s+FLOOR", r"L\1", value)
    value = value.replace("GROUND FLOOR", "GF")
    return re.sub(r"[ -]+", "", value)


@lru_cache(maxsize=1024)
def _read_pdf(filename: str, stamp: int, size: int, use_ocr: bool) -> tuple[tuple[ControlledDocument, ...], tuple[str, ...]]:
    records, warnings = [], []
    path = Path(filename)
    modified = datetime.fromtimestamp(stamp / 1e9, timezone.utc)
    try:
        with open_pdf(filename) as pdf:
            pending = None
            ocr_count = 0
            document_type = None
            transmittal = False
            package = None
            for index, page in enumerate(pdf):
                text = page.get_text()
                if index == 0:
                    transmittal = bool(TRANSMITTAL.search(text))
                # A catalogue/specification is not a register. Inspect its cover,
                # but do not OCR or read every product page looking for approvals.
                # A submission form is different: what it submits is bound behind it.
                if index >= (12 if document_type or transmittal else 3) and not records and pending is None:
                    break
                if index >= 12 and records and all(row.category != "drawings" for row in records):
                    warnings.append(f"{path.name}: only the first 12 pages were checked for submission replies.")
                    break
                drawing = read_drawing(pdf, page, text, filename, modified, index + 1, use_ocr)
                if drawing is not None:
                    records.append(drawing)
                    pending = None
                    continue
                # The reply stamped on a drawing submission form covers the
                # sheets submitted with it.
                if transmittal and package is None:
                    decision, evidence = read_decision(text)
                    if decision == "UR" and use_ocr and ocr_count < 12:
                        ocr_count += 1
                        decision, evidence = read_decision(ocr_images(pdf, page))
                    if decision != "UR":
                        package = (decision, evidence)
                # The first form heading in a file says what the file is; a
                # form bound in behind it is backup, not a second submission.
                document_type = document_type or form_type(text)
                found = parse_page(text, filename, modified, index + 1, document_type)
                # OCR title blocks of scanned pages and image stamps on forms.
                candidate = bool(found) or pending is not None or (len(text.strip()) < 80 and bool(re.search(r"approval|submittal|drawing|[/\\]MS[/\\]", filename, re.I)))
                if use_ocr and candidate and (page.get_images() or len(text.strip()) < 80):
                    if ocr_count < 12:
                        try:
                            ocr_count += 1
                            ocr_text = _ocr_page(page)
                            document_type = document_type or form_type(ocr_text)
                            ocr_found = parse_page(ocr_text, filename, modified, index + 1, document_type)
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
                    if not references and decision != "UR":
                        records[pending] = replace(records[pending], status=decision, reply_text=evidence, page=index + 1)
                else:
                    pending = None
            if package:
                records = [
                    replace(row, status=package[0], reply_text=row.reply_text or package[1])
                    if row.category == "drawings" and row.status == "UR" else row
                    for row in records
                ]
    except Exception:
        warnings.append(f"Could not read {path.name}.")
    return tuple(records), tuple(dict.fromkeys(warnings))


def pdf_paths(root: Path) -> list[tuple[str, str]]:
    """Every PDF under the project, deep folders and long names included."""
    base = LONG_PATH + os.path.abspath(root)
    found = []
    for folder, _, names in os.walk(base):
        for name in names:
            if name.lower().endswith(".pdf"):
                full = os.path.join(folder, name)
                found.append((full, os.path.relpath(full, base).replace(chr(92), "/")))
    return found


def floor_key(row: ControlledDocument) -> str:
    return normalize_drawing_floor(row.floor) if row.floor else row.reference.upper()


def register(rows: list[ControlledDocument]) -> list[ControlledDocument]:
    """One row per floor per system, at the revision last issued for it.

    A floor is drawn once. The same sheet is kept in as many folders as the
    office needs, and reissued as the design moves, so the log follows the
    sheet the title block dates last and carries the earlier revisions behind
    it as that floor's history.
    """
    drawings = [row for row in rows if row.category == "drawings" and row.source == "shop drawing"]
    groups: dict[tuple[str | None, str], list[ControlledDocument]] = {}
    for row in drawings:
        groups.setdefault((row.system_code, floor_key(row)), []).append(row)
    issued = []
    for entries in groups.values():
        latest = max(entries, key=lambda row: (row.issued or date.min, row.revision, row.modified))
        for row in entries:
            issued.append(replace(row, group_reference=latest.reference, name=latest.name))
    rest = []
    for row in rows:
        if row.category == "drawings" and row.source == "shop drawing":
            continue
        rest.append(row)
    return sorted(issued + rest, key=lambda row: (row.category, row.system_code or "", row.reference, row.revision))


def _scan_document_control(root: Path, use_ocr: bool = True, progress=None) -> tuple[list[ControlledDocument], list[str]]:
    enabled = use_ocr and ocr_available()
    warnings = [] if enabled or not use_ocr else ["OCR is unavailable. Image-only documents or consultant stamps may need verification; no approval is assumed."]
    found = {}
    paths = sorted(pdf_paths(root))
    for index, (name, relative) in enumerate(paths):
        try:
            stat = os.stat(extended(name))
            records, notes = _read_pdf(name, stat.st_mtime_ns, stat.st_size, enabled)
            warnings.extend(notes)
            for row in records:
                row = replace(row, path=relative)
                key = (row.category, row.system_code, floor_key(row) if row.source == "shop drawing" else row.reference.upper(), row.revision)
                old = found.get(key)
                rank = lambda r: (r.status != "UR", r.source in ("document", "shop drawing"), r.issued or date.min, r.modified)
                if old is None or rank(row) > rank(old): found[key] = row
        except OSError:
            warnings.append(f"Could not access {os.path.basename(name)}.")
        if progress:
            progress(register(list(found.values())), list(dict.fromkeys(warnings)), index + 1, len(paths))
    return register(list(found.values())), list(dict.fromkeys(warnings))


def scan_document_control(root: Path, use_ocr: bool = True, progress=None) -> tuple[list[ControlledDocument], list[str]]:
    # Avoid duplicate OCR work from concurrent refresh requests.
    with _SCAN_LOCK:
        return _scan_document_control(root, use_ocr, progress)
