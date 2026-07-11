from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120), default="")
    is_active: Mapped[bool] = mapped_column(default=True)

    projects: Mapped[list["Project"]] = relationship(back_populates="owner")
    tasks: Mapped[list["Task"]] = relationship(back_populates="assignee")
