"""The autofill eligibility policy, and the vocabulary the knowledge base
and the compliance statement share.

A historical response is a draft an engineer can start from only when the
record it comes from is clean enough to trust without opening the source
PDF. The source's own review flags say when it is not: text read by OCR,
answers paired to clauses by page position rather than a table cell, table
columns inferred without a header, rows merged across a page break, or
statuses that conflict between sources. So a response is ELIGIBLE for
autofill only when every one of these holds:

1. it is active and at least one of its source links is current -- not
   superseded by a later revision of the same document;
2. its review flag is empty;
3. at least one current source link comes from a high-confidence pairing
   read from native text (extraction method A_table or B_annot, not the
   reading-order or OCR methods C_* / D_*);
4. its historical status is one the statement can carry as written:
   Comply, Comply (affirmative), Noted, or Comply with Qualification --
   "Not Applicable", "By Others", a deviation or free text depend on the
   project, so they are shown as candidates, never filled;
5. the manufacturer is known, unless the answer is "Noted", which commits
   to no product;
6. the requirement text is at least 20 characters, so a fragment cannot
   stand for a clause;
7. neither the response nor its requirement is named in the source's
   review issues (conflicting responses), and no document it was read from
   is flagged as a whole (manufacturer uncertain, system uncertain, garbled
   text); issues about the family or section being unknown do not touch the
   wording and do not block.

Eligibility is about the record. Whether an eligible response fits THIS
project -- its manufacturer, its models, its scope -- is decided again at
autofill time (app.knowledge.autofill), and a match is always a draft: the
historical status is proposed, never a verified conclusion.
"""

from __future__ import annotations

# --- what a historical status becomes on the statement -------------------------------

# The historical status, as the source normalised it, to the response the
# statement carries (app.compliance.statements.RESPONSES) and the technical
# status it proposes.
STATUS_TO_RESPONSE = {
    "Comply": ("Comply", "complies"),
    "Comply (affirmative)": ("Comply", "complies"),
    "Comply with Qualification": ("Complied with remark", "partially_complies"),
    "Partially Comply": ("Complied with remark", "partially_complies"),
    "Noted": ("Noted", "not_applicable"),
    "Not Applicable": ("Not applicable", "not_applicable"),
    "By Others": ("By others", "not_applicable"),
    "Not Comply / Deviation": ("Deviation", "does_not_comply"),
    "Other / Free text": ("", "insufficient_evidence"),
}
FILLABLE_STATUSES = ("Comply", "Comply (affirmative)", "Noted", "Comply with Qualification")
TECHNICAL_STATUSES = ("complies", "does_not_comply", "partially_complies", "insufficient_evidence", "not_applicable")
WORKFLOW_STATUSES = ("unfilled", "autofilled", "candidate", "ai_pending", "reviewed", "recheck")

# Review issues about identity, not wording: they do not block a record.
NON_BLOCKING_ISSUES = ("Specification family unknown", "Specification section number missing")
# Issues logged against a source document that describe the whole document
# -- every response read from it inherits them. Row-level issues logged
# against a source ("missing/short requirement text") are not inherited:
# the requirement-length rule catches the rows they are about.
SOURCE_BLOCKING_ISSUES = ("Manufacturer uncertain", "Uncertain system classification", "Garbled text")
# Extraction methods read from native text with a table cell or a bracketed
# annotation; the rest are reading order or OCR.
TRUSTED_METHODS = ("A_table", "B_annot")
MIN_REQUIREMENT_CHARS = 20


def eligibility_reasons(*, review_flag: str | None, status: str | None, manufacturer: str | None,
                        requirement_chars: int, links: list[tuple[str | None, str | None, bool]],
                        issue_types: set[str]) -> list[str]:
    """Why a response cannot be autofilled; empty when it can. `links` are
    (pairing_confidence, extraction_method, superseded) per source link."""
    reasons: list[str] = []
    if review_flag:
        reasons.append(f"review flag: {review_flag}")
    current = [(conf, method) for conf, method, superseded in links if not superseded]
    if not links:
        reasons.append("no source link")
    elif not current:
        reasons.append("every source superseded by a later revision")
    elif not any(conf == "high" and (method or "") in TRUSTED_METHODS for conf, method in current):
        reasons.append("no high-confidence source read from native text")
    if status not in FILLABLE_STATUSES:
        reasons.append(f"status depends on the project: {status or 'unknown'}")
    if (not manufacturer or manufacturer == "Unconfirmed") and status != "Noted":
        reasons.append("manufacturer not confirmed")
    if requirement_chars < MIN_REQUIREMENT_CHARS:
        reasons.append("requirement text too short to identify a clause")
    blocking = sorted(t for t in issue_types if t not in NON_BLOCKING_ISSUES)
    if blocking:
        reasons.append("review issue: " + "; ".join(blocking))
    return reasons


