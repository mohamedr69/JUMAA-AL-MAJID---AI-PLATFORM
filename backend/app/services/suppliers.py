"""Who supplies each brand: the trading company, its people, its address.

One row per brand (`brand_suppliers`), for every project. Seeded with what
the engineers gave on 18 Sep 2026; an engineer's later edit stands (a seed
never overwrites a row that exists).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import BrandSupplier

SEED: list[dict] = [
    {
        "brand": "FIREGUARD",
        "supplier": "AL RAYAN SECURITY AND SAFETY TRADING",
        "emails": "sales1@alrayandxb.com, m.ghazy@alrayandxb.com",
        "address": "Al Qusais Ind. 4, Near Galadari Driving School, PO Box 233949, Dubai, UAE",
        "website": "alrayandxb.com",
    },
    {
        "brand": "RAMCRO",
        "supplier": "ubemirates",
        "contact": "Suresh K.S, Marketing Manager",
        "phone": "+971 55 5792971",
        "emails": "suresh@ubemirates.com",
        "website": "www.ubemirates.com",
    },
]

FIELDS = ("supplier", "contact", "phone", "emails", "address", "map_url", "website", "notes")


def _brand(brand: str | None) -> str:
    return (brand or "").strip().upper()


def get(db: Session, brand: str | None) -> BrandSupplier | None:
    key = _brand(brand)
    return db.query(BrandSupplier).filter(BrandSupplier.brand == key).first() if key else None


def all_rows(db: Session) -> list[BrandSupplier]:
    return db.query(BrandSupplier).order_by(BrandSupplier.brand).all()


def save(db: Session, brand: str, values: dict, user_id: int | None = None) -> BrandSupplier:
    key = _brand(brand)
    if not key or not (values.get("supplier") or "").strip():
        raise ValueError("A brand and a supplier name are needed")
    row = get(db, key)
    if row is None:
        row = BrandSupplier(brand=key, supplier=values["supplier"].strip())
        db.add(row)
    for field in FIELDS:
        if field in values:
            value = values[field]
            setattr(row, field, value.strip() if isinstance(value, str) and value.strip() else None)
    row.supplier = values["supplier"].strip()
    row.updated_by_id = user_id
    db.commit()
    db.refresh(row)
    return row


def seed(db: Session) -> int:
    added = 0
    for entry in SEED:
        if get(db, entry["brand"]) is None:
            db.add(BrandSupplier(**{k: v for k, v in entry.items()}))
            added += 1
    if added:
        db.commit()
    return added
