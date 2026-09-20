"""The symbol library as a file that travels with the code.

The database is the working copy. Every change to the library (verify,
reassign, remove, device types) is written through to
symbols/symbol_library.json on the company library shelf
(`app.services.company_library`, backend/library by default), and on
start-up any device type or symbol in that file that the database lacks is
loaded. A new PC, or a fresh database, therefore starts with every symbol
verified so far.

The file lives outside app/ on purpose: the dev server reloads on changes
under app/, and writing the library must not restart it.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session, selectinload

from app.models import IfcBlockAlias as BlockAlias
from app.models import IfcDeviceType as DeviceType
from app.models import IfcSymbol as Symbol
from app.services import company_library

log = logging.getLogger("boq.library")

SHELF = "symbols"
FORMAT_VERSION = 1


def library_path() -> Path:
    """<company library>/symbols/symbol_library.json; BOQ_LIBRARY_PATH overrides."""
    override = os.environ.get("BOQ_LIBRARY_PATH")
    return Path(override) if override else company_library.library_root() / SHELF / "symbol_library.json"


def _iso(dt: datetime | None) -> str:
    return dt.isoformat(timespec="seconds") if dt else ""


def export_library(db: Session, path: Path | None = None) -> Path:
    path = path or library_path()
    types = db.query(DeviceType).order_by(DeviceType.category, DeviceType.sort_order, DeviceType.code).all()
    symbols = (
        db.query(Symbol)
        .options(selectinload(Symbol.device_type))
        .order_by(Symbol.signature)
        .all()
    )
    data = {
        "format": FORMAT_VERSION,
        "exported_at": _iso(datetime.now()),
        "device_types": [
            {
                "code": t.code,
                "name": t.name,
                "category": t.category,
                "unit": t.unit,
                "sort_order": t.sort_order,
                "is_active": t.is_active,
            }
            for t in types
        ],
        "symbols": [
            {
                "signature": s.signature,
                "device_code": s.device_type.code if s.device_type else None,
                "is_ignored": s.is_ignored,
                "label": s.label,
                "inner_label": s.inner_label,
                "notes": s.notes,
                "block_names": sorted(s.block_names or []),
                "entity_counts": s.entity_counts or {},
                "source_drawing": s.source_drawing,
                "verified_at": _iso(s.updated_at),
                "raster_hex": s.raster_hex,
                "svg": s.svg,
            }
            for s in symbols
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


def import_library(db: Session, path: Path | None = None) -> dict[str, int]:
    """Add what the file has and the database lacks. Never overwrites a
    symbol already in the database: the database is the working copy."""
    path = path or library_path()
    added = {"device_types": 0, "symbols": 0}
    if not path.exists():
        return added
    data = json.loads(path.read_text(encoding="utf-8"))

    by_code = {t.code.upper(): t for t in db.query(DeviceType).all()}
    for t in data.get("device_types", []):
        code = str(t["code"]).upper()
        if code in by_code:
            continue
        dt = DeviceType(
            code=code,
            name=t["name"],
            category=t["category"],
            unit=t.get("unit", "Nos"),
            sort_order=t.get("sort_order", 500),
            is_active=t.get("is_active", True),
        )
        db.add(dt)
        by_code[code] = dt
        added["device_types"] += 1
    db.flush()

    have = {sig for (sig,) in db.query(Symbol.signature).all()}
    taken = {a for (a,) in db.query(BlockAlias.block_name).all()}
    for s in data.get("symbols", []):
        if s["signature"] in have:
            continue
        code = (s.get("device_code") or "").upper()
        dt = by_code.get(code) if code else None
        if not s.get("is_ignored") and dt is None:
            log.warning("library file: symbol %s names unknown device code %r, skipped", s["signature"], code)
            continue
        sym = Symbol(
            signature=s["signature"],
            label=s.get("label", ""),
            inner_label=s.get("inner_label", ""),
            raster_hex=s.get("raster_hex", ""),
            svg=s.get("svg", ""),
            entity_counts=s.get("entity_counts", {}),
            block_names=s.get("block_names", []),
            device_type_id=None if s.get("is_ignored") else dt.id,
            is_ignored=bool(s.get("is_ignored")),
            notes=s.get("notes", ""),
            source_drawing=s.get("source_drawing", ""),
        )
        db.add(sym)
        db.flush()
        for name in sym.block_names or []:
            key = name.upper()
            if key not in taken:
                db.add(BlockAlias(block_name=key, symbol_id=sym.id))
                taken.add(key)
        added["symbols"] += 1
    db.commit()
    return added


def save_library(db: Session) -> None:
    """Write-through after a library change. A failure to write the file is
    logged, never allowed to fail the user's action."""
    try:
        export_library(db)
    except Exception:  # pragma: no cover - disk full, file locked by an editor, ...
        log.exception("could not write the symbol library file %s", library_path())
