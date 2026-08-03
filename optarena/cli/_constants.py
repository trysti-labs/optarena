"""Shared paths with no dependency on any other `cli` submodule."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
