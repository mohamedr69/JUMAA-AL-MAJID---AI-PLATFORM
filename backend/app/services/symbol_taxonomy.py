r"""How a floor schedule is laid out: families, and what each column is called.

An engineer reading a floor schedule does not want a column per device
type in the platform's own vocabulary. They want the three or four
families a fire alarm job is ordered in, and under each the variants that
are actually separate lines:

    Detectors        Smoke Detector | Smoke with Sounder Base | Heat Detector
                     Multisensor | Multisensor + Sounder Base
    Speaker          Wall Speaker | Ceiling Speaker | Wall Sounder Flasher WP
                     Wall Sounder Flasher WP (Driveway)
    Pull station     Manual Pull Station | Manual Pull Station WP

Two levels, and they are not the same thing as a device type. "Detectors"
spans three device types; "Wall Speaker" and "Ceiling Speaker" are one
device type told apart by a property; "Wall Sounder Flasher WP
(Driveway)" is one device type told apart by a property *and* by where it
sits (`app.services.symbol_rooms`).

So this module holds the two things a schedule needs and the rest of the
pipeline does not: which family a device belongs under, and what one
variant of it is called once its properties are known.
"""

from __future__ import annotations

import re

# --- families -----------------------------------------------------------------------------

DETECTORS = "Detectors"
SPEAKER = "Speaker"
PULL_STATION = "Pull station"
LIGHT = "Exit & emergency light"
TELEPHONE = "Fire telephone"
MODULES = "Modules & isolators"
PANELS = "Panels & power"
OTHER = "Other"

# Which family each device the platform names belongs under. A device not
# listed here falls under "Other", where it is visible rather than lost.
FAMILIES: dict[str, str] = {
    "Smoke detector": DETECTORS,
    "Smoke detector with sounder base": DETECTORS,
    "Duct smoke detector": DETECTORS,
    "Heat detector": DETECTORS,
    "Multisensor detector": DETECTORS,
    "Speaker": SPEAKER,
    "Speaker/strobe": SPEAKER,
    "Sounder": SPEAKER,
    "Horn/strobe": SPEAKER,
    "Strobe": SPEAKER,
    "Manual call point": PULL_STATION,
    "Exit light": LIGHT,
    "Emergency light": LIGHT,
    "Fire telephone": TELEPHONE,
    "Monitor module": MODULES,
    "Control module": MODULES,
    "Monitor/control module": MODULES,
    "Isolator": MODULES,
    "Door holder": MODULES,
    "Control panel": PANELS,
    "Repeater panel": PANELS,
    "Power supply": PANELS,
}

# The order the families are shown in, which is the order a BOQ is read in.
FAMILY_ORDER: tuple[str, ...] = (DETECTORS, PULL_STATION, SPEAKER, LIGHT, TELEPHONE, MODULES, PANELS, OTHER)


# Which system each family is ordered and submitted under. A fire alarm
# job and an emergency lighting job are two BOQs, two submittals and two
# consultants' approvals, so a floor-wise schedule is read as two -- the
# platform's codes, so ELS/FAS spell the same here as everywhere else
# (`app.services.system_rules`).
FAMILY_SYSTEMS: dict[str, str] = {LIGHT: "ELS"}
DEFAULT_SYSTEM = "FAS"


def system_of(device: str | None) -> str | None:
    """The system a device is ordered under: "FAS" or "ELS".

    None for a device the platform does not recognise -- such a line is
    still shown and still counted, under neither system, rather than
    disappearing between the two.
    """
    if not (device or "").strip():
        return None
    return FAMILY_SYSTEMS.get(family_of(device), DEFAULT_SYSTEM)


def family_of(device: str | None) -> str:
    """The family a device is ordered under."""
    return FAMILIES.get((device or "").strip(), OTHER)


def family_rank(family: str) -> int:
    return FAMILY_ORDER.index(family) if family in FAMILY_ORDER else len(FAMILY_ORDER)


# --- what one variant is called ---------------------------------------------------------------

# The name a device type goes by in a schedule, where it differs from the
# platform's own name for it. "Smoke detector with sounder base" is a long
# way of writing what a BOQ calls "Smoke with Sounder Base".
BASE_NAMES: dict[str, str] = {
    "Smoke detector": "Smoke Detector",
    "Smoke detector with sounder base": "Smoke with Sounder Base",
    "Duct smoke detector": "Duct Smoke Detector",
    "Heat detector": "Heat Detector",
    "Multisensor detector": "Multisensor",
    "Manual call point": "Manual Pull Station",
    "Speaker": "Speaker",
    "Sounder": "Sounder",
    "Speaker/strobe": "Speaker Strobe",
    "Horn/strobe": "Horn Strobe",
    "Strobe": "Flasher",
    "Exit light": "Exit Sign",
    "Emergency light": "Emergency Light",
    "Fire telephone": "Telephone Jack",
}

# The families whose variants are told apart by how they are mounted: a
# ceiling speaker and a wall speaker are two lines. A detector is mounted
# on a ceiling whatever else is true of it, so saying so adds nothing.
MOUNTED_FAMILIES = (SPEAKER, PULL_STATION, LIGHT)

# The add-ons worth naming in a heading, and what to call them. An add-on
# the device's own name already carries is not repeated.
ADDON_NAMES: dict[str, str] = {
    "sounder base": "Sounder Base",
    "flasher": "Flasher",
    "sounder": "Sounder",
    "isolator base": "Isolator Base",
}


