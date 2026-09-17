"""The project's proposed materials, and the part catalogue behind them.

  GET    /projects/{id}/materials             the BOQ's parts and the ones added, per system
  POST   /projects/{id}/materials             propose a material (no quantity needed)
  DELETE /projects/{id}/materials/{mid}       withdraw one that was added
  GET    /parts/search?brand=&q=              part numbers on file for a brand, as typed
"""

from __future__ import annotations

from datetime import datetime

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import Project, ProjectProposedMaterial, User
from app.routers.projects import CREATOR_ROLES, _get_project_or_404
from app.routers.submittal import _attach_datasheets, _brand_of, _materials
from app.schemas_design import MaterialItemOut
from app.services import activity, part_catalog, system_rules
from app.services.datasheet_library import get_libraries

router = APIRouter(tags=["materials"])


class ProposedSystemOut(BaseModel):
    code: str
    title: str
    brand: str | None


class ProposedMaterialOut(MaterialItemOut):
    # "boq" for a part the BOQ quotes; "added" for one proposed here (with its
    # id); "battery" for a battery the battery calculation selected.
    source: str = "boq"
    id: int | None = None
    note: str | None = None
    added_at: datetime | None = None


class ProposedMaterialsOut(BaseModel):
    systems: list[ProposedSystemOut]
    items: list[ProposedMaterialOut]


class ProposedMaterialIn(BaseModel):
    system_code: str | None = None
    catalog_no: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=300)
    manufacturer: str | None = Field(default=None, max_length=120)
    quantity: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=2000)


class PartSuggestionOut(BaseModel):
    part_no: str
    description: str
    sources: list[str]


def _systems(project: Project) -> list[ProposedSystemOut]:
    codes = system_rules.project_codes(project) or []
    for item in project.boq_items:
        if item.system_code and item.system_code not in codes:
            codes.append(item.system_code)
    return [ProposedSystemOut(code=code, title=system_rules.system_display_name(project, code), brand=_brand_of(project, code))
            for code in codes]