# Why a record was blocked, split in two. EXTRACTION reasons say the source
# PDF should be looked at before the answer is trusted -- the answer was
# paired to its clause by position, read by OCR, or the document has a later
# revision. Autofill still writes such an answer in when the wording is the
# same and the answers agree, but leaves the row a candidate for review.
# Every other reason -- a project-dependent status, an unconfirmed
# manufacturer, a fragment, conflicting statuses, a review issue -- means the
# answer cannot stand for this clause at all, and nothing is written.
_HARD_REASONS = ("status depends on the project", "manufacturer not confirmed", "too short", "review issue:",
                 "no source link", "CONFLICT")


def extraction_only(reasons: str | None) -> bool:
    """Whether a blocked record was blocked only for how it was extracted."""
    return bool(reasons) and not any(marker in reasons for marker in _HARD_REASONS)


# --- manufacturers ------------------------------------------------------------------

# The names a manufacturer goes by in specifications, BOQs and past
# statements. A brand is kept as recorded on the record; these only decide
# whether a record's maker is the project's maker.
MANUFACTURER_ALIASES: dict[str, tuple[str, ...]] = {
    "EDWARDS": ("EDWARDS", "EST", "EST3", "EST4", "EDWARDS EST", "KIDDE EDWARDS"),
    "EATON": ("EATON", "MENVIER", "COOPER", "COOPER MENVIER", "EATON MENVIER"),
    "RP-TECHNIK": ("RP-TECHNIK", "RP TECHNIK", "RPTECHNIK", "ROPAG"),
    "XTRALIS": ("XTRALIS", "VESDA"),
    "HONEYWELL": ("HONEYWELL", "NOTIFIER", "MORLEY", "MORLEY-IAS", "ESSER", "SYSTEM SENSOR"),
    "TEKNOWARE": ("TEKNOWARE",),
    "SIMPLEX": ("SIMPLEX", "JOHNSON CONTROLS"),
    "APOLLO": ("APOLLO",),
    "HOCHIKI": ("HOCHIKI",),
    "BOSCH": ("BOSCH",),
    "SIEMENS": ("SIEMENS",),
    "NAFFCO": ("NAFFCO",),
    "TECHNOLUX": ("TECHNOLUX",),
    "PANASONIC": ("PANASONIC",),
    "URANUS": ("URANUS",),
    "EMERGI-LITE": ("EMERGI-LITE", "EMERGILITE", "ABB EMERGI-LITE"),
    "LEGRAND": ("LEGRAND",),
    "C-TEC": ("C-TEC", "CTEC"),
}
_ALIAS_LOOKUP = {alias: maker for maker, aliases in MANUFACTURER_ALIASES.items() for alias in aliases}


def canonical_manufacturer(name: str | None) -> str | None:
    """The manufacturer a name means, or None when it is not one we know."""
    if not name:
        return None
    text = name.strip().upper()
    if text in _ALIAS_LOOKUP:
        return _ALIAS_LOOKUP[text]
    for alias, maker in sorted(_ALIAS_LOOKUP.items(), key=lambda kv: -len(kv[0])):
        if alias in text:
            return maker
    return None


# --- systems ------------------------------------------------------------------------

# The statement's system codes to the knowledge base's system labels.
SYSTEM_TO_KNOWLEDGE = {
    "FAS": ("FA",),
    "VES": ("FA",),
    # Emergency lighting is one system: both of the knowledge base's labels.
    "ELS": ("CBS", "EML"),
    "PAVA": ("OTHER: PA / PAVA (non fire)",),
}


# --- scope --------------------------------------------------------------------------

# Commitments a response can carry beyond the product itself, and the words
# in a project's scope of work that cover each.
COMMITMENTS: dict[str, tuple[str, ...]] = {
    "installation": ("install", "installation", "erection"),
    "testing": ("test", "testing"),
    "commissioning": ("commission", "commissioning"),
    "training": ("training", "train"),
    "warranty": ("warranty", "guarantee"),
    "maintenance": ("maintenance", "maintain", "amc", "service contract"),
    "supply": ("supply", "supplies", "supplying"),
}
