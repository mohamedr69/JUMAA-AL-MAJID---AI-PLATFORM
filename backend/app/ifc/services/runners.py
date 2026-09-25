"""The IFC jobs, as the worker runs them (app.workers.ifc_worker).

The API streams the upload to the staging folder, checks what can be
checked without reading the drawing, and queues a job whose `params` say
everything the worker needs: the staged file, its name and hash, and the
drawing and revision it is to be. The worker claims it, and runs one of
these with a session of its own -- never the request's.

A staged upload is the job's: removed when the job ends, whichever way,
except when the worker itself is stopping (`Interrupted`) -- then the job
goes back in the queue and reads the same file when the worker starts
again. A job cancelled while it waits has its file removed by the
cancellation (`jobs.CLEANUP`); anything left over by a worker that was
killed is removed by the worker's sweep (app.ifc.services.upload.sweep).
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.ifc.dxf import convert
from app.ifc.progress import ReadTimer
from app.ifc.services import processing, revisions, upload, zip_import
from app.models import BackgroundJob, Project, ProjectIfcDrawing, User
from app.services import activity, jobs

log = logging.getLogger(__name__)

READ, READ_ZIP, REPROCESS = "ifc_read", "ifc_read_zip", "ifc_reprocess"


def dedup_key(kind: str, project_id: int | None, sha256: str, logical: str = "", revision: str = "") -> str:
    """ifc_read:<project>:<sha256>:<drawing>:<revision> -- the same file
    sent again for the same drawing and revision is the same job."""
    return f"{kind}:{project_id}:{sha256}:{logical[:60]}:{revision}"[:200]


def _unexpected(job_id: int, exc: BaseException) -> processing.ReadError:
    log.exception("IFC job %s failed unexpectedly", job_id)
    return processing.ReadError(f"The drawing could not be read because of an unexpected error on the server "
                                f"({type(exc).__name__}, job {job_id}). The details are in the IFC worker's log.")


def _plan_again(session: Session, project_id: int, p: dict) -> revisions.Plan:
    """The plan the API made, checked again now: another job may have
    revised the drawing, or imported the same file, while this one waited."""
    try:
        return revisions.plan(session, project_id, filename=p["name"], sha256=p["sha256"], revision=p["revision"],
                              supersedes_id=p.get("supersedes_id"), confirm_new=True)
    except revisions.IdentityError as exc:
        message = exc.detail if isinstance(exc.detail, str) else exc.detail.get("message", str(exc))
        if p.get("supersedes_id") and "already revised" in message:
            previous = session.get(ProjectIfcDrawing, p["supersedes_id"])
            if previous is not None:
                message = revisions.conflict_message(session, ProjectIfcDrawing(
                    supersedes_id=previous.id, drawing_reference=previous.drawing_reference, filename=p["name"],
                    revision=p["revision"] or ""))
        raise processing.ReadError(message) from exc


def run_read(session: Session, job: BackgroundJob, ctx: jobs.JobContext) -> dict:
    """ifc_read: one DWG or DXF."""
    p = job.params or {}
    staged = Path(p["staged_path"])
    keep_staged = False
    try:
        if not staged.is_file():
            raise processing.ReadError("The uploaded file is no longer on the server. Upload the drawing again.")
        project = session.get(Project, job.project_id)
        user = session.get(User, p["user_id"])
        name, ext = p["name"], p["ext"]
        converter = convert.find_converter() if ext == "dwg" else None
        if ext == "dwg" and converter is None:
            raise processing.ReadError("The DWG converter is not available on this worker. Install AutoCAD or the "
                                       "free ODA File Converter on the PC the worker runs on, or upload a DXF.")
        ctx.progress(0, 100, "Validating the revision", stage="validating", file=name)
        # Read by this very job before its worker stopped (the drawing was
        # saved, the job not yet marked done): that read is the answer.
        done = revisions.duplicate_of(session, project.id, p["sha256"])
        if done is not None and ((done.meta or {}).get("processing") or {}).get("job_id") == job.id:
            ctx.progress(100, 100, "Read", stage="done", eta_seconds=0, file=name)
            return {"drawing_id": done.id, "filename": name, "revision": done.revision,
                    "reference": done.drawing_reference, "engineer_review": 0, "ai_note": None}
        plan = _plan_again(session, project.id, p)
        timer = ReadTimer.for_upload(
            lambda percent, message, stage, eta: ctx.progress(percent, 100, message, stage=stage, eta_seconds=eta,
                                                               file=name),
            is_dwg=ext == "dwg", size_mb=p["size"] / 1e6, converter=converter.name if converter else None)
        timer.begin("save")
        timer.start()
        try:
            drawing = processing.read_drawing(session, project, user, source=staged, name=name, ext=ext,
                                              sha256=p["sha256"], plan=plan, timer=timer, check=ctx.check,
                                              job_id=job.id)
        finally:
            timer.stop()
        processing_summary = (drawing.meta or {}).get("processing") or {}
        ctx.progress(100, 100, "Read", stage="done", eta_seconds=0, file=name)
        return {"drawing_id": drawing.id, "filename": name, "revision": plan.revision, "reference": plan.reference,
                "engineer_review": processing_summary.get("engineer_review", 0),
                "ai_note": processing_summary.get("ai_note")}
    except jobs.Interrupted:
        keep_staged = True
        raise
    except (jobs.Cancelled, processing.ReadError):
        raise
    except revisions.RevisionConflict as exc:
        raise processing.ReadError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 -- said plainly; the trace is in the log
        raise _unexpected(job.id, exc) from exc
    finally:
        if not keep_staged:
            upload.discard(staged)


def run_read_zip(session: Session, job: BackgroundJob, ctx: jobs.JobContext) -> dict:
    """ifc_read_zip: every drawing in the archive, one after another."""
    p = job.params or {}
    staged = Path(p["staged_path"])
    keep_staged = False
    read: list[dict] = []
    failed: list[dict] = []
    unchanged: list[dict] = []
    confirm: list[dict] = []
    skipped: list[str] = list(p.get("skipped") or [])
    try:
        if not staged.is_file():
            raise processing.ReadError("The uploaded archive is no longer on the server. Upload it again.")
        project = session.get(Project, job.project_id)
        user = session.get(User, p["user_id"])
        ctx.progress(0, 1, "Checking the archive", stage="validating")
        found, _ = zip_import.members(staged)
        total = len(found)
        needs_dwg = any(upload.extension(n) == "dwg" for n, _ in found)
        converter_missing = needs_dwg and convert.find_converter() is None
        with zipfile.ZipFile(staged) as archive:
            for index, (name, member) in enumerate(found):
                ctx.check()
                ctx.progress(index, total, f"Reading {name} ({index + 1} of {total})", stage="read", file=name)
                ext = upload.extension(name)
                if ext == "dwg" and converter_missing:
                    failed.append({"filename": name, "reason": "The DWG converter is not available on this worker."})
                    continue
                try:
                    unpacked = zip_import.unpack(archive, member)
                except zip_import.MemberTooLarge:
                    failed.append({"filename": name, "reason": "It unpacks to more than the archive declared, or "
                                                               f"to more than {upload.limit_mb()} MB."})
                    continue
                except (zipfile.BadZipFile, OSError, EOFError) as exc:
                    failed.append({"filename": name, "reason": f"It could not be unpacked: {exc}"})
                    continue
                try:
                    action, what = zip_import.plan_member(session, project.id, name, unpacked.sha256)
                    if action == "unchanged":
                        entry = {"filename": name, "drawing_id": what.id, "revision": what.revision}
                        # Read by this very job before a worker restart put it back in the queue.
                        (read if (what.meta or {}).get("processing", {}).get("job_id") == job.id else unchanged).append(entry)
                        continue
                    if action == "confirm":
                        confirm.append({"filename": name, "drawing_id": what.id, "existing": what.filename,
                                        "revision": what.revision, "reference": what.drawing_reference,
                                        "reason": f"Matches {what.drawing_reference or what.filename} {what.revision}, "
                                                  f"already imported, and its name states no later revision. "
                                                  f"Import it on its own and say whether it revises "
                                                  f"{what.revision}."})
                        continue
                    drawing = processing.read_drawing(session, project, user, source=unpacked.path, name=name,
                                                      ext=ext, sha256=unpacked.sha256, plan=what, check=ctx.check,
                                                      job_id=job.id)
                    read.append({"drawing_id": drawing.id, "filename": name, "revision": what.revision})
                except processing.ReadError as exc:
                    failed.append({"filename": name, "reason": str(exc)})     # the other floors are still read
                except revisions.RevisionConflict as exc:
                    failed.append({"filename": name, "reason": str(exc)})
                except (jobs.Cancelled, jobs.Interrupted):
                    raise
                except Exception as exc:  # noqa: BLE001 -- one floor's surprise is not the building's
                    failed.append({"filename": name, "reason": str(_unexpected(job.id, exc))})
                finally:
                    upload.discard(unpacked.path)
        ctx.progress(total, total, "Read", stage="done", eta_seconds=0)
        if read or unchanged:
            activity.record(session, user, "ifc.zip_imported",
                            f"Imported {p['name']}: {len(read)} read, {len(unchanged)} unchanged, "
                            f"{len(confirm)} to confirm, {len(failed)} failed",
                            project=project, entity_type="project", entity_id=project.id,
                            detail={"read": len(read), "unchanged": len(unchanged), "confirm": len(confirm),
                                    "failed": len(failed), "job_id": job.id})
        return {"archive": p["name"], "read": read, "failed": failed, "skipped": skipped, "unchanged": unchanged,
                "needs_confirmation": confirm, "drawings": len(read)}
    except jobs.Interrupted:
        keep_staged = True
        raise
    except jobs.Cancelled:
        raise
    except processing.ReadError:
        raise
    except Exception as exc:  # noqa: BLE001
        from fastapi import HTTPException

        if isinstance(exc, HTTPException):
            raise processing.ReadError(str(exc.detail)) from exc
        raise _unexpected(job.id, exc) from exc
    finally:
        if not keep_staged:
            upload.discard(staged)


def run_reprocess(session: Session, job: BackgroundJob, ctx: jobs.JobContext) -> dict:
    """ifc_reprocess: every stored drawing read again with the current
    extraction rules. Known signatures are known: only symbols that are new
    and still unanswered go to the AI, through its cache first."""
    from app.ifc.library_file import save_library
    from app.ifc.reprocess import reprocess_all

    rep = reprocess_all(session, use_ai=True, check=ctx.check,
                        progress=lambda done, total, name: ctx.progress(done, total, f"Reading {name}", stage="read",
                                                                        file=name))
    save_library(session)
    return {"drawings": rep.drawings, "carried_over": rep.carried_over, "weatherproof": rep.weatherproof,
            "left_for_review": rep.left_for_review, "errors": {str(k): v for k, v in rep.errors.items()},
            "ai_verified": rep.ai_verified, "deterministic": rep.deterministic}


RUNNERS = {READ: run_read, READ_ZIP: run_read_zip, REPROCESS: run_reprocess}


def _discard_staged(job: BackgroundJob) -> None:
    upload.discard((job.params or {}).get("staged_path"))


for _kind in (READ, READ_ZIP):
    jobs.CLEANUP[_kind] = _discard_staged
