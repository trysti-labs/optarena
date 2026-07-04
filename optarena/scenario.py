"""
optarena/scenario.py
─────────────────
A *scenario* is one configuration under test: a driver (which tool), a backend
(which server + model), and the cases to run. Comparisons are just two runs of
different scenarios over the same cases.

Scenario files are JSON:

    {
      "name":    "cline-llama",
      "driver":  "cline-ui",
      "backend": {
        "kind":     "ollama",                  // ollama | openai
        "base_url": "http://localhost:8001",
        "model":    "llama3.2"
      },
      "cases":   ["create_factorial", ...],    // omit for all
      "timeout": 180                           // optional per-case override (s)
    }

Scenarios can also be built inline from CLI flags without a file.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Backend:
    kind: str = "ollama"                     # ollama | openai
    base_url: str = "http://localhost:11434"
    model: str = "llama3.2"
    api_key: str = "optarena"                 # for openai-compat endpoints

    @property
    def openai_base(self) -> str:
        """The OpenAI-compatible base URL (always ends in /v1), regardless of kind."""
        base = self.base_url.rstrip("/")
        return base if base.endswith("/v1") else base + "/v1"

    def label(self) -> str:
        return f"{self.model}@{re.sub(r'^https?://', '', self.base_url)}"


@dataclass
class Scenario:
    name: str
    driver: str
    backend: Backend = field(default_factory=Backend)
    cases: list[str] | None = None           # None ⇒ all cases
    timeout: int | None = None               # per-case override, seconds
    cases_dir: str | None = None             # None ⇒ the built-in catalogue

    @classmethod
    def from_dict(cls, data: dict) -> "Scenario":
        backend = Backend(**data.get("backend", {}))
        return cls(
            name=data["name"],
            driver=data["driver"],
            backend=backend,
            cases=data.get("cases"),
            timeout=data.get("timeout"),
            cases_dir=data.get("cases_dir"),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "Scenario":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "driver": self.driver,
            "backend": vars(self.backend),
            "cases": self.cases,
            "timeout": self.timeout,
            "cases_dir": self.cases_dir,
        }
