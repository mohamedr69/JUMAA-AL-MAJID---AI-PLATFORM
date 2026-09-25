"""Turn a stored Drawing into the resolved payload the web app shows:
every symbol group matched against the current library, placed floor by
floor, plus totals."""
from __future__ import annotations

import re
from collections import Counter

from sqlalchemy.orm import Session, selectinload

from app.models import IfcBlockAlias as BlockAlias
from app.models import IfcDeviceType as DeviceType
from app.models import IfcSymbol as Symbol
from app.models import ProjectIfcDrawing as Drawing

from . import hints
from .dxf import matcher
from .dxf.extract import _XREF_NAME
from .dxf.sheets import NOT_IDENTIFIED, OUTSIDE, floor_count, floor_name, parse_floors, refresh_floors

MODEL = "Model"


def library(db: Session) -> tuple[list[Symbol], dict[str, int]]:
    symbols = db.query(Symbol).options(selectinload(Symbol.device_type)).all()
    aliases = {a.block_name.upper(): a.symbol_id for a in db.query(BlockAlias).all()}
    return symbols, aliases


def name_floor(sheet: dict) -> None:
    """The floor a plan sheet is of, from its drawing title: set as
    "floor_name", with "floor_identified". A title that names no floor is not
    given one -- the sheet is "Floor not identified" and says why, so its
    devices are counted under that, not under a guess."""
    if sheet["kind"] != "plan":
        sheet["floor_name"], sheet["floor_identified"] = sheet["title"], True
        return
    name = floor_name(sheet["title"])
    sheet["floor_name"], sheet["floor_identified"] = name or NOT_IDENTIFIED, name is not None
    if name is None:
        why = (f'the drawing title "{sheet["title"]}" names no floor' if sheet["title"]
               else "no drawing title was found on the sheet")
        sheet["note"] = (sheet.get("note") + "; " if sheet.get("note") else "") + f"{NOT_IDENTIFIED}: {why}"


def floor_sheets(drawing: Drawing) -> tuple[list[dict], bool]:
    """The drawing's sheets with the floor multiplier in force (the title's
    reading, or the user's override). Returns (sheets, single_floor): when
    no floor-plan sheet shows any device the whole drawing is one floor --
    named by its one floor-plan sheet's title, never by its file name; a
    drawing with several plan sheets and none showing a device cannot say
    which floor it is, and says so."""
    meta = drawing.meta or {}
    overrides = meta.get("floor_overrides") or {}
    sheets = refresh_floors([dict(s) for s in meta.get("sheets") or []])
    used = {o.get("sheet") for g in drawing.groups or [] for o in g.get("occurrences", [])}
    plans_used = [s for s in sheets if s["kind"] == "plan" and s["name"] in used]
    if not plans_used:
        plans = [s for s in sheets if s["kind"] == "plan"]
        title = plans[0]["title"] if len(plans) == 1 else ""
        source = plans[0].get("title_source", "") if len(plans) == 1 else ""
        floors = parse_floors(title)
        sheets = [{"name": MODEL, "title": title, "kind": "plan", "floors": floors,
                   "multiplier": floor_count(title, floors), "note": "", "title_source": source}]
        single = True
    else:
        single = False
    for s in sheets:
        s["parsed_multiplier"] = s["multiplier"]
        if s["name"] in overrides:
            s["multiplier"] = int(overrides[s["name"]])
            s["overridden"] = True
    return sheets, single


OFF_ARCH = "(not on the architecture)"
CATEGORIES = ("fire_alarm", "emergency_light", "other")

# Before quantities are given the user answers every symbol on the floor
# plans that the library does not know exactly. Kinds of question:
#   answer        no guess the letters confirm (none, or from the shape
#                 alone): the user picks a device, or not a device
#   suggested     the app's guess, the letters agreeing with a library symbol: to check
#   architecture  the architect's model (xref layers, or furniture and fittings
#                 by their Revit family name): "not a device" to check
#   confirm       counted by resemblance to a library symbol: to confirm
# Not asked ("optional"): symbols off the floor plans (riser, schematic,
# outside every sheet, off the architecture) and unlikely devices (no
# letters, not on a device layer, like nothing in the library): they are
# not counted either way. "skipped": the user left it out of the BOQ
# without teaching the library.
REQUIRED = ("answer", "suggested", "architecture", "confirm")
_XREF_LAYER = re.compile(r"\$0\$|\|")
# furniture, fittings and landscape as Revit exports them ("Sofa - Double - Sofa-10297482-FA-105-...")
_ARCH_NAME = re.compile(
    r"FURNITURE|SOFA|CHAIR|TABLE|DESK|\bBEDS?\b|WARDROBE|FRIDGE|REFRIGERATOR|COOK ?TOP|OVEN|KITCHEN|SINK|BASIN|\bWC\b|"
    r"TOILET|URINAL|BATH|SHOWER|(?<![A-Z])DOOR|WINDOW|ELEVATOR|\bLIFT\b|STAIR|TREADMILL|BENCH|GYM|PLAYGROUND|POOL|JACUZZI|"
    r"PLANT|TREE|SHRUB|\bPALM\b|\bCARS?\b|VEHICLE|PARKING|GARBAGE|TROLLEY|RAILING|CURTAIN|LOUNGE|MULLION|COLUMN|EXERCISE|BIKE|(?<![A-Z])TIRES?(?![A-Z])|TYRE|LAUNDRY|CHUTE", re.I)
