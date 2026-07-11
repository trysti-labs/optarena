from __future__ import annotations

import enum

from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class TaskStatus(str, enum.Enum):
    todo = "todo"
    in_progress = "in_progress"
    done = "done"


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.todo)
    priority: Mapped[int] = mapped_column(Integer, default=3)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    project: Mapped["Project"] = relationship(back_populates="tasks")
    assignee: Mapped["User | None"] = relationship(back_populates="tasks")
