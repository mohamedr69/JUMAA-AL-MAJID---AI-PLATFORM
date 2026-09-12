"""Content-based document control. File/folder names never establish approval."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import re
import io
from threading import RLock
import pymupdf
from app.services.submittal_scanner import ocr_available

_SCAN_LOCK = RLock()

def _ocr_page(page):
    import pytesseract
    from PIL import Image
    dpi = min(200, 2600 * 72 / max(page.rect.width, page.rect.height))
    pixels = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72))
    image = Image.open(io.BytesIO(pixels.tobytes("png")))
    return pytesseract.image_to_string(image, timeout=25)


REF = re.compile(r"\b[A-Z0-9]+(?:-[A-Z0-9]+)*-(MAS|SDW|DWG|SAR)-[A-Z0-9-]+", re.I)
REV = re.compile(r"\b(?:MAS\s+|SDW\s+|DWG\s+|SAR\s+)?REV(?:ISION)?\.?\s*[:.-]?\s*\n?\s*R?\s*(\d{1,3})\b", re.I)
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


def parse_page(text: str, path: str, modified: datetime, page: int) -> list[ControlledDocument]:
    # A drawing schedule defines required rows, not an approved drawing itself.
    if re.search(r"DWG\s*NO", text, re.I) and "FIRE ALARM" in text.upper() and "EML SUBMISSION" in text.upper():
        schedule = []
        for match in re.finditer(r"(?m)^\s*(FA\s*\d{3,})\s*\n\s*([^\n]+)", text, re.I):
            ref, title = match.groups()
            title = title.strip()
            codes = ["EML"] if "EMERGENCY SCHEMATIC" in title.upper() else ["FAS"] if "FIRE ALARM SCHEMATIC" in title.upper() else ["FAS", "EML"]
            for code in codes:
                schedule.append(ControlledDocument(code, title, path, modified, re.sub(r"\s+", " ", ref.strip()), "R0", "UR", floor_name(title), page=page, source="drawing schedule", category="drawings"))
        return schedule
    match = REF.search(text)
    drawing_match = DRAW_REF.search(text)
    if match:
        reference = match.group().rstrip("-.")
        category = {"MAS": "submittals", "SAR": "samples", "SDW": "drawings", "DWG": "drawings"}[match.group(1).upper()]
        # A catalogue quoting a submittal number is not a submission form.
        required = r"material[s]?\s+submittal|MAS\s+Reference" if category == "submittals" else r"sample\s+approval|SAR\s+Reference" if category == "samples" else r"drawing\s*(?:title|no|number|submittal)|shop\s*drawing"
        if not re.search(required, text, re.I) and not re.search(r"consultant.*(?:reply|comment|status)|review\s*status", text, re.I): return []
    elif drawing_match and re.search(r"FIRE\s*ALARM|EMERGENCY\s*LIGHT|VOICE\s*EVACUATION", text, re.I):
        reference, category = drawing_match.group(1).strip(), "drawings"
        if not TITLE.search(text): return []
    else:
        return []
    revision_match = REV.search(text)
    suffix = re.search(r"-R(\d+)$", reference, re.I)
    revision = f"R{int(revision_match.group(1) if revision_match else suffix.group(1) if suffix else 0)}"
    reference = re.sub(r"-R\d+$", "", reference, flags=re.I)
    title_match = re.search(r"(?:Material\s+Submittal\s+for|Sample\s+Approval\s+Request\s+for)\s+([^\n]+)", text, re.I) if category != "drawings" else TITLE.search(text)
    title = title_match.group(1).strip() if title_match else reference
    if category != "drawings" and title_match:
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
    if code is None and category == "drawings": return []
    if code is None: code = next((code for code, pattern in SYSTEMS if re.search(pattern, text, re.I)), None)
    decision, evidence = read_decision(text)
    return [ControlledDocument(code, title, path, modified, reference, revision, decision, floor_name(title), evidence, page, category=category)]


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
        with pymupdf.open(path) as pdf:
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
                # OCR title blocks of scanned pages and image stamps on forms.
                candidate = bool(found) or pending is not None or (len(text.strip()) < 80 and bool(re.search(r"approval|submittal|drawing|[/\\]MS[/\\]", filename, re.I)))
                if use_ocr and candidate and (page.get_images() or len(text.strip()) < 80):
                    if ocr_count < 12:
                        try:
                            ocr_count += 1
                            ocr_text = _ocr_page(page)
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
                    if not references and decision != "UR":
                        records[pending] = replace(records[pending], status=decision, reply_text=evidence, page=index + 1)
                else:
                    pending = None
    except Exception:
        warnings.append(f"Could not read {path.name}.")
    return tuple(records), tuple(dict.fromkeys(warnings))


def _scan_document_control(root: Path, use_ocr: bool = True, progress=None) -> tuple[list[ControlledDocument], list[str]]:
    enabled = use_ocr and ocr_available()
    warnings = [] if enabled or not use_ocr else ["OCR is unavailable. Image-only documents or consultant stamps may need verification; no approval is assumed."]
    found = {}
    paths = sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf")
    for index, path in enumerate(paths):
        try:
            stat = path.stat()
            records, notes = _read_pdf(str(path), stat.st_mtime_ns, stat.st_size, enabled)
            warnings.extend(notes)
            for row in records:
                row = replace(row, path=path.relative_to(root).as_posix())
                key = (row.category, row.system_code, row.reference.upper(), row.revision)
                old = found.get(key)
                rank = lambda r: (r.status != "UR", r.source == "document", r.modified)
                if old is None or rank(row) > rank(old): found[key] = row
        except OSError:
            warnings.append(f"Could not access {path.name}.")
        if progress:
            progress(list(found.values()), list(dict.fromkeys(warnings)), index + 1, len(paths))
    # Match an issued drawing to a unique scheduled floor in the same system.
    # Keep its actual drawing reference, while retaining a stable register row.
    rows = list(found.values())
    schedules = [r for r in rows if r.source == "drawing schedule"]
    for i, row in enumerate(rows):
        if row.category != "drawings" or row.source != "document" or not row.floor: continue
        candidates = [s for s in schedules if s.system_code == row.system_code and s.floor and normalize_floor(s.floor) == normalize_floor(row.floor)]
        if len(candidates) == 1:
            rows[i] = replace(row, group_reference=candidates[0].reference)
    return sorted(rows, key=lambda row: (row.category, row.system_code or "", row.reference, row.revision)), list(dict.fromkeys(warnings))


def scan_document_control(root: Path, use_ocr: bool = True, progress=None) -> tuple[list[ControlledDocument], list[str]]:
    # Avoid duplicate OCR work from concurrent refresh requests.
    with _SCAN_LOCK:
        return _scan_document_control(root, use_ocr, progress)
