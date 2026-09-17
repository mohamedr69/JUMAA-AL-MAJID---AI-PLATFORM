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


@dataclasses.dataclass
class SelectedBattery:
    catalog_no: str
    description: str
    manufacturer: str | None
    quantity: float
    panels: list[str]
    datasheet_library: str | None = None
    datasheet_path: str | None = None


def selected_batteries(db: Session, project: Project) -> list[SelectedBattery]:
    """The batteries selected for the project's fire alarm panels, APS and
    BPS cabinets, one per model."""
    from app.routers.design import _battery_calculation

    try:
        calculation = _battery_calculation(db, project)
    except Exception:  # noqa: BLE001 -- no sizing rule, no calculation: no batteries to propose
        return []
    found: dict[str, SelectedBattery] = {}
    for panel in calculation.panels:
        for battery in panel.selected or []:
            if not battery.part_no:
                continue
            key = battery.part_no.upper()
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
    return sorted(found.values(), key=lambda b: b.catalog_no)
