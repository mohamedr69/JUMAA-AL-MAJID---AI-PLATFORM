"""Which systems a project is, the one place every tab asks.

Two company rules decide it, on top of the DRF's Systems rows:

1. **An Edwards fire alarm is one integrated system.** The EST fire alarm
   panel carries the voice evacuation and the fire telephone itself, so
   Fire Alarm, Voice Evacuation and Fire Telephone are one system (FAS) with
   one Design Sheet, one BOQ, one compliance statement and one submittal --
   unless a separate voice evacuation panel is indicated: the engineer ticks
   "Separate voice evacuation panel" in Project Info, or the DRF gives Voice
   Evacuation a different brand from the fire alarm (EP-30175: a Cooper fire
   alarm with an Edwards voice evacuation). Fire Telephone always rides on
   the fire alarm's sheet.

2. **Emergency lighting is one system: ELS.** ELS (emergency lighting), CBS
   (central battery system) and EML (emergency light monitoring) are the same
   system whichever of them a document or the DRF names; it is kept as ELS and
   stands for both DRF rows, Central Battery System and Emergency Light
   Monitoring.

`effective_code` applies both to any code a document carries; `drf_rows` is
what a code stands for on this project's DRF; `project_codes` lists the
systems the project has, in the platform's order.
"""

from __future__ import annotations

from app.knowledge.policy import canonical_manufacturer

FIRE_ALARM = "Fire Alarm"
VOICE_EVACUATION = "Voice Evacuation"
FIRE_TELEPHONE = "Fire Telephone"
FAS_FAMILY_ROWS = (FIRE_ALARM, VOICE_EVACUATION, FIRE_TELEPHONE)

# The platform's order, and the DRF rows each code stands for when nothing is integrated.
CODE_ORDER = ("FAS", "VES", "PAVA", "ELS", "FRC")
# Fire-rated cables are not a DRF row: they are the company's on every
# project whose scope is the full package (design, supply, installation),
# so such a project has FRC as a system -- its own material submittal, its
# own proposed materials -- with nothing in them until the engineer fills them.
FULL_PACKAGE = "fullpackage"
# The two kinds of emergency lighting, one system (ELS) but not one product:
# the DRF's "Emergency Light Monitoring" row is a monitored self-contained
# system (every luminaire carries its own battery -- Menvier -- so there is
# no battery calculation to make), and "Central Battery System" is a central
# battery unit feeding the luminaires, which will have one.
SELF_CONTAINED_ROW = "Emergency Light Monitoring"
CENTRAL_BATTERY_ROW = "Central Battery System"
EMERGENCY_LIGHTING_ROWS = (CENTRAL_BATTERY_ROW, SELF_CONTAINED_ROW)
SELF_CONTAINED, CENTRAL_BATTERY = "self_contained", "central_battery"
ELS_KIND_NAMES = {SELF_CONTAINED: "Monitored Self-Contained Emergency Light System",
                  CENTRAL_BATTERY: "Central Battery System"}
BASE_ROWS: dict[str, tuple[str, ...]] = {
    "FAS": (FIRE_ALARM, FIRE_TELEPHONE),
    "VES": (VOICE_EVACUATION,),
    "PAVA": ("PA/VA & BGM",),
    "ELS": EMERGENCY_LIGHTING_ROWS,
}
CODE_NAMES = {
    "FAS": "Fire Alarm",
    "VES": "Voice Evacuation",
    "PAVA": "Public Address & Voice Alarm",
    "ELS": "Emergency Lighting",
    "FRC": "Fire Rated Cables",
}
# Spellings documents use for the same system.
ALIASES = {
    "PA": "PAVA", "VA": "PAVA", "VAS": "PAVA",
    "VE": "VES",
    "FA": "FAS", "FT": "FAS", "FTS": "FAS",
    "CBS": "ELS", "EML": "ELS", "ELM": "ELS", "EL": "ELS", "EMLSC": "ELS",
}


def canonical(code: str | None) -> str | None:
    """A code in the platform's spelling, before any project rule."""
    if not code:
        return None
    upper = code.strip().upper()
    return ALIASES.get(upper, upper) or None


def _brand(systems, row: str) -> str | None:
    for system in systems or ():
        name = system.get("name") if isinstance(system, dict) else getattr(system, "name", None)
        if name == row:
            brand = system.get("brand") if isinstance(system, dict) else getattr(system, "brand", None)
            return (brand or "").strip() or None
    return None


def _marked(systems, row: str) -> bool:
    return any((s.get("name") if isinstance(s, dict) else getattr(s, "name", None)) == row for s in systems or ())


def voice_evacuation_integrated(systems, *, separate_panel: bool = False) -> bool:
    """Whether Voice Evacuation is part of the fire alarm system (rule 1).

    `systems` are the DRF rows (ProjectSystem objects or dicts with name and
    brand), so the same answer is given before a project exists (the review
    form) and after."""
    if separate_panel:
        return False
    fire_alarm = canonical_manufacturer(_brand(systems, FIRE_ALARM))
    if fire_alarm != "EDWARDS":
        return False
    voice = _brand(systems, VOICE_EVACUATION)
    return voice is None or canonical_manufacturer(voice) == "EDWARDS"


