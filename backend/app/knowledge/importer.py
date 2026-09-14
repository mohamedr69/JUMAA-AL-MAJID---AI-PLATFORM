"""Importing the Compliance Response Database into the application database.

The source collection is a folder synced from OneDrive. It holds ONE file of
record -- `Compliance_Response_Database.xlsx`, whose sheets are the tables
(SOURCES, REQUIREMENTS, SPECIFICATION_MAPPINGS, RESPONSES, RESPONSE_SOURCES,
MERGE_REVIEW, REVIEW_ISSUES) -- and around it: `index.json` / `INDEX.md`
(indexes of the same records), `knowledge/**/*.md` and `agent_bundle/`
(the same records exported as Markdown, for reading), `_work/` (the
extraction's intermediate files) and the assistant's own instructions. Only
the workbook is imported; the rest is inventoried and reported so that
nothing is imported twice from equivalent exports.

The workbook's SHA-1 decides whether there is anything to do. An import
reads the whole workbook, checks its structure, and writes every table in
one transaction: a failure leaves the last usable knowledge untouched.
Records a later workbook no longer carries are marked inactive, never
deleted, and an engineer's validation of an equivalence survives the
re-import of the proposal it validated. No model is called at any point.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, insert, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.database import SessionLocal
from app.models import (
    KnowledgeEquivalence,
    KnowledgeFile,
    KnowledgeImport,
    KnowledgeIssue,
    KnowledgeMapping,
    KnowledgeModel,
    KnowledgeRequirement,
    KnowledgeResponse,
    KnowledgeResponseSource,
    KnowledgeSource,
)

from . import policy
from .normalize import normalize_model, normalize_requirement, requirement_hash, split_models

CANONICAL_WORKBOOK = "Compliance_Response_Database.xlsx"
SHEETS: dict[str, tuple[str, ...]] = {
    "SOURCES": ("source_id", "filename", "onedrive_link_or_id", "duplicate_copies", "project", "job_number", "client",
                "system", "manufacturer", "brand", "specification_family", "section_numbers", "document_revision",
                "document_date", "document_review_status", "extraction_status", "sha1"),
    "REQUIREMENTS": ("requirement_id", "system", "subsystem", "topic", "canonical_requirement_text", "technical_variant",
                     "merge_review_status", "numbers", "n_sources"),
    "SPECIFICATION_MAPPINGS": ("mapping_id", "requirement_id", "specification_family", "specification_title",
                               "specification_edition", "section_number", "clause_number", "clause_label_as_printed",
                               "heading_context", "original_requirement_text", "source_id", "pdf_page", "printed_page",
                               "extraction_method", "pairing_confidence"),
    "RESPONSES": ("response_id", "requirement_id", "system", "manufacturer", "brand", "model_configuration",
                  "exact_historical_response", "remarks_column", "original_compliance_status", "scope_conditions",
                  "responsible_party", "cited_supporting_references", "review_flag", "specification_families", "n_sources"),
    "RESPONSE_SOURCES": ("response_id", "mapping_id", "source_id", "pdf_page", "historical_review_status",
                         "consultant_comment", "superseded_status"),
    "MERGE_REVIEW": ("requirement_id_a", "requirement_id_b", "similarity", "proposal"),
    "REVIEW_ISSUES": ("issue_type", "record_id", "detail"),
}
CHUNK = 2000
# Windows: the file is a OneDrive placeholder whose bytes are not on disk.
_ONLINE_ONLY = 0x400000 | 0x1000


class ImportError_(Exception):
    pass


# --- the source collection ------------------------------------------------------------


@dataclass
class SourceFile:
    relative_path: str
    role: str
    size: int | None = None
    mtime: float | None = None
    online_only: bool = False


@dataclass
class Inspection:
    root: Path | None
    label: str
    reachable: bool
    files: list[SourceFile] = field(default_factory=list)
    problem: str | None = None

    @property
    def workbook(self) -> SourceFile | None:
        return next((f for f in self.files if f.role == "canonical"), None)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for f in self.files:
            out[f.role] += 1
        return dict(out)


def source_root() -> Path | None:
    settings = get_settings()
    configured = (settings.compliance_knowledge_source or "").strip()
    if configured:
        return Path(os.path.expandvars(configured)).expanduser()
    if settings.compliance_knowledge_autodetect:
        from app.core.config import REPO_DIR

        default = REPO_DIR / "data base"
        if (default / CANONICAL_WORKBOOK).is_file():
            return default
    return None


def import_on_start() -> bool:
    """Import the knowledge base in the background when this database has
    none yet and the source is here -- a new machine's first start. Returns
    whether an import was started."""
    if not get_settings().compliance_knowledge_import_on_start or source_root() is None:
        return False
    if last_import_id() is not None:
        return False
    return start_import()


def _role(relative: str) -> str:
    parts = relative.replace("\\", "/").split("/")
    name = parts[-1]
    if relative == CANONICAL_WORKBOOK:
        return "canonical"
    if parts[0] == "_work":
        return "intermediate"
    if parts[0] == ".claude":
        return "agent"
    if name in ("index.json", "INDEX.md", "01_CATALOG.md"):
        return "index"
    if parts[0] in ("knowledge", "agent_bundle") and name.endswith(".md") and "knowledge" in parts:
        return "duplicate_export"
    if name == "agent_bundle.zip":
        return "duplicate_export"
    if name.endswith(".md"):
        return "documentation"
    return "other"


def inspect_source(root: Path | None = None) -> Inspection:
    """What the source folder holds, each file given its role. Reads names
    and sizes only -- no file is opened."""
    root = root if root is not None else source_root()
    if root is None:
        return Inspection(None, "", False, problem="COMPLIANCE_KNOWLEDGE_SOURCE is not set on this server.")
    if not root.is_dir():
        return Inspection(root, root.name, False, problem=f"The source folder is not reachable ({root.name}).")
    files: list[SourceFile] = []
    for folder, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(names):
            path = Path(folder) / name
            relative = str(path.relative_to(root))
            try:
                stat = path.stat()
                attributes = getattr(stat, "st_file_attributes", 0)
                files.append(SourceFile(relative, _role(relative), stat.st_size, stat.st_mtime,
                                        bool(attributes & _ONLINE_ONLY) and stat.st_size > 0))
            except OSError:
                files.append(SourceFile(relative, _role(relative)))
    return Inspection(root, root.name, True, files)


def file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --- reading the workbook ------------------------------------------------------------


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def read_workbook(path: Path, progress=None) -> dict[str, list[dict]]:
    """Every sheet as a list of dicts, after checking that the columns the
    import relies on are there."""
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        missing = [s for s in SHEETS if s not in workbook.sheetnames]
        if missing:
            raise ImportError_(f"The workbook has no sheet {', '.join(missing)}")
        tables: dict[str, list[dict]] = {}
        for sheet, required in SHEETS.items():
            rows = workbook[sheet].iter_rows(values_only=True)
            header = [str(h).strip() if h is not None else "" for h in next(rows, ())]
            absent = [c for c in required if c not in header]
            if absent:
                raise ImportError_(f"Sheet {sheet} lacks the column(s) {', '.join(absent)}")
            table: list[dict] = []
            for row in rows:
                if not any(v is not None for v in row):
                    continue
                table.append({header[i]: _clean(v) for i, v in enumerate(row) if i < len(header) and header[i]})
                if progress and len(table) % 5000 == 0:
                    progress(sheet, len(table))
            tables[sheet] = table
            if progress:
                progress(sheet, len(table))
        built = None
        if "README" in workbook.sheetnames:
            for row in workbook["README"].iter_rows(values_only=True, max_row=30):
                text = " ".join(str(v) for v in row if v is not None)
                if "Built" in text:
                    built = text[:32]
                    break
        tables["_built"] = [{"built": built}]
        return tables
    finally:
        workbook.close()


# --- the import ------------------------------------------------------------------------


@dataclass
class ImportState:
    running: bool = False
    import_id: int | None = None
    phase: str | None = None
    detail: str | None = None
    started_at: float | None = None


_state = ImportState()
_lock = threading.Lock()


def state() -> dict:
    with _lock:
        return dict(_state.__dict__)


def _set(**values) -> None:
    with _lock:
        _state.__dict__.update(values)


def _chunks(items: list, size: int = CHUNK):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _replace(db: Session, model, key_column, rows: list[dict], existing: set[str]) -> tuple[int, int]:
    """Delete the incoming ids and insert the rows, in chunks. Returns
    (added, updated) by whether the id existed before."""
    ids = [row[key_column.name] for row in rows]
    for chunk in _chunks(ids):
        db.execute(delete(model).where(key_column.in_(chunk)))
    for chunk in _chunks(rows):
        db.execute(insert(model), chunk)
    updated = sum(1 for i in ids if i in existing)
    return len(ids) - updated, updated


def _deactivate(db: Session, model, import_id: int) -> int:
    result = db.execute(update(model).where(model.import_id != import_id, model.active.is_(True)).values(active=False))
    return result.rowcount or 0


def run_import(db: Session, *, user_id: int | None = None, force: bool = False, root: Path | None = None) -> KnowledgeImport:
    """Read the source and write the knowledge base. Runs in the caller's
    thread; `start_import` runs it in the background."""
    record = KnowledgeImport(status="running", started_by_id=user_id)
    db.add(record)
    db.commit()
    _set(running=True, import_id=record.id, phase="inspecting", detail=None, started_at=time.time())
    try:
        inspection = inspect_source(root)
        record.source_label = inspection.label
        report: dict = {"files": [], "counts": inspection.counts(), "errors": []}
        if not inspection.reachable:
            raise ImportError_(inspection.problem or "The source folder is not reachable.")
        workbook = inspection.workbook
        if workbook is None:
            raise ImportError_(f"{CANONICAL_WORKBOOK} was not found in the source folder.")
        if workbook.online_only:
            raise ImportError_(f"{CANONICAL_WORKBOOK} is an online-only OneDrive placeholder: open the folder in "
                               "File Explorer and choose 'Always keep on this device' for it, then update again.")
        record.files_discovered = len(inspection.files)
        for f in inspection.files:
            if f.role == "canonical":
                continue
            note = {
                "index": "an index of the workbook's records",
                "duplicate_export": "the workbook's records exported as Markdown",
                "intermediate": "extraction working file",
                "documentation": "documentation",
                "agent": "assistant configuration",
                "other": "not a knowledge file",
            }[f.role]
            report["files"].append({"path": f.relative_path, "role": f.role, "action": "skipped", "note": note})
        record.files_skipped = len(report["files"])

        _set(phase="hashing")
        path = inspection.root / workbook.relative_path
        digest = file_sha1(path)
        record.workbook_hash = digest
        last = db.execute(
            select(KnowledgeImport).where(KnowledgeImport.status == "succeeded").order_by(KnowledgeImport.id.desc())
        ).scalars().first()
        if last is not None and last.workbook_hash == digest and not force:
            report["files"].insert(0, {"path": workbook.relative_path, "role": "canonical", "action": "unchanged",
                                       "note": f"same content as import #{last.id}"})
            record.files_unchanged = 1
            record.status = "unchanged"
            record.report = report
            _register_files(db, record, inspection, digest)
            return _finish(db, record)

        _set(phase="reading")
        tables = read_workbook(path, progress=lambda sheet, n: _set(detail=f"{sheet}: {n:,} rows"))
        record.workbook_built = (tables.pop("_built")[0] or {}).get("built")

        _set(phase="writing", detail=None)
        counts = _write(db, record.id, tables)
        for key, value in counts.items():
            setattr(record, key, value)
        report["files"].insert(0, {"path": workbook.relative_path, "role": "canonical", "action": "imported",
                                   "note": ", ".join(f"{s} {len(tables[s]):,}" for s in SHEETS)})
        report["counts"]["rows"] = {s: len(tables[s]) for s in SHEETS}
        record.files_imported = 1
        record.status = "succeeded"
        record.report = report
        _register_files(db, record, inspection, digest)
        return _finish(db, record)
    except Exception as exc:  # noqa: BLE001 -- whatever failed, the record says so and the tables stand
        db.rollback()
        record = db.get(KnowledgeImport, record.id)
        record.status = "failed"
        record.error = f"{type(exc).__name__}: {exc}"[:2000]
        record.report = {**(record.report or {}), "errors": [record.error]}
        record.finished_at = utc_now()
        db.commit()
        return record
    finally:
        _set(running=False, phase=None, detail=None)
        from . import autofill  # the in-memory match index is built from what is now in the tables

        autofill.invalidate()


def _finish(db: Session, record: KnowledgeImport) -> KnowledgeImport:
    record.finished_at = utc_now()
    db.commit()
    db.refresh(record)
    return record


def _register_files(db: Session, record: KnowledgeImport, inspection: Inspection, workbook_hash: str) -> None:
    """What was seen this time, one row per file, for the next import's
    comparison and the status page."""
    db.execute(delete(KnowledgeFile))
    rows = []
    for f in inspection.files:
        status = "imported" if f.role == "canonical" and record.status == "succeeded" else \
            "unchanged" if f.role == "canonical" else "online_only" if f.online_only else "skipped"
        rows.append({"relative_path": f.relative_path[:512], "role": f.role,
                     "sha1": workbook_hash if f.role == "canonical" else None, "size": f.size, "mtime": f.mtime,
                     "status": status, "note": None, "last_import_id": record.id})
    for chunk in _chunks(rows):
        db.execute(insert(KnowledgeFile), chunk)


def _write(db: Session, import_id: int, tables: dict[str, list[dict]]) -> dict[str, int]:
    """Every table, one transaction. Rows are shaped here from the sheets;
    eligibility is decided here too, from the linked mappings and issues."""
    existing = {
        "sources": set(db.execute(select(KnowledgeSource.source_id)).scalars()),
        "requirements": set(db.execute(select(KnowledgeRequirement.requirement_id)).scalars()),
        "mappings": set(db.execute(select(KnowledgeMapping.mapping_id)).scalars()),
        "responses": set(db.execute(select(KnowledgeResponse.response_id)).scalars()),
    }
    added = updated = 0

    issues_by_record: dict[str, set[str]] = defaultdict(set)
    for row in tables["REVIEW_ISSUES"]:
        if row.get("record_id") and row.get("issue_type"):
            issues_by_record[row["record_id"]].add(row["issue_type"])

    # SOURCES
    source_ids = set()
    rows = []
    for r in tables["SOURCES"]:
        if not r.get("source_id"):
            continue
        source_ids.add(r["source_id"])
        issues = sorted(issues_by_record.get(r["source_id"], ()))
        rows.append({
            "source_id": r["source_id"], "filename": (r.get("filename") or "")[:255], "source_path": r.get("onedrive_link_or_id"),
            "file_hash": r.get("sha1"), "duplicate_copies": r.get("duplicate_copies"), "project": (r.get("project") or None),
            "job_number": (r.get("job_number") or None), "client": r.get("client"), "system": r.get("system"),
            "manufacturer": r.get("manufacturer"), "brand": r.get("brand"), "specification_family": r.get("specification_family"),
            "section_numbers": (r.get("section_numbers") or None), "document_revision": r.get("document_revision"),
            "document_date": r.get("document_date"), "document_review_status": r.get("document_review_status"),
            "extraction_status": r.get("extraction_status"),
            "source_review_status": "; ".join(issues) if issues else None, "import_id": import_id, "active": True,
        })
    a, u = _replace(db, KnowledgeSource, KnowledgeSource.source_id, rows, existing["sources"])
    added += a
    updated += u

    # REQUIREMENTS
    requirement_chars: dict[str, int] = {}
    rows = []
    for r in tables["REQUIREMENTS"]:
        text = r.get("canonical_requirement_text") or ""
        if not r.get("requirement_id"):
            continue
        requirement_chars[r["requirement_id"]] = len(text)
        rows.append({
            "requirement_id": r["requirement_id"], "system": r.get("system"), "subsystem": r.get("subsystem"),
            "topic": r.get("topic"), "exact_requirement_text": text, "normalized_requirement_text": normalize_requirement(text),
            "requirement_hash": requirement_hash(text), "merge_review_status": r.get("merge_review_status"),
            "technical_variant": r.get("technical_variant"), "numbers": r.get("numbers"), "n_sources": _int(r.get("n_sources")),
            "import_id": import_id, "active": True,
        })
    a, u = _replace(db, KnowledgeRequirement, KnowledgeRequirement.requirement_id, rows, existing["requirements"])
    added += a
    updated += u
    requirement_ids = set(requirement_chars)

    # SPECIFICATION_MAPPINGS
    mapping_quality: dict[str, tuple[str | None, str | None]] = {}
    rows = []
    for r in tables["SPECIFICATION_MAPPINGS"]:
        if not r.get("mapping_id") or r.get("requirement_id") not in requirement_ids or r.get("source_id") not in source_ids:
            continue
        mapping_quality[r["mapping_id"]] = (r.get("pairing_confidence"), r.get("extraction_method"))
        rows.append({
            "mapping_id": r["mapping_id"], "requirement_id": r["requirement_id"], "source_id": r["source_id"],
            "specification_family": r.get("specification_family"), "specification_title": (r.get("specification_title") or "")[:255] or None,
            "specification_edition": r.get("specification_edition"), "section_number": r.get("section_number"),
            "clause_number": (r.get("clause_number") or "")[:32] or None, "clause_label": (r.get("clause_label_as_printed") or "")[:32] or None,
            "heading_context": r.get("heading_context"), "original_requirement_text": r.get("original_requirement_text"),
            "pdf_page": r.get("pdf_page"), "printed_page": r.get("printed_page"), "extraction_method": r.get("extraction_method"),
            "pairing_confidence": r.get("pairing_confidence"), "import_id": import_id, "active": True,
        })
    a, u = _replace(db, KnowledgeMapping, KnowledgeMapping.mapping_id, rows, existing["mappings"])
    added += a
    updated += u

    # RESPONSE_SOURCES -- read first: eligibility needs the links.
    links: dict[str, list[tuple[str | None, str | None, bool]]] = defaultdict(list)
    sources_of: dict[str, set[str]] = defaultdict(set)
    link_rows = []
    for r in tables["RESPONSE_SOURCES"]:
        if not r.get("response_id") or r.get("source_id") not in source_ids:
            continue
        superseded = bool(r.get("superseded_status"))
        conf, method = mapping_quality.get(r.get("mapping_id") or "", (None, None))
        links[r["response_id"]].append((conf, method, superseded))
        if not superseded:
            sources_of[r["response_id"]].add(r["source_id"])
        link_rows.append({
            "response_id": r["response_id"], "mapping_id": r.get("mapping_id"), "source_id": r["source_id"],
            "pdf_page": r.get("pdf_page"), "historical_review_status": r.get("historical_review_status"),
            "consultant_comment": r.get("consultant_comment"), "superseded_status": r.get("superseded_status"),
            "import_id": import_id, "active": True,
        })

    # RESPONSES
    rows = []
    model_rows = []
    flagged = 0
    for r in tables["RESPONSES"]:
        if not r.get("response_id") or r.get("requirement_id") not in requirement_ids:
            continue
        response_links = links.get(r["response_id"], [])
        issue_types = issues_by_record.get(r["response_id"], set()) | issues_by_record.get(r["requirement_id"], set())
        for source_id in sources_of.get(r["response_id"], ()):
            issue_types |= {t for t in issues_by_record.get(source_id, ()) if t.startswith(policy.SOURCE_BLOCKING_ISSUES)}
        reasons = policy.eligibility_reasons(
            review_flag=r.get("review_flag"), status=r.get("original_compliance_status"), manufacturer=r.get("manufacturer"),
            requirement_chars=requirement_chars.get(r["requirement_id"], 0), links=response_links, issue_types=issue_types,
        )
        if reasons:
            flagged += 1
        rows.append({
            "response_id": r["response_id"], "requirement_id": r["requirement_id"], "system": r.get("system"),
            "manufacturer": r.get("manufacturer"), "brand": r.get("brand"), "applicable_models": r.get("model_configuration"),
            "scope_conditions": r.get("scope_conditions"), "responsible_party": (r.get("responsible_party") or "")[:128] or None,
            "historical_response": r.get("exact_historical_response") or "", "remarks": r.get("remarks_column"),
            "historical_compliance_status": r.get("original_compliance_status"), "cited_references": r.get("cited_supporting_references"),
            "review_flag": r.get("review_flag"), "specification_families": (r.get("specification_families") or "")[:255] or None,
            "n_sources": _int(r.get("n_sources")),
            "superseded": bool(response_links) and all(s for _, _, s in response_links),
            "autofill_eligibility": "blocked" if reasons else "eligible", "eligibility_reasons": "; ".join(reasons) or None,
            "import_id": import_id, "active": True,
        })
        for model in split_models(r.get("model_configuration")):
            model_rows.append({"response_id": r["response_id"], "model": normalize_model(model)[:64]})
    a, u = _replace(db, KnowledgeResponse, KnowledgeResponse.response_id, rows, existing["responses"])
    added += a
    updated += u
    response_ids = {row["response_id"] for row in rows}

    db.execute(delete(KnowledgeModel).where(KnowledgeModel.response_id.in_(list(response_ids))) if len(response_ids) < 900
               else delete(KnowledgeModel))
    for chunk in _chunks(model_rows):
        db.execute(insert(KnowledgeModel), chunk)

    link_rows = [row for row in link_rows if row["response_id"] in response_ids]
    db.execute(delete(KnowledgeResponseSource).where(KnowledgeResponseSource.import_id != import_id))
    for chunk in _chunks(link_rows):
        db.execute(insert(KnowledgeResponseSource), chunk)

    # MERGE_REVIEW -> equivalences, keeping what engineers validated.
    validated = {
        (e.source_requirement_id, e.equivalent_requirement_id): e
        for e in db.execute(select(KnowledgeEquivalence).where(KnowledgeEquivalence.engineer_validation_status != "proposed")).scalars()
    }
    kept = {k: {"engineer_validation_status": e.engineer_validation_status, "validated_by_id": e.validated_by_id,
                "validated_at": e.validated_at, "applicability_conditions": e.applicability_conditions} for k, e in validated.items()}
    db.execute(delete(KnowledgeEquivalence))
    rows = []
    seen_pairs = set()
    for r in tables["MERGE_REVIEW"]:
        pair = (r.get("requirement_id_a"), r.get("requirement_id_b"))
        if not all(pair) or pair in seen_pairs or pair[0] not in requirement_ids or pair[1] not in requirement_ids:
            continue
        seen_pairs.add(pair)
        try:
            similarity = round(float(r.get("similarity") or 0), 2)
        except ValueError:
            similarity = None
        rows.append({
            "source_requirement_id": pair[0], "equivalent_requirement_id": pair[1], "similarity": similarity,
            "proposal": (r.get("proposal") or "")[:128] or None, "engineer_validation_status": "proposed",
            "validated_by_id": None, "validated_at": None, "applicability_conditions": None, "import_id": import_id, "active": True,
            **kept.get(pair, {}),
        })
    for chunk in _chunks(rows):
        db.execute(insert(KnowledgeEquivalence), chunk)

    # REVIEW_ISSUES
    db.execute(delete(KnowledgeIssue))
    rows = [{"issue_type": r["issue_type"][:128], "record_id": r["record_id"][:16], "detail": r.get("detail"),
             "import_id": import_id, "active": True}
            for r in tables["REVIEW_ISSUES"] if r.get("issue_type") and r.get("record_id")]
    for chunk in _chunks(rows):
        db.execute(insert(KnowledgeIssue), chunk)

    inactive = sum(_deactivate(db, model, import_id) for model in (KnowledgeSource, KnowledgeRequirement, KnowledgeMapping, KnowledgeResponse))
    return {"records_added": added, "records_updated": updated, "records_inactive": inactive, "records_flagged": flagged}


def start_import(*, user_id: int | None = None, force: bool = False) -> bool:
    """Run the import in the background. False when one is already running."""
    with _lock:
        if _state.running:
            return False
        _state.running = True

    def work() -> None:
        db = SessionLocal()
        try:
            run_import(db, user_id=user_id, force=force)
        finally:
            db.close()

    threading.Thread(target=work, name="knowledge-import", daemon=True).start()
    return True


# --- status --------------------------------------------------------------------------------


def last_import_id(db: Session | None = None) -> int | None:
    """The import the knowledge base currently stands at: the last one that
    succeeded. What a statement's drafts are made against."""
    own = db is None
    db = db or SessionLocal()
    try:
        return db.execute(select(KnowledgeImport.id).where(KnowledgeImport.status == "succeeded")
                          .order_by(KnowledgeImport.id.desc()).limit(1)).scalar()
    finally:
        if own:
            db.close()


