"""Shared paths/registries with no dependency on any other `_cases` submodule."""

from __future__ import annotations

from pathlib import Path

CASES_DIR = Path(__file__).resolve().parent.parent / "cases"
DOCKER_IMAGE_DEFAULT = "optarena-tester:latest"
DOCKERFILE_DIR = Path(__file__).resolve().parent.parent.parent / "docker"

# Registry of every sandbox image OptArena knows how to build, keyed by the
# short name used with `optarena sandbox build --lang <key>`. "base" is the
# original combined image (gcc + python3 + node) and stays the default for
# cases with no `image` set, so the 7 original cases are unaffected.
# Per-language images add a framework's dependencies pre-fetched at build
# time (the sandbox runs with --network none, so nothing can be installed
# at check_command time - it must already be in the image).
DOCKER_IMAGES: dict[str, str] = {
    "base": DOCKER_IMAGE_DEFAULT,
    "python": "optarena-tester-python:latest",
    "node": "optarena-tester-node:latest",
    "jvm": "optarena-tester-jvm:latest",
    "go": "optarena-tester-go:latest",
    "rust": "optarena-tester-rust:latest",
    "dotnet": "optarena-tester-dotnet:latest",
    "php": "optarena-tester-php:latest",
    "ruby": "optarena-tester-ruby:latest",
    # Sandboxed-real tool-use execution (--tool-service-mode sandboxed): runs
    # the ACTUAL reference MCP server binary instead of the in-process mock -
    # see optarena/_cases/_sandboxed_mcp_service.py. Not a check_command
    # toolchain image like every entry above.
    "mcp-filesystem": "optarena-tester-mcp-filesystem:latest",
    "mcp-git-repo": "optarena-tester-mcp-git-repo:latest",
    "mcp-code-intel": "optarena-tester-mcp-code-intel:latest",
    "mcp-build-tools": "optarena-tester-mcp-build-tools:latest",
    "mcp-database": "optarena-tester-mcp-database:latest",
    "mcp-observability": "optarena-tester-mcp-observability:latest",
    "mcp-package-registry": "optarena-tester-mcp-package-registry:latest",
    "mcp-cloud-infra": "optarena-tester-mcp-cloud-infra:latest",
    "mcp-docker": "optarena-tester-mcp-docker:latest",
    "mcp-kubernetes": "optarena-tester-mcp-kubernetes:latest",
}

IGNORE_DIRS = {".git", ".vscode", ".aider", "node_modules", "__pycache__"}

# ── L3 (repo-scale) cases: a shared starter repo copied in, not inlined ────────
REPOS_DIR = Path(__file__).resolve().parent.parent.parent / "repos"