@router.get("/projects/{project_id}/materials", response_model=ProposedMaterialsOut)
def list_proposed(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProposedMaterialsOut:
    """The BOQ's parts (from the saved BOQ) and the materials added here,
    each with the datasheet found for it -- from the database and the
    library index; nothing is scanned."""
    project = _get_project_or_404(db, project_id)
    items: list[ProposedMaterialOut] = [ProposedMaterialOut(**m.model_dump(), source="boq") for m in _materials(project)]
    added = (db.query(ProjectProposedMaterial).filter(ProjectProposedMaterial.project_id == project.id)
             .order_by(ProjectProposedMaterial.id).all())
    extra = [ProposedMaterialOut(system_code=row.system_code, part_no=row.catalog_no, description=row.description or "",
                                 manufacturer=row.manufacturer, quantity=_number(row.quantity), groups=[], source="added",
                                 id=row.id, note=row.note, added_at=row.created_at) for row in added]
    _attach_datasheets(extra, get_libraries(), db)
    items.extend(extra)
    # The batteries the fire alarm's battery calculation selects.
    from app.services import battery_materials

    libraries = get_libraries()
    for battery in battery_materials.selected_batteries(db, project):
        item = ProposedMaterialOut(system_code="FAS", part_no=battery.catalog_no, description=battery.description,
                                   manufacturer=battery.manufacturer, quantity=battery.quantity or None,
                                   groups=[f"Battery calculation: {', '.join(battery.panels)}"], source="battery",
                                   datasheet_library=battery.datasheet_library, datasheet_path=battery.datasheet_path)
        if item.datasheet_path:
            item.datasheet_filename = item.datasheet_path.rsplit("/", 1)[-1]
            item.datasheet_named_for_part = True
        else:
            _attach_datasheets([item], libraries, db)
        items.append(item)
    return ProposedMaterialsOut(systems=_systems(project), items=items)


class DatasheetLinkIn(BaseModel):
    manufacturer: str = Field(min_length=1, max_length=64)
    part_no: str = Field(min_length=1, max_length=120)
    library: str = Field(min_length=1, max_length=64)
    path: str = Field(min_length=1, max_length=500)
    note: str | None = Field(default=None, max_length=500)


class DatasheetLinkOut(BaseModel):
    manufacturer: str
    part_no: str
    library: str
    path: str
    filename: str
    note: str | None
    source: str


@router.put("/materials/datasheet-link", response_model=DatasheetLinkOut)
def link_datasheet(
    payload: DatasheetLinkIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> DatasheetLinkOut:
    """Link a part to the datasheet that documents it, for every project:
    the file names of the library do not carry its number, so the search
    cannot find it; this settles it once."""
    from app.services import datasheet_links

    libraries = get_libraries()
    library = libraries.get(payload.library) or next((lib for name, lib in libraries.items() if name.upper() == payload.library.upper()), None)
    if library is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No datasheet library named {payload.library}")
    target = library.folder / payload.path
    if not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{payload.path} is not in the {library.name} library")
    row = datasheet_links.link(db, manufacturer=payload.manufacturer, part_no=payload.part_no, library=library.name,
                               path=payload.path, note=payload.note, user_id=current_user.id)
    activity.record(db, current_user, "material.datasheet_linked",
                    f"Linked {row.part_no} ({row.manufacturer}) to the datasheet {target.name}",
                    entity_type="material", detail={"library": row.library, "path": row.path})
    return DatasheetLinkOut(manufacturer=row.manufacturer, part_no=row.part_no, library=row.library, path=row.path,
                            filename=target.name, note=row.note, source=row.source)


@router.delete("/materials/datasheet-link", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def unlink_datasheet(
    manufacturer: str = Query(max_length=64),
    part_no: str = Query(max_length=120),
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
):
    from app.services import datasheet_links

    if not datasheet_links.unlink(db, manufacturer=manufacturer, part_no=part_no):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such link")
    activity.record(db, current_user, "material.datasheet_unlinked", f"Unlinked {part_no} ({manufacturer}) from its datasheet",
                    entity_type="material")


def _number(text: str | None) -> float | None:
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


@router.post("/projects/{project_id}/materials", response_model=ProposedMaterialOut, status_code=status.HTTP_201_CREATED)
def propose(
    project_id: int,
    payload: ProposedMaterialIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> ProposedMaterialOut:
    """Propose a material for a system. The manufacturer is the system's
    (the DRF's brand) unless given; the description is filled from the
    catalogue when the part is on file and none is given."""
    project = _get_project_or_404(db, project_id)
    system_code = system_rules.effective_code(payload.system_code, project) if payload.system_code else None
    manufacturer = (payload.manufacturer or _brand_of(project, system_code) or "").strip().upper() or None
    description = (payload.description or "").strip()
    if not description:
        known = next((e for e in part_catalog.search(db, manufacturer, payload.catalog_no, limit=5)
                      if part_catalog.part_key(e.part_no) == part_catalog.part_key(payload.catalog_no)), None)
        description = known.description if known else ""
    row = ProjectProposedMaterial(project_id=project.id, system_code=system_code, manufacturer=manufacturer,
                                  catalog_no=payload.catalog_no.strip(), description=description or None,
                                  quantity=(payload.quantity or "").strip() or None, note=(payload.note or "").strip() or None,
                                  created_by_id=current_user.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    activity.record(db, current_user, "material.proposed", f"Proposed {row.catalog_no} for {system_code or 'the project'}",
                    project=project, entity_type="material", entity_id=row.id,
                    detail={"system": system_code, "manufacturer": manufacturer, "description": description})
    out = ProposedMaterialOut(system_code=row.system_code, part_no=row.catalog_no, description=row.description or "",
                              manufacturer=row.manufacturer, quantity=_number(row.quantity), groups=[], source="added",
                              id=row.id, note=row.note, added_at=row.created_at)
    _attach_datasheets([out], get_libraries(), db)
    return out


@router.get("/projects/{project_id}/materials/schedule.pdf")
def export_schedule(
    project_id: int,
    system_code: str | None = Query(default=None, max_length=16),
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The Schedule of Material for one system, as the submittal package
    encloses it (section 5): the BOQ's parts by assembly, then the
    materials added on the tab. From the database; nothing scanned."""
    from app.services.submittal_package import build_schedule

    project = _get_project_or_404(db, project_id)
    code = system_rules.effective_code(system_code, project) if system_code else None
    doc = build_schedule(project, code)
    pdf = doc.tobytes()
    doc.close()
    name = f"EP-{project.ep_number} - Schedule of Material{' - ' + code if code else ''}.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name)}"})


@router.delete("/projects/{project_id}/materials/{material_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def withdraw(
    project_id: int,
    material_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
):
    project = _get_project_or_404(db, project_id)
    row = db.get(ProjectProposedMaterial, material_id)
    if row is None or row.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Proposed material not found")
    activity.record(db, current_user, "material.withdrawn", f"Withdrew the proposed material {row.catalog_no}",
                    project=project, entity_type="material", entity_id=row.id, commit=False)
    db.delete(row)
    db.commit()


@router.get("/parts/search", response_model=list[PartSuggestionOut])
def search_parts(
    brand: str | None = Query(default=None, max_length=64),
    q: str = Query(default="", max_length=64),
    limit: int = Query(default=20, ge=1, le=100),
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PartSuggestionOut]:
    """Part numbers on file for the brand that match what was typed."""
    return [PartSuggestionOut(**e.as_dict()) for e in part_catalog.search(db, brand, q, limit=limit)]
