"""Propose -- and only on request apply -- repairs to projects created before
the September 2026 review fixes.

The ten reviewed projects were saved by the platform as it was: design
sheets filed as PA, VA, VAS or VE carry no system code, so their BOQ lines
are "Unassigned" and carry no manufacturer; and EP-31112's source folder is
the first folder that matched its number rather than the one its DRF is in.

Nothing here decides for the engineer. The default is a **dry run** that
lists every change it would make, project by project; `--apply` makes
exactly those changes and nothing else. Issued BOQ revisions are snapshots
and are never touched. The engineer's own edits to a line are never touched
either: only a line's *system code* and a *blank* manufacturer are filled.

    cd backend
    .\\venv\\Scripts\\python scripts\\repair_projects.py             # what would change
    .\\venv\\Scripts\\python scripts\\repair_projects.py --apply     # change it
    .\\venv\\Scripts\\python scripts\\repair_projects.py --project 4 # one project

What it proposes, per project:

1. **Design sheet codes.** A sheet whose filename carries a code the
   resolver now understands (PA -> PAVA, VE -> VES) gets that code. A sheet
   with no code, on a project whose DRF marks exactly one system, gets that
   system's code. Anything else stays as it is and is listed.
2. **BOQ line codes.** Where, after (1), every sheet on the project has the
   same code, the project's unassigned lines take it. Where sheets differ,
   the lines cannot be told apart after the fact and are left for the BOQ
   page. The manufacturer is filled from the DRF for lines that have none.
3. **Source folder.** Where the stored DRF is not inside the stored source
   folder, the DRF's own EP folder is proposed instead.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.models import Project  # noqa: E402
from app.routers.projects import _brand_for  # noqa: E402
from app.services.ep_resolver import (  # noqa: E402
    ANY_EP_FOLDER_RE,
    SYSTEM_CODE_RE,
    canonical_system_code,
    infer_single_system,
)


def _sheet_code(filename: str) -> str | None:
    match = SYSTEM_CODE_RE.search(filename)
    return canonical_system_code(match.group(1)) if match else None


def _ep_folder_of(path: Path) -> Path | None:
    """The EP-numbered ancestor of a document path."""
    for parent in path.parents:
        if ANY_EP_FOLDER_RE.match(parent.name):
            return parent
    return None


def propose(project: Project) -> list[tuple[str, callable]]:
    """(description, apply) pairs for one project."""
    changes: list[tuple[str, callable]] = []

    # 1. design sheet codes
    marked = [s.name for s in project.systems]
    inferred = infer_single_system(marked)
    for sheet in project.design_sheets:
        current = canonical_system_code(sheet.system_code)
        proposed = current or _sheet_code(Path(sheet.document_path).name) or inferred
        if proposed and proposed != (sheet.system_code or None):
            why = "filename" if _sheet_code(Path(sheet.document_path).name) else f"DRF marks {proposed} only"
            changes.append((
                f"design sheet {Path(sheet.document_path).name}: system {sheet.system_code!r} -> {proposed!r} ({why})",
                lambda sheet=sheet, proposed=proposed: setattr(sheet, "system_code", proposed),
            ))
        elif not proposed:
            changes.append((f"design sheet {Path(sheet.document_path).name}: no code and none inferable -- left for the engineer", None))

    # 2. BOQ line codes and manufacturers
    codes_after = {
        canonical_system_code(s.system_code) or _sheet_code(Path(s.document_path).name) or inferred
        for s in project.design_sheets
    }
    codes_after.discard(None)
    unassigned = [line for line in project.boq_items if not line.system_code]
    if unassigned and len(codes_after) == 1:
        code = next(iter(codes_after))
        brand = _brand_for(code, project.systems)
        blank_brand = sum(1 for line in unassigned if not (line.manufacturer or "").strip())
        changes.append((
            f"BOQ: {len(unassigned)} unassigned line(s) -> {code}"
            + (f"; manufacturer {brand!r} on the {blank_brand} with none" if brand and blank_brand else ""),
            lambda lines=unassigned, code=code, brand=brand: [
                (setattr(line, "system_code", code),
                 setattr(line, "manufacturer", brand) if brand and not (line.manufacturer or "").strip() else None)
                for line in lines
            ],
        ))
    elif unassigned and len(codes_after) > 1:
        changes.append((
            f"BOQ: {len(unassigned)} unassigned line(s) come from sheets of {sorted(codes_after)}; "
            "which is which cannot be told after the fact -- assign them on the BOQ page",
            None,
        ))

    # 3. source folder
    if project.drf_document_path and project.source_folder_path:
        drf = Path(project.drf_document_path)
        source = Path(project.source_folder_path)
        try:
            inside = drf.resolve().is_relative_to(source.resolve())
        except (OSError, ValueError):
            inside = False
        if not inside:
            ep_folder = _ep_folder_of(drf)
            if ep_folder is not None:
                changes.append((
                    f"source folder: {source} -> {ep_folder} (the DRF's own folder)",
                    lambda project=project, ep_folder=ep_folder: setattr(project, "source_folder_path", str(ep_folder)),
                ))
            else:
                changes.append((f"source folder {source} does not contain the DRF {drf}, and no EP folder of the DRF's was found", None))
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Make the proposed changes. Without it, nothing is written.")
    parser.add_argument("--project", type=int, action="append", default=[], help="Limit to these project ids. Repeatable.")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        query = db.query(Project).order_by(Project.id)
        if args.project:
            query = query.filter(Project.id.in_(args.project))
        projects = query.all()
        total_applicable = 0
        for project in projects:
            changes = propose(project)
            if not changes:
                continue
            print(f"\n[{project.id}] EP-{project.ep_number} {project.project_name or ''}")
            for description, apply in changes:
                mark = "  -" if apply else "  ?"
                print(f"{mark} {description}")
                if apply and args.apply:
                    apply()
                if apply:
                    total_applicable += 1
        if args.apply:
            db.commit()
            print(f"\nApplied {total_applicable} change(s).")
        else:
            print(f"\nDry run: {total_applicable} change(s) would be applied; items marked ? need the engineer. Re-run with --apply.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