def project_integrated(project) -> bool:
    return voice_evacuation_integrated(project.systems, separate_panel=bool(getattr(project, "separate_ve_panel", False)))


def effective_code(code: str | None, project=None, *, integrated: bool | None = None) -> str | None:
    """The system a document's code belongs to on this project (rules 1 and 2)."""
    result = canonical(code)
    if result == "VES":
        if integrated is None:
            integrated = project_integrated(project) if project is not None else False
        if integrated:
            return "FAS"
    return result


def drf_rows(code: str | None, project=None, *, integrated: bool | None = None) -> tuple[str, ...]:
    """The DRF rows a code stands for on this project."""
    result = canonical(code)
    if result == "FAS":
        if integrated is None:
            integrated = project_integrated(project) if project is not None else False
        return FAS_FAMILY_ROWS if integrated else BASE_ROWS["FAS"]
    return BASE_ROWS.get(result or "", ())


def codes_for_rows(systems, *, separate_panel: bool = False) -> list[str]:
    """The system codes a set of DRF rows amounts to, in the platform's order."""
    integrated = voice_evacuation_integrated(systems, separate_panel=separate_panel)
    found = set()
    for code, rows in BASE_ROWS.items():
        if any(_marked(systems, row) for row in rows):
            found.add(effective_code(code, integrated=integrated))
    if integrated and _marked(systems, VOICE_EVACUATION):
        found.add("FAS")
    return [code for code in CODE_ORDER if code in found]


def emergency_lighting_kinds(systems) -> set[str]:
    """Which kinds of emergency lighting the DRF marks: {"self_contained",
    "central_battery"}, either, both or neither."""
    kinds = set()
    if _marked(systems, SELF_CONTAINED_ROW):
        kinds.add(SELF_CONTAINED)
    if _marked(systems, CENTRAL_BATTERY_ROW):
        kinds.add(CENTRAL_BATTERY)
    return kinds


def battery_calculation_applies(project, system_code: str | None) -> tuple[bool, str | None]:
    """Whether a system's submittal encloses a battery calculation, and why
    not. The fire alarm's does (the panel, APS and BPS batteries). A
    monitored self-contained emergency light system has none: each
    luminaire carries its own battery. A central battery system will have
    one of its own when it is built; until then none is enclosed."""
    code = effective_code(system_code, project)
    if code == "FAS":
        return True, None
    if code == "ELS":
        kinds = emergency_lighting_kinds(getattr(project, "systems", None) or ())
        if kinds == {SELF_CONTAINED}:
            return False, "A monitored self-contained emergency light system has no battery calculation: every luminaire carries its own battery."
        if CENTRAL_BATTERY in kinds:
            return False, "The central battery system's battery calculation is not built yet."
        return False, "Emergency lighting has no battery calculation on this project."
    return False, f"{CODE_NAMES.get(code or '', code or 'This system')} has no battery calculation."


def system_display_name(project, system_code: str | None) -> str:
    """The system's name as the project has it: emergency lighting by its
    kind (monitored self-contained / central battery), the rest by code."""
    code = effective_code(system_code, project) or ""
    if code == "ELS":
        kinds = emergency_lighting_kinds(getattr(project, "systems", None) or ())
        if kinds:
            return " & ".join(ELS_KIND_NAMES[k] for k in (SELF_CONTAINED, CENTRAL_BATTERY) if k in kinds)
    return CODE_NAMES.get(code, code or "Unassigned")


def is_full_package(project) -> bool:
    """Whether the project's scope of work is the full package, in any
    spelling the DRF or the form gives it ("Full Package", "fullpackage")."""
    import re

    scope = re.sub(r"[^a-z]", "", str(getattr(project, "scope_of_work", None) or "").lower())
    return scope == FULL_PACKAGE


def project_codes(project) -> list[str]:
    """The systems this project has: what its DRF marks, what its Design
    Sheets and BOQ deliver, each under its effective code -- and the
    fire-rated cables on a full-package project."""
    integrated = project_integrated(project)
    codes = set(codes_for_rows(project.systems, separate_panel=bool(getattr(project, "separate_ve_panel", False))))
    if is_full_package(project):
        codes.add("FRC")
    for sheet in getattr(project, "design_sheets", []) or []:
        codes.add(effective_code(sheet.system_code, integrated=integrated))
    for item in getattr(project, "boq_items", []) or []:
        codes.add(effective_code(item.system_code, integrated=integrated))
    return [code for code in CODE_ORDER if code in codes] + sorted(c for c in codes if c and c not in CODE_ORDER)


def normalize_project(project) -> int:
    """Bring the codes a project's documents carry to their effective codes.
    Returns how many were changed. The caller commits."""
    integrated = project_integrated(project)
    changed = 0
    holders = list(getattr(project, "design_sheets", []) or []) + list(getattr(project, "boq_items", []) or [])
    holders += list(getattr(project, "submittals", []) or [])
    for holder in holders:
        current = getattr(holder, "system_code", None)
        wanted = effective_code(current, integrated=integrated)
        if current and wanted != current:
            holder.system_code = wanted
            changed += 1
    return changed
