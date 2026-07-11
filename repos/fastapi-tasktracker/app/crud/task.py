from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.task import Task, TaskStatus
from app.schemas.task import TaskCreate, TaskUpdate


def create_task(db: Session, data: TaskCreate) -> Task:
    task = Task(
        title=data.title,
        description=data.description,
        priority=data.priority,
        project_id=data.project_id,
        assignee_id=data.assignee_id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def get_task(db: Session, task_id: int) -> Task | None:
    return db.get(Task, task_id)


def list_tasks(
    db: Session,
    project_id: int | None = None,
    status: TaskStatus | None = None,
    assignee_id: int | None = None,
    skip: int = 0,
    limit: int = 100,
) -> list[Task]:
    stmt = select(Task).order_by(Task.id)
    if project_id is not None:
        stmt = stmt.where(Task.project_id == project_id)
    if status is not None:
        stmt = stmt.where(Task.status == status)
    if assignee_id is not None:
        stmt = stmt.where(Task.assignee_id == assignee_id)
    stmt = stmt.offset(skip).limit(limit)
    return list(db.scalars(stmt))


def update_task(db: Session, task: Task, data: TaskUpdate) -> Task:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(task, field, value)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def delete_task(db: Session, task: Task) -> None:
    db.delete(task)
    db.commit()
