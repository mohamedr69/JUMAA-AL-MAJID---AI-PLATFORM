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

import re
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


def words_of(rows, folder: str, *, max_pages: int = 4) -> str | None:
    """What the consultant actually wrote, read off the reply itself.

    The reply is filed as a scan and indexed as a document like any
    other, so nothing stores its words. For the reply sheet they are the
    point -- the comments are what is being answered -- so the file is
    read here, on demand and only the one document.
    """
    import pymupdf

    from app.services import document_control

    for row in rows:
        relative = (row.relative_path or "").replace("\\", "/")
        if not relative or not Path(relative).parent.as_posix().startswith(folder):
            continue
        if not APPROVAL_FOLDER_RE.search(Path(relative).parent.as_posix()):
            continue
        try:
            with pymupdf.open(document_control._os_path(Path(row.path))) as document:
                pages = list(document)[:max_pages]
                text = chr(10).join(page.get_text() for page in pages)
        except Exception:
            continue
        cleaned = " ".join(text.split())
        if cleaned:
            return cleaned
    return None


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


# --- comments carried into the revision that answers them -------------------------
#
# A resubmission reproduces the comments it is answering: the R1 form
# prints the consultant's remarks on R0 so the reader can see what
# changed, and the R1 package carries the reply sheet as well. Read
# plainly, that makes R1 look answered the moment it is filed -- "revise
# and resubmit" over a revision the consultant has not seen yet.
#
# Two things say otherwise, and either is enough:
#
#   * the comments name the revision they are about ("...Revision 0,
#     dated 02.09.2026"), and it is not this one;
#   * the project files each revision as Submitted and Received, and
#     nothing has come back into this revision's Received folder.

SUBMITTED_FOLDER_RE = re.compile(r"submitted|submittal(?:s)?\b(?!.*received)", re.IGNORECASE)
# "Revision 0", "Rev. 00", "Rev 1" -- the revision the comments are on.
# "revise and resubmit" is not a revision number, and does not match:
# what follows "rev" has to be digits.
COMMENTED_REVISION_RE = re.compile(r"\brev(?:ision)?\.?\s*[:\-]?\s*(\d{1,2})\b", re.IGNORECASE)


def commented_revision(evidence: str | None) -> int | None:
    """The revision the consultant's comments say they are about, where
    they say. The first mention is the one: the comments open by naming
    the sheet they are on, and anything after is the remarks themselves.
    """
    found = COMMENTED_REVISION_RE.search(evidence or "")
    return int(found.group(1)) if found else None


def _revision_of(reading: dict) -> int | None:
    revision = reading.get("revision")
    if revision is None or revision == "":
        return None
    try:
        return int(str(revision).strip().lstrip("Rr") or 0)
    except ValueError:
        return None


def answers_another_revision(reading: dict) -> bool:
    """Whether the reply printed on this form names a revision that is not
    the form's own -- the comments it is answering, carried in with it.

    The revision-naming half of `carried_over`, on its own, for the places
    that have the reading but no list of what is filed in the folder.
    """
    reply = reading.get("reply") or {}
    if not reply.get("present") or reply.get("status") in (None, "", "none"):
        return False
    revision = _revision_of(reading)
    said = commented_revision(reply.get("evidence"))
    return revision is not None and said is not None and said != revision


def carried_over(reading: dict, replies: list[tuple[str, str, str]]) -> bool:
    """Whether this reading's consultant reply belongs to an earlier
    revision -- printed on the form because the form answers it.

    A reply the platform itself filed beside the form (`apply_to` marks
    those) is not in question here: this is about what the form reader
    took off the page.
    """
    reply = reading.get("reply") or {}
    if not reply.get("present") or reply.get("status") in (None, "", "none"):
        return False
    if reply.get("source"):          # filed in the received folder, not read off the form
        return False
    revision = _revision_of(reading)
    if revision is None:
        return False

    # What the comments say they are on. Where they say it at all, that
    # settles it either way: comments naming this revision are its own
    # answer, whatever the folders look like.
    said = commented_revision(reply.get("evidence"))
    if said is not None:
        return said != revision

    # What the folder says, for comments that name no revision. Only
    # where the project files a revision in
    # two halves: an older project keeps the form loose in the revision
    # folder, and there is no Received folder to be empty.
    relative = (reading.get("relative") or "").replace("\\", "/")
    if not relative or not SUBMITTED_FOLDER_RE.search(Path(relative).parent.name):
        return False
    folder = revision_folder(relative)
    return bool(folder) and for_revision(folder, replies)[0] is None


def vetted(reading: dict, replies: list[tuple[str, str, str]]) -> dict:
    """The reading with a carried-over reply taken off it, so the
    revision reads as what it is: sent, and not yet answered."""
    if not carried_over(reading, replies):
        return reading
    reply = reading.get("reply") or {}
    # The code, the consultant, the date and the words all describe the
    # earlier revision's answer. Left on, they print as "consultant reply
    # C" beside a revision reading "under review", which is the
    # contradiction this is here to stop.
    return {**reading, "reply": {"present": False, "from_consultant": False, "status": None,
                                 "code": "", "consultant": "", "date": "", "evidence": "",
                                 # Kept, so the reason is visible rather than guessed at.
                                 "carried_over_from": commented_revision(reply.get("evidence")),
                                 "source": "the comments on an earlier revision, printed on this form"}}
