"""BOQ Floor Wise beside BOQ as per IFC Drawings: the same fire alarm
devices, floor by floor, from two documents that should agree.

The floor-wise BOQ is the engineer's Excel schedule; the IFC BOQ is counted
off the IFC drawings in force (each drawing's latest revision). They name
things differently, so both are brought to the same words first:

    devices   the platform's device vocabulary (`symbol_taxonomy.device_in`)
              -- "Smoke detector", "Manual call point" -- read from the
              schedule line's wording and from the IFC device type's name.
              A wall and a ceiling speaker are both "Speaker" on both sides.
    floors    a short key -- B3, GF, P1, L3, MECH, RF -- from "3rd Basement"
              and "3RD BASEMENT FLOOR" alike (`floor_key`). An IFC typical
              plan stands for the floors it was counted for, each of them;
              one whose floor count was changed by hand cannot be put on
              particular floors and is shown as its own line.

Nothing is matched by guess: a device or a floor that only one side names is
shown against a blank on the other, which is the difference to look at.
Fire alarm only, like the IFC tab; emergency lighting follows it.
"""
from __future__ import annotations

import re
from collections import defaultdict

from app.services.symbol_taxonomy import device_in

_ORDINAL_WORDS = {
    "FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4, "FIFTH": 5, "SIXTH": 6, "SEVENTH": 7, "EIGHTH": 8,
    "NINTH": 9, "TENTH": 10,
}
_NUMBER = re.compile(r"\b(\d{1,3})(?:ST|ND|RD|TH)?\b")


def _number(text: str) -> int | None:
    m = re.search(r"\b[BPML]\s?(\d{1,3})\b", text) or _NUMBER.search(text)
    if m:
        return int(m.group(1))
    for word, n in _ORDINAL_WORDS.items():
        if re.search(rf"\b{word}\b", text):
            return n
    return None


def floor_key(label: str) -> tuple[str, float, str]:
    """(key, order, label) for a floor as either document names it:
    '3rd Basement' and '3RD BASEMENT FLOOR' are both ('B3', -3, 'Basement 3')."""
    t = re.sub(r"[_\-.]", " ", str(label).upper())
    t = " ".join(t.split())
    n = _number(t)
    # "B4" and "B05" alike: a basement number may be written with its zero.
    if re.search(r"\bBASEMENT\b|\bB\s?\d{1,2}\b", t):
        n = n or 1
        return f"B{n}", -float(n), f"Basement {n}"
    if re.search(r"\bLOWER\s+GROUND\b|\bLG\b", t):
        return "LG", -0.5, "Lower ground"
    if re.search(r"\bUPPER\s+GROUND\b|\bUG\b", t):
        return "UG", 0.2, "Upper ground"
    if re.search(r"\bGROUND\b|\bG\s?F\b|^G$", t):
        return "GF", 0.0, "Ground floor"
    if re.search(r"\bMEZZ", t):
        n = n or 1
        return f"M{n}", 0.3 + n / 100, f"Mezzanine {n}"
    if re.search(r"\bPODIUM\b|^P\s?\d+$", t):
        n = n or 1
        return f"P{n}", 0.5 + n / 100, f"Podium {n}"
    if re.search(r"\b(TOP|UPPER|MAIN)\s+ROOF\b", t):
        return "TRF", 1001.0, "Top roof"
    if re.search(r"\bROOF\b|\bR\s?F\b", t):
        return "RF", 1000.0, "Roof"
    if re.search(r"\bMECH", t):
        return "MECH", 500.0, "Mechanical floor"
    if re.search(r"\bSLAB\b", t):
        return "SLAB", 500.5, "Structural slab"
    if re.search(r"\bSERVICE\b", t):
        return "SERVICE", 499.0, "Service floor"
    words = "|".join(_ORDINAL_WORDS)
    if n is not None and not re.search(r"[A-Z]{3,}", re.sub(rf"\b(LEVEL|FLOOR|FLR|LVL|L\s?\d+|\d+(ST|ND|RD|TH)|{words})\b", "", t)):
        if n == 0:
            return "GF", 0.0, "Ground floor"
        return f"L{n}", float(n), f"Level {n}"
    return t, 900.0, str(label)


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _tidy(value: float) -> float | int:
    return int(value) if float(value).is_integer() else round(value, 2)