# never architecture, whatever else the name says (a door holder is a fire alarm device)
_DEVICE_NAME = re.compile(
    r"DETECT|SMOKE|HEAT|CALL ?POINT|\bMCP\b|SPEAKER|SOUNDER|STROBE|FLASH|BEACON|BELL|HORN|EXIT|EMERG|LIGHT|LUMINAIRE|"
    r"FIRE|ALARM|MODULE|SIGN|PANEL|ANNUNC|BREAK ?GLASS|MANUAL|TELEPHONE|JACK|ISOLATOR|REPEATER|INTERFACE|SPRINKLER|"
    r"SENSOR|DEVICE|HOLDER|RELEASE|MAGNET|RECALL|CONTACT", re.I)


def architecture_like(g: dict) -> bool:
    """The symbol belongs to the architect's model: every layer it is placed
    on is an xref's (bound as NAME$0$LAYER or attached as NAME|LAYER), every
    block came in with an xref (NAME$0$BLOCK, XR_...), or every block is
    furniture or a fitting by its Revit family and type. Never when a layer
    reads like a fire alarm or lighting layer, or a name like a device."""
    layers = [layer for layer in g.get("layers", {}) if layer]
    if any(matcher._DEVICE_LAYER.search(layer) for layer in layers):
        return False
    if layers and all(_XREF_LAYER.search(layer) for layer in layers):
        return True
    names = [n for n in g.get("block_names", {}) if n]
    families = [matcher.revit_family(n) or n for n in names]  # a Revit name's sheet part says nothing
    if not names or any(_DEVICE_NAME.search(f) for f in families):
        return False
    return all(_XREF_NAME.search(n) for n in names) or all(_ARCH_NAME.search(f) for f in families)


def review_kind(g: dict, skipped: set[str]) -> str | None:
    """What the user is asked about this symbol before the quantities (see
    REQUIRED above); None when the library knows its exact drawing."""
    status = g["status"]
    on_plan = g.get("on_plans", 0) > 0
    if status == "verified":
        return "confirm" if on_plan and (g.get("match") or {}).get("kind") in ("library", "family") else None
    if status == "ignored":
        return None
    if g["signature"] in skipped:
        return "skipped"
    sug = g.get("suggestion") or {}
    likely = g.get("hint", 0) >= 3 or (bool(sug) and not sug.get("is_ignored"))
    if not on_plan or not likely:
        return "optional"
    if architecture_like(g):
        return "architecture"
    # a guess is filled in only when the letters agree with a library symbol;
    # one from the shape alone (furniture that looks like a speaker) is shown
    # to the user but left for them to answer
    return "suggested" if sug and g.get("label") else "answer"


def floor_boq(groups: list[dict], plans: list[dict], order: dict[int, int]) -> dict[str, dict]:
    """The quantities floor by floor, per category: every floor plan in the
    drawing's sheet order with its devices (count on the plan, floors the
    plan stands for, and the product), then the building total per device.
    The web app's quantity tabs and the Excel export both list this."""
    out = {}
    for category in CATEGORIES:
        types: dict[int, dict] = {}
        count: Counter = Counter()
        for g in groups:
            dt = g.get("device_type")
            if g["status"] != "verified" or not dt or dt["category"] != category:
                continue
            types[dt["id"]] = dt
            for sheet, n in g["by_sheet"].items():
                count[sheet, dt["id"]] += n
        ids = sorted(types, key=lambda i: (order.get(i, 0), types[i]["name"]))
        floors = []
        for s in plans:
            rows = [{"device_type": types[i], "per_floor": count[s["name"], i], "qty": count[s["name"], i] * s["multiplier"]}
                    for i in ids if count[s["name"], i]]
            floors.append({
                "sheet": s["name"], "title": s["title"], "floor_name": s["floor_name"],
                "floor_identified": s.get("floor_identified", True),
                "floors": s["floors"], "multiplier": s["multiplier"], "rows": rows,
                "per_floor": sum(r["per_floor"] for r in rows), "qty": sum(r["qty"] for r in rows),
            })
        building = []
        for i in ids:
            qty = sum(count[s["name"], i] * s["multiplier"] for s in plans)
            if qty:
                building.append({"device_type": types[i], "qty": qty})
        out[category] = {"floors": floors, "building": building, "qty": sum(b["qty"] for b in building)}
    return out


