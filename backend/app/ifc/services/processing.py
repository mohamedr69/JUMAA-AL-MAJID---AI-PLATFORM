"""One drawing read: convert, extract, identify its symbols, save, file.

The order is what keeps the database and the disk in step:

    convert the DWG (into the project's working folder)
    extract the symbols, sheets and floors
    identify the symbols (library, rules, cache, AI)       -- the library is the company's: kept either way
    reserve the revision in the database (flush)           -- the unique index decides here
    file the drawing in the project's folder
    move the upload into the working folder
    commit

Nothing is filed before the revision is safely the drawing's, and when
anything fails -- a conversion, a broken DXF, a stop, another worker
revising the same drawing, the unexpected -- what this read created goes:
the database row (rolled back), the filed copy (if this read created it),
the converted DXF and the working copy. The upload itself is the job's to
remove (app.ifc.services.runners), so a job put back in the queue can
read it again.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.ifc import storage
from app.ifc.dxf import convert
from app.ifc.dxf.extract import extract
from app.ifc.progress import ReadTimer
from app.ifc.resolve import MODEL
from app.ifc.services import classification, revisions
from app.models import ProjectIfcDrawing, User
from app.services import activity, jobs, project_folders

log = logging.getLogger(__name__)


class ReadError(Exception):
    """The drawing could not be read: its message is for the engineer."""


def read_drawing(db: Session, project, user: User, *, source: Path, name: str, ext: str, sha256: str | None,
                 plan: revisions.Plan, timer: ReadTimer | None = None, check=None, use_ai: bool = True,
                 job_id: int | None = None, consume_source: bool = True) -> ProjectIfcDrawing:
    """Read the DWG or DXF at `source` into the project as `plan` says (its
    revision, and the drawing it revises). `consume_source`: the file is
    moved into the working folder once saved; otherwise copied. `timer`
    hears each stage; `check` is called between stages and raises to stop
    the read, which then leaves nothing behind."""
    started = time.monotonic()

    def stage(name_: str) -> None:
        if check is not None:
            check()
        if timer is not None:
            timer.begin(name_)

    stage("save")
    size = source.stat().st_size
    folder = storage.project_folder(project)
    folder.mkdir(parents=True, exist_ok=True)
    # The working copy goes under a short name of its own: an IFC sheet's
    # name under the uploads folder can pass Windows' 260-character limit,
    # which neither AutoCAD nor ezdxf will open. The name as uploaded is the
    # drawing's `filename`, which is what the floors are read from.
    stem = uuid.uuid4().hex[:12]
    working = folder / f"{stem}.{ext}"
    dxf_path = folder / f"{stem}.dxf"
    created: list[Path] = []
    filed: str | None = None
    filed_by_us = False
    inserted = False
    timings: dict[str, float] = {}

    try:
        conversion: dict | None = None
        if ext == "dwg":
            stage("convert")
            t0 = time.monotonic()
            created.append(dxf_path)
            try:
                res = convert.convert_dwg_to_dxf(source, dxf_path)
            except convert.ConversionError as exc:
                raise ReadError(f"Could not convert the DWG to DXF: {exc}") from exc
            timings["conversion_s"] = round(time.monotonic() - t0, 2)
            conversion = {"source_format": "dwg", "converter": res.converter, "seconds": round(res.seconds, 1),
                          "dwg_path": storage.relative(working)}
            if timer is not None:
                timer.dxf_size(dxf_path.stat().st_size / 1e6)
            if check is not None:
                check()

        stage("read")
        t0 = time.monotonic()

        def progress(part: str, fraction: float) -> None:
            if timer is None:
                if check is not None and part in ("walk", "finish"):
                    check()
                return
            if part != timer.stage:
                stage(part)
            timer.at(fraction)

        read_from = dxf_path if ext == "dwg" else source
        try:
            result = extract(str(read_from), progress=progress, plan=timer.plan if timer is not None else None)
        except (ReadError, jobs.Cancelled, jobs.Interrupted):
            raise
        except Exception as exc:  # ezdxf raises many types for a broken file
            if check is not None:
                check()      # a stop asked for is a stop, not a broken drawing
            raise ReadError(f"Could not read this drawing: {exc}") from exc
        timings["extraction_s"] = round(time.monotonic() - t0, 2)
        d = result.to_dict()
        meta = {"containers": d["containers"], "skipped_empty_blocks": d["skipped_empty_blocks"],
                "layouts": d["layouts"], "sheets": d["sheets"], "architecture": d["architecture"],
                "loose_symbols": d["loose_symbols"], "conversion": conversion}
        supersedes = plan.supersedes
        if supersedes is not None:
            before = supersedes.meta or {}
            sheets = {s["name"] for s in d["sheets"] or []} | {MODEL}  # a drawing with no sheets is one plan, "Model"
            signatures = {g["signature"] for g in d["groups"]}
            overrides = {k: v for k, v in (before.get("floor_overrides") or {}).items() if k in sheets}
            skipped = sorted(sig for sig in before.get("review_skipped") or [] if sig in signatures)
            if overrides:
                meta["floor_overrides"] = overrides
            if skipped:
                meta["review_skipped"] = skipped
            meta["carried_over"] = {"from": supersedes.revision, "floor_overrides": len(overrides),
                                    "review_skipped": len(skipped)}

        # Identify the symbols: the library first, then the rules, the
        # cache and the AI. What they answer goes to the library for good.
        stage("classify")

        def sub(step: str, fraction: float) -> None:
            if timer is not None:
                timer.sub(step, fraction)

        queue, summary = classification.classify(db, groups=d["groups"], meta=meta, project_id=project.id,
                                                 drawing_name=name, job_id=job_id, use_ai=use_ai, check=check,
                                                 progress=sub)
        meta["symbol_review"] = queue
        meta["classified"] = summary.classified

        # Saved: the revision first, in the database, where two workers
        # revising one drawing are told apart; then filed.
        stage("file")
        supersedes_id = supersedes.id if supersedes is not None else None
        drawing = ProjectIfcDrawing(
            project_id=project.id, filename=name, stored_path=storage.relative(dxf_path), archive_path=None,
            units=d["units"], dxf_version=d["dxf_version"], seconds=d["seconds"], meta=meta, groups=d["groups"],
            created_by_id=user.id, revision=plan.revision, supersedes_id=supersedes_id,
            drawing_reference=plan.reference, source_sha256=sha256,
        )
        revisions.insert(db, drawing)
        inserted = True

        note = None
        try:
            filed, filed_by_us = project_folders.file_ifc_drawing_from(
                project, name, source, stamp=datetime.now().strftime("%Y-%m-%d %H%M"))
            if filed is None:
                note = "The drawing was read but not filed: this project's folder is not reachable on this PC."
        except OSError as exc:
            note = f"The drawing was read but could not be filed in {project_folders.IFC_FIRE_ALARM} ({exc.strerror or exc})."

        # The upload becomes the working copy (the DWG beside its DXF, or the DXF itself).
        target = working if ext == "dwg" else dxf_path
        if consume_source:
            os.replace(source, target)
        else:
            shutil.copyfile(source, target)
        created.append(target)

        timings["total_s"] = round(time.monotonic() - started, 2)
        meta = dict(meta)
        meta["filed_note"] = note
        meta["processing"] = {
            **classification.summary_dict(summary), **timings, "job_id": job_id, "file_size": size,
            "sha256": sha256, "processed_at": utc_now().isoformat(timespec="seconds"),
        }
        drawing.meta = meta
        drawing.archive_path = filed
        db.commit()
    except BaseException:
        if inserted:
            db.rollback()
        if filed_by_us and filed:
            project_folders.unfile_ifc_drawing(project, filed)
        for path in created:
            path.unlink(missing_ok=True)
        raise

    if timer is not None:
        timer.learned(dwg_mb=size / 1e6 if ext == "dwg" else None, dxf_mb=dxf_path.stat().st_size / 1e6)
    log.info("ifc.read.done project=%s drawing=%s reference=%r revision=%s job=%s file=%r size=%d sha256=%s "
             "conversion_s=%s extraction_s=%s total_s=%s unique=%d occurrences=%d engineer_review=%d",
             project.id, drawing.id, plan.reference, plan.revision, job_id, name, size, (sha256 or "")[:12],
             timings.get("conversion_s"), timings.get("extraction_s"), timings.get("total_s"),
             summary.unique_symbols, summary.total_occurrences, summary.engineer_review)
    activity.record(db, user, "ifc.drawing_uploaded",
                    f"Read the IFC drawing {name} {plan.revision}"
                    + (f" (revises {supersedes.revision})" if supersedes is not None else "")
                    + f": {len(d['groups'])} distinct symbols"
                    + (f", {d['loose_symbols']} of them drawn without a block" if d["loose_symbols"] else ""),
                    project=project, entity_type="ifc_drawing", entity_id=drawing.id,
                    detail={"file": name, "filed": filed, "converted": bool(conversion), "revision": plan.revision,
                            "reference": plan.reference, "supersedes_id": supersedes_id, "job_id": job_id,
                            "deterministic": summary.deterministic, "ai_verified": summary.ai_verified,
                            "engineer_review": summary.engineer_review})
    # The building's floors, for the Drawings page: the one thing it takes
    # from an IFC drawing. Nothing of any shop drawing's changes.
    try:
        from app.services import shop_drawings

        shop_drawings.floors_changed(db, project)
    except Exception:  # noqa: BLE001 -- the drawing is read; the floor registry catches up on the next change
        db.rollback()
        log.exception("Could not bring the building floors up to the IFC drawings of project %s", project.id)
    return drawing
