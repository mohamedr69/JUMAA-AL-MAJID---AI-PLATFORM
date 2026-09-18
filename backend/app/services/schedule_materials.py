r"""The parts a floor-wise schedule line can be ordered as.

The schedule says what a line *is* -- "Smoke Detector", "Exit Light -
Above Door" -- and the Proposed Materials tab says what this project
orders. This is the list an engineer picks from to join the two, and it
is the project's own materials rather than any general catalogue: nothing
can be chosen that has not been proposed for the job.

Only **field devices** are offered -- the things installed on a floor and
counted there. A proposed materials list is mostly not that. One real
project's fire alarm materials run to forty parts, of which a dozen are
the panel's own:

    4-CPU              Central Processor Module        the panel
    4-FWAL4            Firewall                        the panel
    SIGA-AA50          Intelligent Audio Amplifier     the panel
    27193-11           Surface Mount Box               ordered with a device
    757A-WB            Weatherproof Box, Cast          ordered with a device
    SIGA-SB            Signature Detector Base         ordered with a device
    STI-3002           Weatherresistant Gasket         an accessory

Offering those would let a floor's smoke detectors be settled as a back
box, which is worse than offering nothing.

The test is in two parts, and the first does most of the work: a field
device is something the schedule's own vocabulary can name
(`symbol_taxonomy.device_in`). A central processor, a firewall, a filler
plate and a gasket name no device and fall out on their own. What
survives that and still is not a field device -- a panel's riser card, a
detector base, a master handset -- is named here.

**A line can be two parts.** "Smoke with Sounder Base" is a detector and
an audible base ordered together (SIGA-OSD-FCN + SIGA-LPS), so where a
project proposes both, the pair is offered as one choice.

The list is per system: the fire alarm tab offers the fire alarm's
materials, the emergency lighting tab its own.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.services.symbol_taxonomy import PANELS, device_in, family_of

# What is not a field device even though its wording names one. Each is
# either a part of the panel, or a thing ordered *with* a device rather
# than instead of it.
NOT_A_FIELD_DEVICE_RE = re.compile(
    # Ordered with a device: its base, its box, its cover.
    r"\bBASE\b|\bBACK\s*BOX\b|\bBACKBOX\b|\b(?:SURFACE|WEATHER\w*|MOUNTING|DEEP|CAST)\s*(?:MOUNT\s*)?BOX\b|"
    r"\bBOX\b|\bGASKET\b|\bSTOPPER\b|\bCOVER\b|\bGUARD\b|\bBRACKET\b|\bPLATE\b|\bPICTOGRAM\b|"
    r"\bACCESSOR(?:Y|IES)\b|\bENCLOSURE\b|\bSAMPLING\s*TUBE\b|\bSTORAGE\b|\bSPARE\b|\bKIT\b|"
    # The panel's own parts.
    r"\bAMPLIFIER\b|\bMICROPHONE\b|\bINTERFACE\b|\bCOMMON\s*RELAY\b|\bFILLER\b|\bDOOR\s*ASSEMBLY\b|"
    r"\bCONTROLLER\b|\bPROCESSOR\b|\bFIREWALL\b|\bNETWORK\b|\bUSB\b|\bBRIDGE\b|\bCABINET\b|\bCHASSIS\b|"
    r"\bDISPLAY\s*MODULE\b|\bANNUNCIATOR\b|\bPOWER\s*SUPPLY\b|\bBOOSTER\b|\bCHARGER\b|\bTRANSFORMER\b|"
    # Ordered by the calculation or by the metre, never by the floor.
    r"\bBATTER(?:Y|IES)\b|\bSEALED\s*LEAD\s*ACID\b|\bCABLE\b|\bCONDUIT\b|\bTRAY\b|"
    # Not a material at all.
    r"\bSOFTWARE\b|\bLICENC?SE\b|\bPROGRAMMING\b|\bCOMMISSION(?:ING)?\b|\bTRAINING\b|\bTESTING\b",
    re.IGNORECASE,
)

# A handset is the panel's, unless it is the socket on the wall that a
# fireman plugs into -- which is a field device and is counted per floor.
_HANDSET_RE = re.compile(r"\bHANDSET\b", re.IGNORECASE)
_SOCKET_RE = re.compile(r"\bRECEPTACLE\b|\bJACK\b|\bOUTLET\b", re.IGNORECASE)

# What a detector's audible base is called, and what pairing one with a
# detector is called on the schedule.
_SOUNDER_BASE_RE = re.compile(r"\b(?:AUDIBLE|SOUNDER)\b.{0,12}\bBASE\b|\bSOUNDER\s*BASE\b", re.IGNORECASE)


def is_device(part_no: str | None, description: str | None) -> bool:
    """Whether a proposed material is a field device counted on a floor."""
    text = f"{part_no or ''} {description or ''}".strip()
    if not text:
        return False
    # The schedule's own vocabulary must be able to name it.
    device = device_in(description) or device_in(part_no)
    if not device:
        return False
    # The panel itself is not installed on a floor.
    if family_of(device) is PANELS:
        return False
    if _HANDSET_RE.search(text) and not _SOCKET_RE.search(text):
        return False
    return not NOT_A_FIELD_DEVICE_RE.search(text)


def is_sounder_base(part_no: str | None, description: str | None) -> bool:
    """Whether a material is a detector's audible base -- not a device of
    its own, but half of "Smoke with Sounder Base"."""
    return bool(_SOUNDER_BASE_RE.search(f"{part_no or ''} {description or ''}"))


def device_materials(db: Session, project, system_code: str | None) -> list[dict]:
    """The project's proposed materials for one system, field devices only.

    Read straight from the database -- the BOQ's own parts and what an
    engineer added on the Proposed Materials tab. Deliberately *not*
    through that tab's listing endpoint: that one also finds each part's
    datasheet, which walks the manufacturer libraries on a synced drive
    and takes minutes. A dropdown needs a part number, a description and a
    brand, all of which are already here.
    """
    from app.extraction.identity import part_key
    from app.models import ProjectBoqItem, ProjectProposedMaterial

    wanted = (system_code or "").strip().upper()
    found: dict[str, dict] = {}
    bases: dict[str, dict] = {}

    def offer(part_no: str | None, description: str | None, manufacturer: str | None,
              system: str | None, source: str) -> None:
        part = (part_no or "").strip()
        if not part or not part_key(part):
            return                      # a heading or a description-only line
        if wanted and (system or "").strip().upper() != wanted:
            return
        entry = {
            "part_no": part,
            "description": (description or "").strip(),
            "manufacturer": manufacturer,
            "system_code": system,
            "source": source,
        }
        # A sounder base is kept aside: it is not a device of its own, and
        # it is half of a line the schedule does carry.
        if is_sounder_base(part, description):
            bases.setdefault(part, entry)
            return
        if not is_device(part, description):
            return
        # One row per part: the same part reaches the list from the BOQ and
        # from what an engineer added, and it is one thing to order.
        found.setdefault(part, entry)

    for line in db.query(ProjectBoqItem).filter(ProjectBoqItem.project_id == project.id).all():
        offer(line.catalog_no, line.description, line.manufacturer, line.system_code, "boq")
    for row in (db.query(ProjectProposedMaterial)
                .filter(ProjectProposedMaterial.project_id == project.id)
                .order_by(ProjectProposedMaterial.id).all()):
        offer(row.catalog_no, row.description, row.manufacturer, row.system_code, "added")

    return sorted(found.values(), key=lambda material: material["part_no"]) + _with_sounder_base(found, bases)


def _with_sounder_base(devices: dict[str, dict], bases: dict[str, dict]) -> list[dict]:
    """A smoke detector and an audible base, as the one line the schedule
    calls "Smoke with Sounder Base".

    The two are ordered together and counted once, so they are offered as
    one choice rather than leaving the engineer to settle the line as the
    detector alone and lose the base from the count.
    """
    detectors = [
        entry for entry in devices.values()
        if (device_in(entry["description"]) or device_in(entry["part_no"])) in (
            "Smoke detector", "Multisensor detector", "Heat detector")
    ]
    pairs: list[dict] = []
    for base in sorted(bases.values(), key=lambda entry: entry["part_no"]):
        for detector in sorted(detectors, key=lambda entry: entry["part_no"]):
            pairs.append({
                "part_no": f"{detector['part_no']} + {base['part_no']}",
                "description": f"{detector['description']} with {base['description'].lower()}".strip(),
                "manufacturer": detector["manufacturer"] or base["manufacturer"],
                "system_code": detector["system_code"],
                "source": "pair",
            })
    return pairs


def settle_unambiguous(db: Session, project, stored) -> int:
    """Settle every line the project's own materials leave no choice about.

    A line the project proposes exactly one part for is not a decision --
    there is nothing to choose between -- so it is settled here, once,
    rather than asked of the engineer every time the tab is opened. Where
    the project proposes two ceiling speakers, or five emergency light
    fittings, the line is the engineer's to settle and is left alone: a
    guess at which part a floor is ordered as is worse than a blank.

    A line the engineer has already settled is never touched. Returns how
    many lines were settled, and the caller commits.
    """
    result = dict(stored.result or {})
    items = [dict(item) for item in result.get("items") or []]
    if not items:
        return 0
    offered: dict[str | None, list[dict]] = {}
    settled = 0
    for item in items:
        device = item.get("device")
        if item.get("material") or not device:
            continue
        system = item.get("system")
        if system not in offered:
            offered[system] = device_materials(db, project, system)
        only = [material for material in offered[system]
                if (device_in(material["description"]) or device_in(material["part_no"])) == device]
        if len(only) != 1:
            continue
        item["material"] = {"part_no": only[0]["part_no"], "description": only[0]["description"],
                            "manufacturer": only[0]["manufacturer"]}
        settled += 1
    if settled:
        result["items"] = items
        stored.result = result          # reassigned, so the JSON change is seen
    return settled
