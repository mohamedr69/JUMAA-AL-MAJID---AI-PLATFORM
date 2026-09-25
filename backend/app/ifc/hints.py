"""What a symbol's own words say it is: its letters and its block names.

The matcher recognises a symbol by its drawing, against the library. A
symbol the library has never seen gets no suggestion from it, and is
answered by picking one of forty device types from a list -- which is how
"M + S" on block MSS, a multi-sensor detector, was once answered as a
manual call point and put 602 call points on EP-30880.

The drawing's author usually wrote what the symbol is: "SMOKE DETECTOR",
"BREAK GLASS WP", "MSS", "CEILING SPEAKER". This module reads those words,
by fixed rules, into:

  * a hint -- the device type the words name, offered as a suggestion on
    Verify Symbols. Never counted without an engineer's answer.
  * a family -- smoke, heat, multi-sensor, call point, speaker, sounder...
    An answer in another family than the symbol's own words is flagged as
    a conflict, while answering and afterwards. A smoke detector answered
    "smoke detector with sounder base" is the same family: no flag.

Rules are tried in order, the specific before the general ("smoke detector
with sounder base" before "sounder", "multi-sensor" before "smoke").
"""
from __future__ import annotations

import re

from .dxf.loose import LOOSE_NAME

# (family, device type code, pattern) -- first match wins.
RULES: list[tuple[str, str, re.Pattern]] = [(f, c, re.compile(p, re.I)) for f, c, p in [
    ("multi-sensor", "MD", r"MULTI[\s-]*(SENS|CRITERIA)|\bMSS?\b|\bMSD\b|\bM\s*\+\s*S\b|SMOKE\s*\+\s*HEAT"),
    ("smoke", "SDS", r"SMOKE.{0,25}SOUNDER|\bSDS\b|\bSDSB\b"),
    ("heat", "HDS", r"HEAT.{0,25}SOUNDER|\bHDS\b"),
    ("smoke", "DD", r"\bDUCT\b.{0,25}(DET|SMOKE)|\bDSD\b"),
    ("smoke", "BD", r"\bBEAM\b.{0,25}DET"),
    ("smoke", "ASD", r"ASPIRAT|\bASD\b|VESDA"),
    ("flame", "FD", r"\bFLAME\b"),
    ("gas", "GD", r"\bGAS\b.{0,10}DET"),
    ("heat", "HD", r"\bHEAT\b|\bHD\b|\bROR\b|RATE\s*OF\s*RISE"),
    ("smoke", "SD", r"\bSMOKE\b|\bSD\b|\bOPTICAL\b|\bPHOTO(ELECTRIC)?\b"),
    ("call point", "MCP-WP", r"(BREAK\s*GLASS|\bMCP\b|CALL\s*POINT|PULL\s*STATION).{0,25}(\bWP\b|W/P|WEATHER)"),
    ("call point", "MCP", r"BREAK\s*GLASS|\bMCP\b|CALL\s*POINT|PULL\s*STATION|\bBGU?\b"),
    ("telephone", "FTH", r"HANDSET"),
    ("telephone", "FTJ", r"TELEPHONE|\bFTJ\b|FIRE\s*PHONE|\bTEL\b|PHONE\s*JACK"),
    ("speaker", "SPKS", r"(SPEAKER|SPKR|\bSPK).{0,25}(STROBE|FLASH)"),
    ("speaker", "SPKW", r"WALL.{0,20}(SPEAKER|SPKR)|\bSPKW\b|\bHORN\b"),
    ("speaker", "SPK", r"SPEAKER|SPKR|\bSPK\b"),
    ("sounder", "SNS", r"SOUNDER.{0,25}(STROBE|FLASH|BEACON)|\bSNS\b"),
    ("sounder", "SND", r"SOUNDER|\bSND\b|\bBELL\b"),
    ("sounder", "STB", r"STROBE|FLASHER|\bSTRB\b|BEACON|\bVAD\b"),
    ("module", "MIM", r"MONITOR|INPUT\s*MOD|\bMIM\b|\bMM\b"),
    ("module", "CM", r"CONTROL\s*MOD|OUTPUT\s*MOD|RELAY\s*MOD|(?<![\d.]\s)(?<![\d.])\bCM\b|\bCRM\b"),
    ("module", "ISO", r"ISOLAT|\bISO\b"),
    ("panel", "RP", r"REPEATER|ANNUNCIAT|\bRFACP\b|MIMIC"),
    ("panel", "VEP", r"VOICE\s*EVAC.{0,15}PANEL|\bVEP\b"),
    ("panel", "FACP", r"\bFACP\b|FIRE\s*ALARM\s*(CONTROL\s*)?PANEL|\bFAP\b"),
    ("panel", "PSU", r"POWER\s*SUPPLY|\bPSU\b"),
    ("door holder", "DH", r"DOOR\s*HOLD|MAGNETIC\s*DOOR"),
    ("interface", "FS", r"FLOW\s*SWITCH"),
    ("interface", "TS", r"TAMPER"),
    ("indicator", "RI", r"REMOTE\s*(INDICATOR|LED|LAMP)"),
    ("end of line", "EOL", r"END\s*OF\s*LINE|\bEOL\b"),
]]

