from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ProjectBase(BaseModel):
    name: str
    description: str = ""


class ProjectCreate(ProjectBase):
    owner_id: int


class ProjectOut(ProjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
