"""
optarena/scenario.py
─────────────────
A *scenario* is one configuration under test: a driver (which tool), a backend
(which server + model), and the cases to run. Comparisons are just two runs of
different scenarios over the same cases.

Scenario files are JSON:

    {
      "name":    "aider-llama",
      "driver":  "aider",
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

from .schema import validate_scenario


@dataclass
class Backend:
    kind: str = "ollama"                     # ollama | openai
    base_url: str = "http://localhost:11434"
    model: str = "llama3.2"
    api_key: str = "optarena"                 # for openai-compat endpoints
    # Only honored by ollama-chat (native /api/chat accepts options.num_ctx
    # per-request). Every other driver goes through the OpenAI-compatible
    # /v1/chat/completions endpoint, which this Ollama version silently
    # ignores an options/num_ctx field on (HTTP 200, no error, but the
    # loaded context stays at whatever OLLAMA_CONTEXT_LENGTH/the model
    # default is) - there is no per-request override for those drivers;
    # raising their context requires OLLAMA_CONTEXT_LENGTH on the server.
    num_ctx: int | None = None

    @property
    def openai_base(self) -> str:
        """The OpenAI-compatible base URL (always ends in /v1), regardless of kind."""
        base = self.base_url.rstrip("/")
        return base if base.endswith("/v1") else base + "/v1"

    def label(self) -> str:
        return f"{self.model}@{re.sub(r'^https?://', '', self.base_url)}"

    def redacted_dict(self) -> dict:
        """Serializable form with the secret stripped. Used anywhere a
        Backend gets written to disk (saved runs, comparisons, logs) - never
        for round-tripping a scenario *file*, where a real key is exactly
        what the user is configuring. `api_key_set` records whether a
        non-default key was supplied, without ever writing its value."""
        d = vars(self).copy()
        d["api_key_set"] = bool(self.api_key) and self.api_key != "optarena"
        d["api_key"] = None
        return d


@dataclass
class Scenario:
    name: str
    driver: str
    backend: Backend = field(default_factory=Backend)
    cases: list[str] | None = None           # None ⇒ all cases
    timeout: int | None = None               # per-case override, seconds
    cases_dir: str | None = None             # None ⇒ the built-in catalogue

    @classmethod
    def from_dict(cls, data: dict, source: str = "<scenario>") -> "Scenario":
        # Structural validation before anything else touches this dict - a
        # malformed/fuzzed scenario file (unknown key, wrong type, an
        # out-of-range timeout) fails fast here with a clear file+key error,
        # not as a confusing KeyError/TypeError after a driver/backend call
        # may already have run.
        validate_scenario(data, source=source)
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
        path = Path(path)
        sc = cls.from_dict(json.loads(path.read_text(encoding="utf-8")), source=str(path))
        # A relative `cases_dir` names a directory next to the scenario
        # FILE, not the process's current working directory - otherwise the
        # same scenario resolves to a different (or missing) case set
        # depending on where `optarena run` happens to be invoked from.
        # Absolute paths and the None/built-in-catalogue default pass through
        # unchanged.
        if sc.cases_dir and not Path(sc.cases_dir).is_absolute():
            sc.cases_dir = str((path.resolve().parent / sc.cases_dir).resolve())
        return sc

    def to_dict(self, *, redact: bool = False) -> dict:
        """`redact=True` for anything written to disk (RunRecord, comparisons,
        logs) - strips `backend.api_key`. `redact=False` (default) is for
        round-tripping a scenario definition itself (e.g. re-serializing a
        loaded scenario file), where the real key is exactly what's being
        configured, not a secret being persisted as evaluation output."""
        return {
            "name": self.name,
            "driver": self.driver,
            "backend": self.backend.redacted_dict() if redact else vars(self.backend),
            "cases": self.cases,
            "timeout": self.timeout,
            "cases_dir": self.cases_dir,
        }
