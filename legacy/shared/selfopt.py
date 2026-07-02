"""
shared/selfopt.py
─────────────────
SelfOpt server health-check helpers.
"""

from __future__ import annotations

import json
import urllib.request


def selfopt_healthy(url: str = "http://localhost:8001") -> bool:
    """Return True if the SelfOpt server is responding."""
    try:
        resp = urllib.request.urlopen(f"{url}/api/tags", timeout=5)
        data = json.loads(resp.read())
        return isinstance(data.get("models"), list)
    except Exception:
        return False


def require_selfopt(url: str = "http://localhost:8001") -> None:
    """Raise RuntimeError if SelfOpt is not reachable."""
    if not selfopt_healthy(url):
        raise RuntimeError(
            f"SelfOpt server is not reachable at {url}. "
            "Start it with: selfopt start --port 8001"
        )