# Letters inside a symbol that name it on their own, too short for the rules above.
LETTERS: dict[str, str] = {"OS": "SD", "H": "HD", "CS": "SPK", "M + S": "MD", "M+S": "MD"}

# Architecture, whatever letters it carries: "SD" on a door tag is a sliding
# door, "TEL DP" a telephone distribution point, "35 CM" a column size. A
# symbol with a name like these gets no hint at all.
_ARCHITECTURE = re.compile(
    r"D[O0]{2,}R|\bTAG\b|WINDOW|\bCOL\b|COLUMN|STAIR|\bROOM\b|\bBATH|KITCHEN|\bBED\b|PARKING|\bPARK\b|"
    r"\bRAMP\b|\bPLOT\b|\bTREE|FURN|\bCORE\b|\bSERV\b|\bDP\b|\bLIFT\b|SHAFT|\bWC\b|TOILET|\bSINK\b", re.I)
# Device letters are a tag, not a sentence: a longer text is a note or a room name.
MAX_LETTERS = 12

# Names that say nothing: anonymous blocks, and the one shared by every symbol drawn without a block.
_ANONYMOUS = re.compile(r"^\*U\d+$|^A\$C[0-9A-F]+$", re.I)


def _family_of_code(code: str) -> str | None:
    for family, c, _ in RULES:
        if c == code:
            return family
    return None


def read(text: str) -> tuple[str, str] | None:
    """(family, code) that a piece of text names, or None."""
    for family, code, pattern in RULES:
        if pattern.search(text):
            return family, code
    return None


def family_of_type(code: str, name: str) -> str | None:
    """The family of a device type, by its name first ("Sounder Strobe WP"
    on code SS), then its code."""
    found = read(name) or read(code)
    return found[0] if found else _family_of_code(code.upper())


def _block_words(name: str) -> str | None:
    """A block's own name, without the xref it came in through
    ('XREF - title block$0$MSS' -> 'MSS'); None for an anonymous block."""
    if name == LOOSE_NAME:
        return None
    last = name.split("$0$")[-1].strip()
    return None if not last or _ANONYMOUS.match(last) else last


def hint(group: dict, codes: dict[str, dict]) -> dict | None:
    """What the group's letters and block names say it is, as a device type
    of the library (`codes`: code -> device type out), or None.
    {"device_type", "family", "reason"}."""
    label = " ".join(str(group.get("label") or "").split()).upper()
    names = [w for w in (_block_words(n) for n in group.get("block_names") or {}) if w]
    if _ARCHITECTURE.search(label) or any(_ARCHITECTURE.search(n) for n in names):
        return None
    sources: list[tuple[str, str]] = []
    if label and len(label) <= MAX_LETTERS:
        sources.append((label, f"letters '{label}'"))
    for words in names:
        sources.append((words.upper(), f"block name '{words}'"))
    for text, why in sources:
        code = LETTERS.get(text) if why.startswith("letters") else None
        found = (_family_of_code(code), code) if code else read(text)
        if found and found[1] in codes:
            return {"device_type": codes[found[1]], "family": found[0], "reason": why}
    return None


def readings(group: dict) -> tuple[tuple[str, str] | None, set[tuple[str, str]]]:
    """What the letters say (family, code), and what each block name says,
    read separately -- for the deterministic rule, which wants two
    independent sources to agree. Nothing at all for a name or letters
    that read as architecture."""
    label = " ".join(str(group.get("label") or "").split()).upper()
    names = [w for w in (_block_words(n) for n in group.get("block_names") or {}) if w]
    if _ARCHITECTURE.search(label) or any(_ARCHITECTURE.search(n) for n in names):
        return None, set()
    letters = None
    if label and len(label) <= MAX_LETTERS:
        code = LETTERS.get(label)
        letters = (_family_of_code(code), code) if code else read(label)
    return letters, {found for found in (read(n.upper()) for n in names) if found}


def conflict(group: dict) -> str | None:
    """Why an answered symbol's device type disagrees with its own words, or None."""
    h = group.get("name_hint")
    dt = group.get("device_type")
    if not h or not dt or group.get("status") != "verified":
        return None
    answered = family_of_type(dt["code"], dt["name"])
    if answered is None or answered == h["family"]:
        return None
    return (f"Answered as {dt['code']} ({dt['name']}), but its {h['reason']} reads as "
            f"{h['device_type']['code']} ({h['device_type']['name']})")
