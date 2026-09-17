"""The batteries the battery calculation selects, as materials.

The BOQ quotes a battery by capacity ("12V10A"); the calculation selects
the catalogued unit that covers each panel's load (ROCKET ES18-12 ...).
What is proposed for approval is the selected unit, so it is a material
of the fire alarm system -- on the Proposed Materials tab and on the
Schedule of Material -- read from the saved calculation (Python, per
panel; no model). One line per battery model, its units summed over the
panels it serves.
"""

from __future__ import annotations

import dataclasses

from sqlalchemy.orm import Session

from app.models import Project


import re

_BATTERY_PART_RE = re.compile(r"^\d{1,2}\s*V\s*@?\s*\d{1,3}\s*A(H)?$", re.IGNORECASE)


@dataclasses.dataclass
class SelectedBattery:
    catalog_no: str
    description: str
    manufacturer: str | None
    quantity: float
    panels: list[str]
    # The BOQ group heading of the cabinet the battery serves: where it
    # takes the place of the battery the BOQ quoted.
    headings: list[str] = dataclasses.field(default_factory=list)
    datasheet_library: str | None = None
    datasheet_path: str | None = None


def is_battery_line(item) -> bool:
    """A BOQ line that quotes a battery by capacity ("12V65A", "Battery,
    12 V @ 65 AH"): what the calculation's selection replaces."""
    from app.services.battery_calculation import battery_from_text

    part = (getattr(item, "catalog_no", None) or "").strip()
    if part and _BATTERY_PART_RE.match(part):
        return True
    return battery_from_text(getattr(item, "description", None) or "") is not None


def selected_batteries(db: Session, project: Project, *, per_panel: bool = False) -> list[SelectedBattery]:
    """The batteries selected for the project's fire alarm panels, APS and
    BPS cabinets: one per model (their units summed), or one per panel and
    model when `per_panel`."""
    from app.routers.design import _battery_calculation

    try:
        calculation = _battery_calculation(db, project)
    except Exception:  # noqa: BLE001 -- no sizing rule, no calculation: no batteries to propose
        return []
    found: dict[tuple, SelectedBattery] = {}
    for panel in calculation.panels:
        for battery in panel.selected or []:
            if not battery.part_no:
                continue
            key = (battery.part_no.upper(), panel.key if per_panel else None)
            entry = found.get(key)
            if entry is None:
                entry = found[key] = SelectedBattery(
                    catalog_no=battery.part_no,
                    description=f"Sealed lead-acid battery, {battery.voltage:g} V @ {battery.capacity_ah:g} Ah",
                    manufacturer=(battery.brand or "ROCKET").upper(), quantity=0.0, panels=[],
                    datasheet_library=battery.datasheet_library, datasheet_path=battery.datasheet_path)
            entry.quantity += float(battery.units or 0)
            name = panel.name or panel.heading
            if name not in entry.panels:
                entry.panels.append(name)
            if panel.heading not in entry.headings:
                entry.headings.append(panel.heading)
    return sorted(found.values(), key=lambda b: (b.catalog_no, b.panels))