def status(db: Session) -> dict:
    """What the knowledge base holds and how it got there, for the pages.
    Never names the server's filesystem."""
    from sqlalchemy import func

    last_ok = db.execute(select(KnowledgeImport).where(KnowledgeImport.status.in_(("succeeded", "unchanged")))
                         .order_by(KnowledgeImport.id.desc())).scalars().first()
    last = db.execute(select(KnowledgeImport).order_by(KnowledgeImport.id.desc())).scalars().first()
    active = KnowledgeResponse.active.is_(True)
    responses = db.execute(select(func.count()).select_from(KnowledgeResponse).where(active)).scalar() or 0
    eligible = db.execute(select(func.count()).select_from(KnowledgeResponse)
                          .where(active, KnowledgeResponse.autofill_eligibility == "eligible")).scalar() or 0
    requirements = db.execute(select(func.count()).select_from(KnowledgeRequirement).where(KnowledgeRequirement.active.is_(True))).scalar() or 0
    sources = db.execute(select(func.count()).select_from(KnowledgeSource).where(KnowledgeSource.active.is_(True))).scalar() or 0
    by_system: dict[str, dict[str, int]] = {}
    for system, total in db.execute(select(KnowledgeResponse.system, func.count()).where(active).group_by(KnowledgeResponse.system)):
        by_system[system or "Unclassified"] = {"responses": total, "eligible": 0}
    for system, ok in db.execute(select(KnowledgeResponse.system, func.count())
                                 .where(active, KnowledgeResponse.autofill_eligibility == "eligible").group_by(KnowledgeResponse.system)):
        by_system[system or "Unclassified"]["eligible"] = ok
    manufacturers = {
        maker or "Unconfirmed": total
        for maker, total in db.execute(select(KnowledgeResponse.manufacturer, func.count()).where(active)
                                       .group_by(KnowledgeResponse.manufacturer).order_by(func.count().desc()).limit(12))
    }
    def summary(record: KnowledgeImport | None) -> dict | None:
        if record is None:
            return None
        return {
            "id": record.id, "status": record.status, "started_at": record.started_at, "finished_at": record.finished_at,
            "source_label": record.source_label, "workbook_built": record.workbook_built, "files_discovered": record.files_discovered,
            "files_imported": record.files_imported, "files_unchanged": record.files_unchanged, "files_failed": record.files_failed,
            "files_skipped": record.files_skipped, "records_added": record.records_added, "records_updated": record.records_updated,
            "records_inactive": record.records_inactive, "records_flagged": record.records_flagged, "error": record.error,
        }
    current = state()
    return {
        "running": current["running"], "phase": current["phase"], "detail": current["detail"],
        "source_configured": source_root() is not None,
        "last_successful": summary(last_ok), "last": summary(last),
        "last_refreshed_at": (last_ok.finished_at if last_ok else None),
        "records": {"sources": sources, "requirements": requirements, "responses": responses, "eligible_responses": eligible},
        "by_system": by_system, "manufacturers": manufacturers,
    }


def import_report(db: Session, import_id: int) -> dict | None:
    record = db.get(KnowledgeImport, import_id)
    if record is None:
        return None
    files = record.report.get("files", []) if record.report else []
    by_role: dict[str, dict] = {}
    for f in files:
        entry = by_role.setdefault(f["role"], {"role": f["role"], "action": f["action"], "note": f["note"], "count": 0, "examples": []})
        entry["count"] += 1
        if len(entry["examples"]) < 3:
            entry["examples"].append(Path(f["path"]).name)
    return {"id": record.id, "status": record.status, "error": record.error, "started_at": record.started_at,
            "finished_at": record.finished_at, "workbook_built": record.workbook_built,
            "counts": (record.report or {}).get("counts", {}), "files": list(by_role.values()),
            "canonical": next((f for f in files if f["role"] == "canonical"), None)}
