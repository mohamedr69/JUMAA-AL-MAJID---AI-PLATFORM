"""The consultant's answer to a submittal, filed in the project folder.

A reply comes back as a scan of the consultant's comments -- a page of
remarks, signed and dated -- and it carries no submittal reference of its
own. The form reader never sees it, so without this the project reports
a submittal "under review" over an answer sitting in its folder.

**Where it sits is what ties it to what it answers.** The archive files a
revision's two halves side by side:

    02- Material Submittals/FA/R0/Submitted/   what we sent
    02- Material Submittals/FA/R0/Received/    what came back on it

so a reply under `.../FA/R0/...` answers the R0 of FA. The code is read
from the words the consultant used, and failing that from the letter the
file is filed under ("...-0016_00_C.pdf" is their C).

One implementation, used by the map (`app.ai.submittal_reader`), by the
register and by the logs, so those three cannot disagree about whether a
submittal has been answered.
"""

from __future__ import annotations

from pathlib import Path

from app.services.submittal_scanner import APPROVAL_FOLDER_RE

# The consultant's words -> the reading's reply status, in the spelling
# `submittal_reader.CODES` expects. Longest phrase first: "approved as
# noted" is not an approval.
PHRASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("re-submit", "resubmit", "revise and resubmit"), "resubmit"),
    (("approved as noted", "as noted", "with comments"), "approved_as_noted"),
    (("not approved", "rejected"), "rejected"),
    (("approved",), "approved"),
)
# The letter a reply is filed under, as the consultant's forms code them.
LETTERS = {"A": "approved", "B": "approved_as_noted", "C": "resubmit", "D": "rejected"}


def on_file(rows) -> list[tuple[str, str, str]]:
    """Every reply in the project folder, as (folder, file name, words).

    `rows` are `ProjectDocument`s; a reply is any document filed under a
    received or approved folder.
    """
    found: list[tuple[str, str, str]] = []
    for row in rows:
        relative = (row.relative_path or "").replace("\\", "/")
        if not relative:
            continue
        where = Path(relative).parent
        if not APPROVAL_FOLDER_RE.search(where.as_posix()):
            continue
        reading = (getattr(row, "extracted", None) or {}).get("form") or {}
        # The consultant's own words where a reading has them; a reply
        # filed as a scan has none, and the file name carries the code.
        words = (reading.get("reply") or {}).get("evidence") or ""
        found.append((where.as_posix(), row.filename or Path(relative).name, words))
    return found


def for_revision(folder: str, replies: list[tuple[str, str, str]]) -> tuple[str | None, str | None]:
    """The answer filed under `folder`, as (status, words). `folder` is
    the revision's own folder -- `.../FA/R0` -- so both halves sit under
    it. (None, None) when nothing was filed there."""
    for where, filename, words in replies:
        if not where.startswith(folder):
            continue
        said = (words or "").lower()
        for phrases, status in PHRASES:
            if any(phrase in said for phrase in phrases):
                return status, words or None
        letter = Path(filename).stem.rsplit("_", 1)[-1].strip().upper()
        return LETTERS.get(letter), words or None
    return None, None


def revision_folder(document_path: str | None) -> str | None:
    """The revision folder a filed submittal belongs to: the parent of the
    Submitted folder it sits in."""
    if not document_path:
        return None
    return Path(document_path.replace("\\", "/")).parent.parent.as_posix()


def apply_to(reading: dict, replies: list[tuple[str, str, str]]) -> bool:
    """Give a form reading the answer filed beside it, where it has none
    of its own. Returns whether one was found.

    The reading's own reply wins: a reply printed on the form itself was
    read from the page, and a scan filed beside it is the same answer at
    best. Marked `from_consultant`, because a reply filed under the
    received folder is the consultant's by definition -- that is what the
    folder is.
    """
    reply = reading.get("reply") or {}
    if reply.get("present") and reply.get("from_consultant") and reply.get("status") not in (None, "", "none"):
        return False
    folder = revision_folder(reading.get("relative"))
    if not folder:
        return False
    status, words = for_revision(folder, replies)
    if not status:
        return False
    reading["reply"] = {
        **reply,
        "present": True,
        "from_consultant": True,
        "status": status,
        "evidence": words or reply.get("evidence") or "",
        "source": "filed in the received folder",
    }
    return True
