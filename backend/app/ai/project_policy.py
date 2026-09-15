"""Whether a project's documents may be sent to an AI provider.

The platform-wide switch (AI_ENABLED) says whether a provider is configured;
this says whether a given client's documents may go to it. A project set to
"blocked" makes every AI feature on it unavailable -- reading a cell, checking
details against the DRF, reviewing or auto-filling a compliance clause --
with the reason shown, and the deterministic features carry on unchanged.
"""

from __future__ import annotations

POLICIES = ("allowed", "blocked")
BLOCKED_MESSAGE = ("AI is switched off for this project (Project Info, AI use): its documents are not sent to an AI "
                   "provider. Everything else works as usual.")


class AiBlocked(Exception):
    pass


def allowed(project) -> bool:
    return project is None or getattr(project, "ai_policy", "allowed") != "blocked"


def require(project) -> None:
    if not allowed(project):
        raise AiBlocked(BLOCKED_MESSAGE)
