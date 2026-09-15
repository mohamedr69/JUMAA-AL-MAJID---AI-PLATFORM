"""Issue codes, coverage records and the attention router.

An *issue* is one thing a deterministic read could not settle: a quantity
cell the OCR could not parse, a design sheet with no system code, a page
with no recognisable table. The router (`route`) is a table of Python rules
that says, per code, whether the platform can repair it locally, whether a
model may be asked for a proposal, and whether a human has to decide. The
model's own confidence never appears in that table: eligibility is a
property of the issue, and acceptance is a property of the evidence.

Outcomes are kept distinct so that a partial read can never pass as a
complete one: a page that was detected but not processed makes the run
`VALID_PARTIAL` however well-formed the rows from the other pages are.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class IssueCode(str, enum.Enum):
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    UNPROCESSED_PAGE_OR_REGION = "UNPROCESSED_PAGE_OR_REGION"
    UNRECOGNIZED_TABLE_LAYOUT = "UNRECOGNIZED_TABLE_LAYOUT"
    AMBIGUOUS_OCR = "AMBIGUOUS_OCR"
    AMBIGUOUS_SYSTEM = "AMBIGUOUS_SYSTEM"
    CONFLICTING_SOURCE_VALUES = "CONFLICTING_SOURCE_VALUES"
    AMBIGUOUS_REVISION = "AMBIGUOUS_REVISION"
    QUANTITY_OR_UNIT_PARSE_FAILURE = "QUANTITY_OR_UNIT_PARSE_FAILURE"
    SOURCE_ROOT_MISMATCH = "SOURCE_ROOT_MISMATCH"
    UNSUPPORTED_DOCUMENT = "UNSUPPORTED_DOCUMENT"
    MISSING_ENGINEERING_EVIDENCE = "MISSING_ENGINEERING_EVIDENCE"


class Outcome(str, enum.Enum):
    VALID = "VALID"
    VALID_PARTIAL = "VALID_PARTIAL"
    NEEDS_INTERPRETATION = "NEEDS_INTERPRETATION"
    NEEDS_HUMAN_DECISION = "NEEDS_HUMAN_DECISION"
    MISSING_SOURCE = "MISSING_SOURCE"
    UNSUPPORTED_INPUT = "UNSUPPORTED_INPUT"
    PROCESSING_FAILURE = "PROCESSING_FAILURE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


@dataclass(frozen=True)
class Routing:
    """What the platform does with an issue of a given code."""

    severity: str                 # "info" | "medium" | "high"
    local_repair: bool            # a deterministic fallback exists (and has already run)
    llm_eligible: bool            # a model may be asked for a proposal
    human_required: bool          # an engineer must decide, whatever the model says
    task: str | None              # the ai task name, when eligible
    why: str


# The router. Every entry is a decision, not a heuristic: the brief's rule
# that software faults, missing evidence and revision choice never reach a
# model is encoded here and tested.
ROUTING: dict[IssueCode, Routing] = {
    IssueCode.MISSING_REQUIRED_FIELD: Routing(
        "high", True, False, True, None,
        "A blank field is typed in by the engineer; a model cannot read what is not on the form.",
    ),
    IssueCode.UNPROCESSED_PAGE_OR_REGION: Routing(
        "high", False, True, True, "table_layout",
        "A page with ink but no recognised table: eligible for layout interpretation, "
        "reviewed before any row is accepted. Not enabled until a labelled set exists.",
    ),
    IssueCode.UNRECOGNIZED_TABLE_LAYOUT: Routing(
        "high", False, True, True, "table_layout",
        "The whole sheet is an unknown layout. As above.",
    ),
    IssueCode.AMBIGUOUS_OCR: Routing(
        "medium", True, True, True, "read_cell",
        "A cell read at low confidence: the model sees that cell's image and proposes a reading, "
        "checked against an independent OCR re-read before it is shown as validated.",
    ),
    IssueCode.QUANTITY_OR_UNIT_PARSE_FAILURE: Routing(
        "medium", True, True, True, "read_cell",
        "A quantity cell the parser could not settle. As AMBIGUOUS_OCR.",
    ),
    IssueCode.AMBIGUOUS_SYSTEM: Routing(
        "medium", True, True, True, "classify_system",
        "A sheet with no code on a DRF marking several systems: the model may propose one of "
        "the DRF's marked codes; the reviewer picks.",
    ),
    IssueCode.CONFLICTING_SOURCE_VALUES: Routing(
        "medium", True, False, True, None,
        "Two sources disagree: both are shown; nothing is chosen for the engineer.",
    ),
    IssueCode.AMBIGUOUS_REVISION: Routing(
        "high", True, False, True, None,
        "Which revision governs is the engineer's call; the default is the highest declared one.",
    ),
    IssueCode.SOURCE_ROOT_MISMATCH: Routing(
        "high", False, False, True, None,
        "An application validation failure, refused at creation.",
    ),
    IssueCode.UNSUPPORTED_DOCUMENT: Routing(
        "high", False, False, True, None,
        "Not a PDF or workbook the platform reads; a model cannot supply what the file lacks.",
    ),
    IssueCode.MISSING_ENGINEERING_EVIDENCE: Routing(
        "high", True, False, True, None,
        "A current or datasheet that is not on file: retrieve it or enter it; never guess a rating.",
    ),
}

# Tasks the model may actually be called for today. `table_layout` is
# routed as eligible but stays off until an evaluation report for it passes
# its gate (app/ai/evaluation.py); until then the issue is recorded for review.
ENABLED_TASKS: frozenset[str] = frozenset({"read_cell", "classify_system"})


def route(code: IssueCode) -> Routing:
    return ROUTING[code]


def llm_task_for(code: IssueCode) -> str | None:
    """The ai task an issue of this code may be sent to, or None."""
    routing = ROUTING[code]
    if not routing.llm_eligible or routing.task is None:
        return None
    from app.ai import evaluation

    if evaluation.switched_off(routing.task):
        return None
    if routing.task not in ENABLED_TASKS and not evaluation.gate_open(routing.task):
        return None
    return routing.task


@dataclass
class Issue:
    """One unsettled thing, with where it is and what the reader saw."""

    code: IssueCode
    page: int | None = None
    # Pixel bbox at the extractor's render DPI: (x0, y0, x1, y1). What an
    # evidence builder crops.
    region: tuple[int, int, int, int] | None = None
    # A stable name for what the proposal would set: "boq_line:<page>:<n>",
    # "drf_field:<name>", "sheet_system:<path>".
    target: str = ""
    # What the reader saw, for the evidence builder and the reviewer.
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def routing(self) -> Routing:
        return ROUTING[self.code]


@dataclass
class RegionCoverage:
    kind: str                 # "table" | "banner" | "page"
    top: int
    bottom: int
    status: str               # "processed" | "skipped" | "failed"
    rows_accepted: int = 0
    rows_dropped: int = 0
    reason: str | None = None


@dataclass
class PageCoverage:
    page: int
    detected: bool = True
    processed: bool = False
    regions: list[RegionCoverage] = field(default_factory=list)
    reason: str | None = None


@dataclass
class Coverage:
    pages: list[PageCoverage] = field(default_factory=list)

    @property
    def unprocessed_pages(self) -> list[int]:
        return [p.page for p in self.pages if p.detected and not p.processed]

    def to_dict(self) -> dict:
        return {
            "pages": [
                {
                    "page": p.page,
                    "detected": p.detected,
                    "processed": p.processed,
                    "reason": p.reason,
                    "regions": [vars(r) for r in p.regions],
                }
                for p in self.pages
            ]
        }


def outcome_for(coverage: Coverage, issues: list[Issue], *, lines: int, failure: str | None = None) -> Outcome:
    """The run's outcome from what was covered and what is open.

    Order matters: a processing failure or unsupported input is that
    regardless of rows; a page not processed makes a partial read; open
    issues that a model may help with make NEEDS_INTERPRETATION; open
    issues only a human can settle make NEEDS_HUMAN_DECISION.
    """
    if any(i.code == IssueCode.UNSUPPORTED_DOCUMENT for i in issues):
        return Outcome.UNSUPPORTED_INPUT
    if any(i.code == IssueCode.UNRECOGNIZED_TABLE_LAYOUT for i in issues):
        # The document opened and rendered; it is its layout that is not
        # understood. That is a question of interpretation or of a person,
        # not a software failure.
        return Outcome.NEEDS_INTERPRETATION if any(llm_task_for(i.code) for i in issues) else Outcome.NEEDS_HUMAN_DECISION
    if failure is not None:
        return Outcome.PROCESSING_FAILURE
    if coverage.unprocessed_pages:
        return Outcome.VALID_PARTIAL
    if any(llm_task_for(i.code) for i in issues):
        return Outcome.NEEDS_INTERPRETATION
    if issues:
        return Outcome.NEEDS_HUMAN_DECISION
    return Outcome.VALID