def canonical(device: str | None, properties: dict | None = None, room: str | None = None) -> str:
    """What to call one variant of a device, given what the drawing said
    about it.

    The name is built rather than looked up, so a combination nobody has
    seen before still gets a sensible heading: the mounting, then the
    device, then what it carries, then whether it is weatherproof, then --
    last -- the room, when it is the room that made it its own line.
    """
    device = (device or "").strip()
    properties = properties or {}
    family = family_of(device)
    base = BASE_NAMES.get(device, device or "Unnamed symbol")

    words: list[str] = []
    mounting = properties.get("mounting")
    if mounting and family in MOUNTED_FAMILIES:
        words.append(str(mounting).title())
    elif properties.get("environment") == "indoor" and family in MOUNTED_FAMILIES:
        # Where the drawing gave no mounting but did say indoor, that is
        # what tells this variant from the weatherproof one.
        words.append("Indoor")

    add_ons = [ADDON_NAMES[a] for a in (properties.get("add_ons") or []) if a in ADDON_NAMES]
    # An add-on the device's own name already says is not said twice.
    add_ons = [a for a in add_ons if a.lower() not in base.lower()]

    if family is DETECTORS and add_ons:
        # A detector's add-on is the thing that makes it another line, and
        # a BOQ writes it with a plus: "Multisensor + Sounder Base".
        name = f"{base} + {' + '.join(add_ons)}"
    else:
        words.append(base)
        words.extend(add_ons)
        name = " ".join(words)

    if properties.get("environment") == "outdoor":
        name = f"{name} WP"
    direction = properties.get("direction")
    if direction and family is LIGHT:
        name = f"{name} ({str(direction).title()})"
    if room:
        name = f"{name} ({str(room).title()})"
    return name


# --- what a piece of text names -----------------------------------------------------------

# The devices the company builds with, and the words a schedule calls them
# by. The order matters: a smoke detector with a sounder base is not a
# smoke detector, so the combinations come first, and an add-on does not
# change what the device is -- a speaker with a flasher is a speaker.
#
# This came from the drawings route's symbol recogniser when that was
# removed; a schedule's own wording is now the only thing read.
DEVICE_RULES: tuple[tuple[str, str], ...] = (
    ("Smoke detector with sounder base", r"(SMOKE|SD).{0,20}(SOUNDER|SOUNDER BASE|AUDIBLE BASE|WITH SOUNDER)|SOUNDER BASE"),
    ("Duct smoke detector", r"DUCT|SIGA-?SD\b|\bSD-?T\d"),
    ("Multisensor detector", r"MULTI[- ]?SENSOR|MULTISENSOR|MULTI[- ]?DETECTOR|SMOKE\s*(?:AND|&|\+)\s*HEAT|OSHD|3D\b"),
    ("Smoke detector", r"SMOKE|PHOTO|\bSD\b|OSD|SIGA-?PS\b"),
    ("Heat detector", r"\bHEAT\b|\bHD\b|HRS\b|HRD\b|THERMAL|RATE OF RISE"),
    ("Manual call point", r"MANUAL CALL|CALL[- ]?POINT|\bMCP\b|PULL[- ]?STATION|BREAK[- ]?GLASS|\bBGU\b|SIGA-?278"),
    ("Speaker/strobe", r"SPEAKER.{0,6}STROBE|SPK.{0,4}STR|SS70|757-\d+A-SS"),
    ("Horn/strobe", r"HORN.{0,6}STROBE|\bHS\b|757-\d+A-T|G1AV|WSTIA"),
    ("Speaker", r"SPEAKER|\bSPK\b|S186|G4S"),
    ("Sounder", r"SOUNDER|\bBELL\b|HOOTER|HORN|G1A"),
    # A flasher on a speaker or a sounder is an add-on, not a different
    # device: the base device is named first, so this is reached only
    # when nothing else is (app.services.symbol_properties reads the
    # flasher as a property of the symbol).
    ("Strobe", r"STROBE|BEACON|FLASHER|XENON|202-\d"),
    ("Fire telephone", r"TELEPHONE|\bFT\b|6833|6830|TCS-\d"),
    ("Monitor module", r"MONITOR MODULE|INPUT MODULE|\bMM\b|SIGA-?CT|SIGA-?CC"),
    ("Control module", r"CONTROL MODULE|OUTPUT MODULE|RELAY|\bCM\b|SIGA-?CR|SIGA-?IO|SIGA-?UM"),
    ("Isolator", r"ISOLATOR|\bIB\b"),
    ("Door holder", r"DOOR[- ]?HOLD|MAGNET"),
    ("Repeater panel", r"REPEATER|ANNUNCIATOR|\bANN\b"),
    ("Control panel", r"\bFACP\b|CONTROL PANEL|\bEST4\b|FIRE ALARM PANEL"),
    ("Power supply", r"\bBPS\b|\bAPS\b|POWER SUPPLY|BOOSTER"),
    ("Exit light", r"\bEXIT\b"),
    ("Emergency light", r"EMERGENCY LIGHT|\bEMERGENCY\b|\bEM LIGHT\b"),
)


def device_in(text: str | None) -> str | None:
    """The device a piece of text names, or None."""
    if not text:
        return None
    for device, pattern in DEVICE_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return device
    return None
