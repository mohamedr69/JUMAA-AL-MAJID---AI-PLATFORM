from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_role
from app.models import EstimationProject, RoleEnum, User

access = require_role(RoleEnum.admin, RoleEnum.fire_alarm_estimation_engineer)
router = APIRouter(prefix="/estimation/projects", tags=["estimation"], dependencies=[Depends(access)])


class ProjectIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    reference: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=255)
    client: str = Field(default="", max_length=255)


class ProjectOut(ProjectIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)):
    return db.query(EstimationProject).order_by(EstimationProject.created_at.desc(), EstimationProject.id.desc()).all()


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectIn, user: User = Depends(access), db: Session = Depends(get_db)):
    project = EstimationProject(**payload.model_dump(), created_by=user.id)
    project.reference = project.reference.upper()
    db.add(project)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An estimation project with this reference already exists")
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectOut)
def open_project(project_id: int, db: Session = Depends(get_db)):
    project = db.get(EstimationProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Estimation project not found")
    return project
