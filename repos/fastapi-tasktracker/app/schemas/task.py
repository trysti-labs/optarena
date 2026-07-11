from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.models.task import TaskStatus


class TaskBase(BaseModel):
    title: str
    description: str = ""
    priority: int = 3


class TaskCreate(TaskBase):
    project_id: int
    assignee_id: int | None = None


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: TaskStatus | None = None
    priority: int | None = None
    assignee_id: int | None = None


class TaskOut(TaskBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: TaskStatus
    project_id: int
    assignee_id: int | None = None
