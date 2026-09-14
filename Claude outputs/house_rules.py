"""The company's answering rules, in one place.

Both model prompts are built from these strings -- the batched answer in
`compliance.assist` and the single-clause suggestion in `knowledge.review`.
Keeping them here is the point: the wording rules change often, they must
change for both paths at once, and an engineer editing them should not have
to read either module.

Editing anything here changes what the model is told. Bump PROMPT_VERSION in
the module that uses it, or the cache will keep serving answers made under
the old rules.
"""

from __future__ import annotations

from app.compliance.statements import RESPONSES

# --- the response vocabulary ----------------------------------------------

RESPONSE_RULES = (
    "RESPONSE, one of:\n"
    "- Comply: the offered system, material or work meets the clause.\n"
    "- Noted: an informative clause with nothing to supply (definitions, references, related "
    "sections, general contract conditions).\n"
    "- Complied with remark: met with a qualification or an equivalent; name it in the remark.\n"
    "- Not applicable: the clause asks for something this project's design and BOQ do not include.\n"
    "- By others: the work belongs to another trade (containment, wiring, power supply, panel "
    "fixing, builder's work, authority connection, BMS vendor).\n"
    "- Deviation: the offered product does not meet the clause; say why.\n"
    "- Clarification required: the information given cannot decide it; say what is missing.\n"
)

# --- the remark -----------------------------------------------------------

REMARK_RULES = (
    "REMARK. Leave it empty unless it carries something the response does not. Write one when the "
    "clause turns on an approval or listing, a standard, a specific product, or a system capability, "
    "and always for By others, Complied with remark, Deviation and Clarification required.\n"
    "A product remark takes this shape: Comply with proposed <approvals> <brand> <item>"
    "[, <second item>][ in accordance with <standard>][, with <capability> capability]. For example:\n"
    "  Comply with proposed UL Listed & DCD approved EST4 panel.\n"
    "  Comply with proposed UL Listed & DCD approved EST4 panel in accordance with NFPA 72.\n"
    "  Comply with proposed UL Listed & DCD approved EST4 system manufactured by Edwards.\n"
    "  Comply with proposed UL Listed & DCD approved EST4 panels with FireWorks Graphic Command "
    "Centre, with peer to peer network capability.\n"
    "A By others remark is: Will be coordinated with MEP contractor. Name the actual trade when it "
    "is not the MEP contractor. Do not write out the scope boundary or list the excluded works.\n"
    "Name the brand only, never its parent company: write Edwards, not Edwards (Carrier) and not "
    "Carrier. Name an approval only where the BOQ or a datasheet shows it for that exact model. Do "
    "not repeat the clause wording, its ratings, quantities or sequence of operation. Name one "
    "capability at most, and name it rather than explain it. Never write Noted, As per specification "
    "or Refer to submittal as a remark. At most 25 words; shorter is better; empty when none is "
    "needed.\n"
)

# --- the line that must not be crossed -------------------------------------

HONESTY_RULE = (
    "A deviation is never hidden by leaving the remark empty: use Deviation or Complied with remark "
    "and say it in one short line. Never invent a product, value, approval, listing or certificate "
    "that is not in the BOQ or the datasheets, and never carry a model number this project's BOQ "
    "does not offer.\n"
)

# --- reusing a past answer --------------------------------------------------

PRECEDENT_RULE = (
    "A past answer shows how the company answered before; it is not evidence about this project. "
    "Before reusing one, check its numeric limits, wiring class or style, standard editions and "
    "model numbers against this clause; when any differ, decide afresh from the project facts and "
    "the BOQ.\n"
)

assert set(RESPONSES) == {
    "Comply", "Noted", "Complied with remark", "Not applicable",
    "By others", "Deviation", "Clarification required",
}, "RESPONSE_RULES is out of step with statements.RESPONSES"
