from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.project import Project
from app.schemas.project import ProjectCreate


def create_project(db: Session, data: ProjectCreate) -> Project:
    project = Project(name=data.name, description=data.description, owner_id=data.owner_id)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def get_project(db: Session, project_id: int) -> Project | None:
    return db.get(Project, project_id)


def list_projects(db: Session, skip: int = 0, limit: int = 100) -> list[Project]:
    stmt = select(Project).order_by(Project.id).offset(skip).limit(limit)
    return list(db.scalars(stmt))
