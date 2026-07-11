from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    database_url: str = os.environ.get("TASKTRACKER_DATABASE_URL", "sqlite:///./tasktracker.db")


settings = Settings()
