"""
optarena/packs.py
─────────────────
Case *packs*: version, share, discover, and pull task packs - the "no registry /
versioning" gap. A pack is a single self-describing JSON file (portable: commit
it, email it, host it at a URL, drop it in a gist) that bundles a directory of
cases with a name, a semantic version, and a content hash. Installed packs live
in a local registry (``~/.optarena/packs/<name>@<version>/``) that `run --pack`
resolves and that `cases packs` lists - a hosted hub is a later, optional layer;
this gives versioning + sharing + discovery with zero infrastructure, matching
the rest of the tool.

    optarena cases pack ./mycases --name web-suite --version 1.2.0   # build
    optarena cases install web-suite-1.2.0.optpack.json             # local file
    optarena cases install https://example.com/web-suite.optpack.json  # or URL
    optarena cases packs                                            # discover
    optarena run --pack web-suite --driver aider --name r1         # use

The content hash makes a pack immutable-by-identity: two installs of the same
name@version must hash-match or the install is refused, so a run's manifest can
cite exactly which pack bits it measured.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.request
from pathlib import Path

from .schema import validate_case, validate_unique_case_names

PACK_FORMAT = 1
PACKS_DIR = Path.home() / ".optarena" / "packs"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.IGNORECASE)


def _safe(s: str) -> str:
    return re.sub(r"[^\w.\-+]+", "-", s)


def content_hash(cases: dict[str, dict]) -> str:
    """Stable sha256 over the pack's cases (filename + canonical JSON), so the
    same content always yields the same hash regardless of dict/file order."""
    h = hashlib.sha256()
    for name in sorted(cases):
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(json.dumps(cases[name], sort_keys=True, ensure_ascii=False).encode("utf-8"))
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


def build_pack(cases_dir: str | Path, name: str, version: str) -> dict:
    """Read every ``*.json`` case from *cases_dir* (validated) into a pack dict."""
    if not _NAME_RE.match(name):
        raise ValueError(f"invalid pack name {name!r} (use letters, digits, . _ -)")
    directory = Path(cases_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"cases dir not found: {directory}")
    cases: dict[str, dict] = {}
    loaded = []
    for p in sorted(directory.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        validate_case(data, source=str(p))
        cases[p.name] = data
        loaded.append(data)
    if not cases:
        raise ValueError(f"no *.json cases found in {directory}")
    validate_unique_case_names(loaded, source=str(directory))
    return {
        "optarena_pack": PACK_FORMAT,
        "name": name,
        "version": version,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "case_count": len(cases),
        "hash": content_hash(cases),
        "cases": cases,
    }


def write_pack(pack: dict, out: str | Path | None = None) -> Path:
    out = Path(out) if out else Path(f"{_safe(pack['name'])}-{_safe(pack['version'])}.optpack.json")
    out.write_text(json.dumps(pack, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def load_pack(source: str) -> dict:
    """Load a pack from a local path or an http(s) URL, validating its shape and
    that its declared hash matches its contents (tamper/corruption check)."""
    if re.match(r"^https?://", source):
        with urllib.request.urlopen(source, timeout=30) as resp:  # noqa: S310 - user-supplied URL, documented
            raw = resp.read().decode("utf-8")
    else:
        raw = Path(source).read_text(encoding="utf-8")
    pack = json.loads(raw)
    if not isinstance(pack, dict) or pack.get("optarena_pack") != PACK_FORMAT:
        raise ValueError("not an OptArena pack (missing/other optarena_pack version)")
    for key in ("name", "version", "cases"):
        if key not in pack:
            raise ValueError(f"pack missing required key: {key}")
    if not _NAME_RE.match(str(pack["name"])):
        raise ValueError(f"pack has an invalid name: {pack['name']!r}")
    actual = content_hash(pack["cases"])
    if pack.get("hash") and pack["hash"] != actual:
        raise ValueError(f"pack hash mismatch (declared {pack['hash']}, computed {actual}) "
                         "- refusing to install a tampered/corrupt pack")
    pack["hash"] = actual
    return pack


def install_pack(pack: dict, packs_dir: Path | None = None, force: bool = False) -> Path:
    """Extract a loaded pack into ``<packs_dir>/<name>@<version>/`` (one case
    file each) plus a ``_pack.json`` manifest. Refuses to overwrite a different
    build of the same name@version (hash mismatch) unless *force*."""
    root = (packs_dir or PACKS_DIR) / f"{_safe(pack['name'])}@{_safe(pack['version'])}"
    if root.exists() and not force:
        existing = root / "_pack.json"
        if existing.exists():
            prev = json.loads(existing.read_text(encoding="utf-8"))
            if prev.get("hash") == pack["hash"]:
                return root  # already installed, identical - idempotent
            raise FileExistsError(
                f"{pack['name']}@{pack['version']} already installed with a DIFFERENT hash "
                f"({prev.get('hash')} vs {pack['hash']}); pass force=True to overwrite")
    root.mkdir(parents=True, exist_ok=True)
    # Clear any stale case files from a forced reinstall.
    for old in root.glob("*.json"):
        if old.name != "_pack.json":
            old.unlink()
    for fname, data in pack["cases"].items():
        (root / _safe(fname)).write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest = {k: v for k, v in pack.items() if k != "cases"}
    (root / "_pack.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return root


def list_installed(packs_dir: Path | None = None) -> list[dict]:
    """Installed packs (newest install first), for `cases packs` discovery."""
    root = packs_dir or PACKS_DIR
    if not root.is_dir():
        return []
    out = []
    for d in root.iterdir():
        man = d / "_pack.json"
        if d.is_dir() and man.is_file():
            try:
                out.append(json.loads(man.read_text(encoding="utf-8")) | {"path": str(d)})
            except (OSError, ValueError):
                continue
    out.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return out


def resolve_pack(ref: str, packs_dir: Path | None = None) -> Path:
    """Resolve ``name`` or ``name@version`` to an installed pack directory for
    `run --pack`. Bare name picks the highest installed version."""
    root = packs_dir or PACKS_DIR
    if "@" in ref:
        d = root / _safe(ref.replace("@", "@"))
        # normalize name@version -> name@version dir name
        name, version = ref.split("@", 1)
        d = root / f"{_safe(name)}@{_safe(version)}"
        if d.is_dir():
            return d
        raise FileNotFoundError(f"pack not installed: {ref} (see `optarena cases packs`)")
    candidates = [m for m in list_installed(packs_dir) if m.get("name") == ref]
    if not candidates:
        raise FileNotFoundError(f"pack not installed: {ref} (see `optarena cases packs`)")
    # Highest version string wins; fall back to newest install.
    candidates.sort(key=lambda m: (str(m.get("version", "")), m.get("created_at", "")), reverse=True)
    return Path(candidates[0]["path"])