def compare(schedule: dict | None, drawings: list[dict]) -> dict:
    """`schedule` is the floor-wise BOQ as stored (`Schedule.as_dict`), or
    None; `drawings` the IFC drawings in force, each resolved
    (`resolve.resolved_drawing`) with its "id", "filename" and "revision"."""
    qty: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {"schedule": 0.0, "ifc": 0.0})
    floors: dict[str, dict] = {}
    devices: dict[str, dict] = {}
    schedule_order = {}

    def device(name: str) -> dict:
        return devices.setdefault(name, {"device": name, "schedule_lines": [], "ifc_types": []})

    def floor(label: str, *, from_schedule: bool = False, shown_as: str | None = None) -> str:
        key, order, nice = floor_key(label)
        entry = floors.setdefault(key, {"key": key, "label": nice, "order": order, "schedule_names": [], "ifc_names": []})
        names = entry["schedule_names" if from_schedule else "ifc_names"]
        if (shown_as or label) not in names:
            names.append(shown_as or label)
        return key

    # --- the floor-wise BOQ ------------------------------------------------------------------
    unnamed = []
    if schedule:
        for i, name in enumerate(schedule.get("floors") or []):
            schedule_order.setdefault(floor_key(name)[0], i)
        for item in schedule.get("items") or []:
            if (item.get("system") or "") != "FAS":
                if not item.get("system") and not item.get("device"):
                    unnamed.append({"description": item.get("description"), "total": item.get("total")})
                continue
            name = item.get("device") or item.get("description") or "?"
            dev = device(name)
            dev["schedule_lines"].append({"description": item.get("description"), "catalog_no": item.get("catalog_no"),
                                          "total": item.get("total")})
            for fname, n in (item.get("per_floor") or {}).items():
                if _num(n):
                    qty[name, floor(fname, from_schedule=True)]["schedule"] += _num(n)

    # --- the IFC drawings in force --------------------------------------------------------------
    unplaced = []
    for d in drawings:
        boq = (d.get("floor_boq") or {}).get("fire_alarm") or {"floors": []}
        for sheet in boq["floors"]:
            mult = int(sheet.get("multiplier") or 0)
            numbers = [int(n) for n in sheet.get("floors") or []]
            typical = mult > 1
            placeable = not typical or len(numbers) == mult
            if typical and not placeable:
                unplaced.append({"drawing": d["filename"], "revision": d.get("revision"), "sheet": sheet["sheet"],
                                 "floor_name": sheet["floor_name"], "multiplier": mult, "qty": sheet["qty"],
                                 "floors_read": numbers})
            for row in sheet["rows"]:
                dt = row["device_type"]
                name = device_in(dt["name"]) or dt["name"]
                dev = device(name)
                if not any(t["code"] == dt["code"] for t in dev["ifc_types"]):
                    dev["ifc_types"].append({"code": dt["code"], "name": dt["name"]})
                if not placeable:
                    continue
                if typical:
                    for n in numbers:
                        qty[name, floor(f"Level {n}", shown_as=sheet["floor_name"])]["ifc"] += row["per_floor"]
                else:
                    qty[name, floor(sheet["floor_name"])]["ifc"] += row["qty"]

    # --- laid out ------------------------------------------------------------------------------
    # The schedule's own floor order where it names the floor; the key's order otherwise.
    def floor_sort(key: str) -> tuple:
        if key in schedule_order:
            return (schedule_order[key], 0.0)
        # A floor only the drawings name goes after the schedule's floors below it.
        order = floors[key]["order"]
        below = [i for k, i in schedule_order.items() if k in floors and floors[k]["order"] <= order]
        return (max(below) if below else -1, 0.5 + order / 1e6)

    used = {k for (_, k), v in qty.items() if v["schedule"] or v["ifc"]}
    floor_list = [floors[k] for k in sorted(used, key=floor_sort)]

    unplaced_by_device: dict[str, float] = defaultdict(float)
    for d in drawings:
        boq = (d.get("floor_boq") or {}).get("fire_alarm") or {"floors": []}
        for sheet in boq["floors"]:
            mult = int(sheet.get("multiplier") or 0)
            if mult > 1 and len(sheet.get("floors") or []) != mult:
                for row in sheet["rows"]:
                    unplaced_by_device[device_in(row["device_type"]["name"]) or row["device_type"]["name"]] += row["qty"]

    out_devices = []
    for name, dev in devices.items():
        per_floor = []
        for f in floor_list:
            v = qty.get((name, f["key"]))
            if v and (v["schedule"] or v["ifc"]):
                per_floor.append({"floor": f["key"], "schedule": _tidy(v["schedule"]), "ifc": _tidy(v["ifc"]),
                                  "difference": _tidy(v["ifc"] - v["schedule"])})
        s_total = sum(v["schedule"] for (n, _), v in qty.items() if n == name)
        i_total = sum(v["ifc"] for (n, _), v in qty.items() if n == name) + unplaced_by_device.get(name, 0.0)
        out_devices.append({
            **dev,
            "schedule_total": _tidy(s_total), "ifc_total": _tidy(i_total),
            "ifc_unplaced": _tidy(unplaced_by_device.get(name, 0.0)),
            "difference": _tidy(i_total - s_total),
            "floors_differing": sum(1 for p in per_floor if p["difference"]),
            "floors": per_floor,
        })
    out_devices.sort(key=lambda d: (-(d["schedule_total"] + d["ifc_total"]), d["device"]))

    out_floors = []
    for f in floor_list:
        s = sum(v["schedule"] for (_, k), v in qty.items() if k == f["key"])
        i = sum(v["ifc"] for (_, k), v in qty.items() if k == f["key"])
        out_floors.append({**f, "schedule": _tidy(s), "ifc": _tidy(i), "difference": _tidy(i - s),
                           "devices_differing": sum(1 for d in out_devices for p in d["floors"]
                                                    if p["floor"] == f["key"] and p["difference"])})

    # One device's surplus mirroring another's shortfall, floor by floor, is
    # one symbol answered as the other ("M + S" as a call point): named, not guessed at.
    swaps = []
    for more in out_devices:
        if more["difference"] <= 0:
            continue
        surplus = {c["floor"]: c["difference"] for c in more["floors"] if c["difference"]}
        for fewer in out_devices:
            if fewer is more or fewer["difference"] != -more["difference"]:
                continue
            shortfall = {c["floor"]: -c["difference"] for c in fewer["floors"] if c["difference"]}
            if surplus == shortfall:
                swaps.append({"more": more["device"], "fewer": fewer["device"], "qty": more["difference"],
                              "floors": len(surplus), "ifc_types": [t["code"] for t in more["ifc_types"]]})

    s_all = sum(d["schedule_total"] for d in out_devices)
    i_all = sum(d["ifc_total"] for d in out_devices)
    return {
        "devices": out_devices,
        "floors": out_floors,
        "unplaced": unplaced,
        "swaps": swaps,
        "schedule_unnamed": unnamed,
        "totals": {"schedule": _tidy(s_all), "ifc": _tidy(i_all), "difference": _tidy(i_all - s_all),
                   "devices": len(out_devices),
                   "devices_matching": sum(1 for d in out_devices if not d["difference"] and not d["floors_differing"]),
                   "floors": len(out_floors),
                   "floors_matching": sum(1 for f in out_floors if not f["devices_differing"])},
    }
