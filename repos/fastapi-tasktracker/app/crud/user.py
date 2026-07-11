from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.user import UserCreate


def create_user(db: Session, data: UserCreate) -> User:
    user = User(username=data.username, email=data.email, full_name=data.full_name)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def list_users(db: Session, skip: int = 0, limit: int = 100) -> list[User]:
    stmt = select(User).order_by(User.id).offset(skip).limit(limit)
    return list(db.scalars(stmt))
