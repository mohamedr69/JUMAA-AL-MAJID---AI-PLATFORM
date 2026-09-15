"""Where each BOQ line came from, kept through every save.

A line read off a Design Sheet carries its run, document hash, page, box,
the raw OCR text and what the parser made of it. The BOQ page saves the whole
table at once, so provenance lives server-side and is matched back to the
saved rows by id: the page cannot write it, only keep the id it was given.
When an engineer changes a value the sheet carries, the machine's original is
kept in `extracted_values` and the line is marked edited, so "extracted",
"corrected by an engineer" and "typed in" stay three different things.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.models import BoqSnapshot, ExtractionRun, Project, ProjectBoqItem, User

# The values a Design Sheet carries. Manufacturer, unit, prices and remarks
# are the platform's or the engineer's, so editing them is not a correction
# of what was read.
SHEET_FIELDS = ("system_code", "group_heading", "catalog_no", "description", "quantity")

PROVENANCE_FIELDS = (
    "building", "catalog_canonical", "catalog_match", "origin", "extraction_run_id", "source_document_sha256", "source_page", "source_region", "raw_values",
    "ocr_confidence", "parser_version", "extracted_values", "edited_by_id", "edited_at", "created_at",
)
CONTENT_FIELDS = ("system_code", "group_heading", "manufacturer", "catalog_no", "description", "quantity", "unit",
                  "unit_price", "total_price", "remarks")


def sheet_values(source) -> dict:
    return {name: getattr(source, name, None) if not isinstance(source, dict) else source.get(name)
            for name in SHEET_FIELDS}


def extracted_item(*, system_code: str | None, line, run: ExtractionRun | None, manufacturer: str | None,
                   position: int, origin: str = "extracted") -> ProjectBoqItem:
    """A BOQ row for a line the extractor read, with everything that locates it."""
    region = line.region() if hasattr(line, "region") else None
    item = ProjectBoqItem(
        system_code=system_code,
        position=position,
        group_heading=line.group_heading,
        manufacturer=manufacturer,
        catalog_no=line.catalog_no,
        description=line.description,
        quantity=line.quantity,
        origin=origin,
        extraction_run_id=run.id if run is not None else None,
        source_document_sha256=run.document_sha256 if run is not None else None,
        source_page=getattr(line, "page", None),
        source_region=list(region) if region else None,
        raw_values={
            "catalog_no": line.catalog_no,
            "description": line.description,
            "quantity": getattr(line, "raw_quantity", None),
            "quantity_parse": getattr(line, "quantity_parse", None),
        },
        ocr_confidence=Decimal(str(round(line.confidence, 2))) if getattr(line, "confidence", None) is not None else None,
        parser_version=run.parser_version if run is not None else None,
        created_at=utc_now(),
        building=(getattr(line, "building", None) or {}).get("display"),
        catalog_match=getattr(line, "catalog_match", None),
        catalog_canonical=(getattr(line, "catalog_match", None) or {}).get("canonical"),
    )
    if item.raw_values is not None:
        item.raw_values = {**item.raw_values, "catalog_no": getattr(line, "catalog_raw", None) or line.catalog_no,
                           "alternates": getattr(line, "alternates", None),
                           "building_aliases": (getattr(line, "building", None) or {}).get("aliases")}
    item.extracted_values = {**sheet_values(item)}
    return item


def _same(a, b) -> bool:
    if isinstance(a, str):
        a = a.strip() or None
    if isinstance(b, str):
        b = b.strip() or None
    if isinstance(a, (int, float, Decimal)) or isinstance(b, (int, float, Decimal)):
        try:
            return Decimal(str(a)) == Decimal(str(b)) if a is not None and b is not None else a == b
        except Exception:  # noqa: BLE001
            return a == b
    return a == b


def rebuild_items(project: Project, incoming: list[dict], user: User | None) -> list[ProjectBoqItem]:
    """The BOQ as saved: one row per incoming line, in order, each keeping
    the provenance of the stored row whose id it carries. An id that is not
    one of this project's lines is ignored (the line is new): provenance is
    never taken from the page."""
    existing = {item.id: item for item in project.boq_items}
    now = utc_now()
    rows: list[ProjectBoqItem] = []
    claimed: set[int] = set()
    for position, values in enumerate(incoming):
        line_id = values.get("id")
        previous = existing.get(line_id) if line_id is not None and line_id not in claimed else None
        content = {name: values.get(name) for name in CONTENT_FIELDS}
        item = ProjectBoqItem(position=position, **content)
        if previous is None:
            item.origin = "manual"
            item.created_at = now
            item.edited_by_id = user.id if user else None
            item.edited_at = now
        else:
            claimed.add(previous.id)
            for name in PROVENANCE_FIELDS:
                setattr(item, name, getattr(previous, name))
            if any(not _same(getattr(previous, name), content.get(name)) for name in CONTENT_FIELDS):
                if previous.extracted_values is None and previous.origin in ("extracted", "ai_accepted", "review_accepted"):
                    item.extracted_values = sheet_values(previous)
                item.edited_by_id = user.id if user else None
                item.edited_at = now
        rows.append(item)
    return rows


def is_corrected(item: ProjectBoqItem) -> bool:
    """A machine-read line whose sheet values an engineer has since changed."""
    if item.origin not in ("extracted", "ai_accepted", "review_accepted") or not item.extracted_values:
        return False
    return any(not _same(item.extracted_values.get(name), getattr(item, name)) for name in SHEET_FIELDS)


def item_record(item: ProjectBoqItem) -> dict:
    """Everything about a line, JSON-ready, for a snapshot."""
    record = {"id": item.id, "position": item.position}
    for name in CONTENT_FIELDS + PROVENANCE_FIELDS:
        value = getattr(item, name)
        if isinstance(value, Decimal):
            value = str(value)
        elif hasattr(value, "isoformat"):
            value = value.isoformat()
        record[name] = value
    return record


def snapshot(db: Session, project: Project, reason: str, user: User | None) -> BoqSnapshot:
    """Keep the BOQ exactly as it is now. Added to the session, not committed:
    it belongs to the same transaction as the change it guards."""
    shot = BoqSnapshot(project_id=project.id, boq_version=project.boq_version, reason=reason,
                       items=[item_record(item) for item in project.boq_items],
                       created_by_id=user.id if user else None)
    db.add(shot)
    return shot


def part_library(db: Session) -> dict[str, str]:
    """Part numbers the platform already trusts, by key: the catalogue's
    part currents and battery units, and the models the knowledge base's
    approved responses name. What a read code is checked against."""
    from app.extraction.identity import part_key
    from app.models import DesignRule, KnowledgeModel
    from app.seed import BATTERY_UNIT_CATEGORY, PART_CURRENT_CATEGORY

    library: dict[str, str] = {}
    for rule in db.query(DesignRule).filter(DesignRule.category.in_((PART_CURRENT_CATEGORY, BATTERY_UNIT_CATEGORY)),
                                            DesignRule.superseded_at.is_(None)):
        spelling = (rule.data or {}).get("part_no")
        if spelling:
            library.setdefault(part_key(spelling), spelling)
    for (model,) in db.query(KnowledgeModel.model).distinct():
        if model and len(part_key(model)) >= 4:
            library.setdefault(part_key(model), model)
    return library


def check_catalog(target, library: dict[str, str]) -> None:
    """Record, beside the line's code, what the part library makes of it.
    `target` is a ProjectBoqItem or a line record dict; the code itself is
    never changed."""
    from app.extraction.identity import match_catalog

    get = (lambda name: target.get(name)) if isinstance(target, dict) else (lambda name: getattr(target, name))
    code = get("catalog_no")
    if not code:
        return
    match = match_catalog(code, library)
    previous = get("catalog_match") or {}
    record = {**previous, "cleaned": code, "library": match.canonical, "library_reason": match.reason}
    canonical = match.canonical or previous.get("canonical")
    if isinstance(target, dict):
        target["catalog_match"], target["catalog_canonical"] = record, canonical
    else:
        target.catalog_match, target.catalog_canonical = record, canonical


def document_name(run: ExtractionRun | None) -> str | None:
    return Path(run.document_path).name if run is not None else None


def pipeline_sha(path: Path) -> str | None:
    from app.extraction.pipeline import sha256_of

    return sha256_of(path)
