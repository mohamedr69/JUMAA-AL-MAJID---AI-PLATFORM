from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_role
from app.models import DivisionProject, RoleEnum, User
from app.routers.estimation import ProjectIn, ProjectOut


def project_router(division: str, role: RoleEnum):
    access = require_role(RoleEnum.admin, role)
    routes = APIRouter(prefix=f"/{division}/projects", tags=[division], dependencies=[Depends(access)])

    @routes.get("", response_model=list[ProjectOut])
    def list_projects(db: Session = Depends(get_db)):
        return db.query(DivisionProject).filter_by(division=division).order_by(DivisionProject.created_at.desc(), DivisionProject.id.desc()).all()

    @routes.post("", response_model=ProjectOut, status_code=201)
    def create_project(payload: ProjectIn, user: User = Depends(access), db: Session = Depends(get_db)):
        project = DivisionProject(**payload.model_dump(), division=division, created_by=user.id)
        project.reference = project.reference.upper()
        db.add(project)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="A project with this reference already exists in your division")
        db.refresh(project)
        return project

    @routes.get("/{project_id}", response_model=ProjectOut)
    def open_project(project_id: int, db: Session = Depends(get_db)):
        project = db.query(DivisionProject).filter_by(id=project_id, division=division).first()
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return project

    return routes


router = APIRouter()
router.include_router(project_router("fire-fighting", RoleEnum.fire_fighting_engineer))
router.include_router(project_router("elv", RoleEnum.elv_engineer))
