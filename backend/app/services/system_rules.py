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
CODE_ORDER = ("FAS", "VES", "PAVA", "ELS")
EMERGENCY_LIGHTING_ROWS = ("Central Battery System", "Emergency Light Monitoring")
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


def project_codes(project) -> list[str]:
    """The systems this project has: what its DRF marks, and what its Design
    Sheets and BOQ deliver, each under its effective code."""
    integrated = project_integrated(project)
    codes = set(codes_for_rows(project.systems, separate_panel=bool(getattr(project, "separate_ve_panel", False))))
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