def resolved_drawing(db: Session, drawing: Drawing, with_occurrences: bool = True) -> dict:
    """A device is counted when it is on a floor-plan sheet AND placed on the
    architecture (when the drawing has architecture to judge by). Anything
    else is shown with the reason and not counted: a riser or schematic
    sheet, outside every sheet, or off the architecture (a legend symbol, a
    stray copy)."""
    symbols, aliases = library(db)
    groups = matcher.resolve(drawing.groups or [], symbols, aliases)
    # What each symbol's own words say it is: a suggestion where the library
    # has none, and a flag where an answer disagrees with them (`hints`).
    codes = {t.code.upper(): matcher.device_type_out(t)
             for t in db.query(DeviceType).filter(DeviceType.is_active.is_(True)).all()}
    for g in groups:
        g["name_hint"] = hints.hint(g, codes)
        g["conflict"] = hints.conflict(g)
    sheets, single = floor_sheets(drawing)
    mult = {s["name"]: s["multiplier"] for s in sheets if s["kind"] == "plan"}
    kinds = {s["name"]: s["kind"] for s in sheets}
    arch = (drawing.meta or {}).get("architecture") or {}

    for g in groups:
        by_sheet: Counter = Counter()
        for o in g.get("occurrences", []):
            sheet = MODEL if single else (o.get("sheet") or OUTSIDE)
            if sheet in mult and o.get("on_arch") is False:
                sheet = OFF_ARCH
            by_sheet[sheet] += 1
        g["by_sheet"] = dict(by_sheet)
        g["on_plans"] = sum(n for s, n in by_sheet.items() if s in mult)
        g["boq_qty"] = sum(n * mult[s] for s, n in by_sheet.items() if s in mult)
        g["not_counted"] = g["count"] - g["on_plans"]
    if not with_occurrences:
        for g in groups:
            g.pop("occurrences", None)

    # symbols on diagram sheets, off the architecture or outside every sheet: shown, not counted
    present = Counter()
    for g in groups:
        for s, n in g["by_sheet"].items():
            present[s] += n
    extra = []
    for s in present:
        if s not in kinds:
            title = {OUTSIDE: "Outside every sheet", OFF_ARCH: "Not placed on the architecture"}.get(s, s)
            extra.append({"name": s, "title": title, "kind": "outside", "floors": [], "multiplier": 0, "note": ""})
    all_sheets = sheets + extra

    # one floor or several: which plans hold counted devices, and how many floors they stand for
    plans_with_devices = [s for s in sheets if s["kind"] == "plan" and any(g["by_sheet"].get(s["name"]) for g in groups if g["status"] == "verified")]
    plans_considered = plans_with_devices or [s for s in sheets if s["kind"] == "plan"]
    floors_total = sum(s["multiplier"] for s in plans_considered)
    mode = "single" if len(plans_considered) <= 1 and floors_total <= 1 else "multiple"
    floor_info = {
        "mode": mode,
        "plans": len(plans_considered),
        "floors": floors_total,
        "floor_name": ((floor_name(plans_considered[0]["title"]) or NOT_IDENTIFIED)
                       if len(plans_considered) == 1 else None),
        "excluded_sheets": [f'{s["name"]} {s["title"]}' for s in sheets if s["kind"] == "diagram"],
        "architecture_found": bool(arch.get("found")),
    }
    for s in all_sheets:
        name_floor(s)
    order = {t.id: t.sort_order for t in db.query(DeviceType).all()}
    boq = floor_boq(groups, [s for s in sheets if s["kind"] == "plan"], order)

    skipped = set((drawing.meta or {}).get("review_skipped") or [])
    review: Counter = Counter()
    for g in groups:
        g["review"] = review_kind(g, skipped)
        if g["review"]:
            review[g["review"]] += 1
    required = sum(review[k] for k in REQUIRED)
    review_info = {k: review[k] for k in (*REQUIRED, "optional", "skipped")}
    review_info.update(required=required, ready=required == 0,
                       conflicts=sum(1 for g in groups if g["conflict"] and g["on_plans"]))

    conversion = {k: v for k, v in ((drawing.meta or {}).get("conversion") or {}).items() if k != "dwg_path"} or None
    return {
        "id": drawing.id,
        "filename": drawing.filename,
        "uploaded_at": drawing.uploaded_at,
        "units": drawing.units,
        "dxf_version": drawing.dxf_version,
        "seconds": drawing.seconds,
        "containers": (drawing.meta or {}).get("containers", []),
        "loose_symbols": (drawing.meta or {}).get("loose_symbols", 0),
        "skipped_empty_blocks": (drawing.meta or {}).get("skipped_empty_blocks", []),
        "layouts": (drawing.meta or {}).get("layouts", []),
        "conversion": conversion,
        "sheets": all_sheets,
        "single_floor": single,
        "floor_info": floor_info,
        "floor_boq": boq,
        "review": review_info,
        "groups": groups,
        "totals": matcher.totals(groups),
    }
