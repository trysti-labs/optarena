from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import crud, schemas
from app.db.session import get_db
from app.models.task import TaskStatus

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=schemas.TaskOut, status_code=201)
def create_task(payload: schemas.TaskCreate, db: Session = Depends(get_db)):
    return crud.create_task(db, payload)


@router.get("", response_model=list[schemas.TaskOut])
def list_tasks(
    project_id: int | None = None,
    status: TaskStatus | None = None,
    assignee_id: int | None = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    return crud.list_tasks(
        db, project_id=project_id, status=status, assignee_id=assignee_id, skip=skip, limit=limit,
    )


@router.get("/{task_id}", response_model=schemas.TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = crud.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@router.patch("/{task_id}", response_model=schemas.TaskOut)
def update_task(task_id: int, payload: schemas.TaskUpdate, db: Session = Depends(get_db)):
    task = crud.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return crud.update_task(db, task, payload)


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = crud.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    crud.delete_task(db, task)
