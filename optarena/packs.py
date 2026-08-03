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
import shutil
import subprocess
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

from .schema import validate_case, validate_unique_case_names

PACK_FORMAT = 1
PACKS_DIR = Path.home() / ".optarena" / "packs"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.IGNORECASE)

# P1-06: a publisher's public identity, one per line, in the exact format
# `ssh-keygen -Y verify`'s `-f` (allowed-signers) option expects:
# "<identity> <key-type> <base64-key>" (the same format `ssh` itself uses
# for its own known_hosts/authorized_keys - not an optarena invention).
# Explicit trusted-publisher keyring (P1-06's own resolution wording) - a
# pack signed by an identity NOT in this file is not "verified", however
# well-formed the signature itself is; see `add_trusted_publisher`.
TRUSTED_PUBLISHERS_FILE = Path.home() / ".optarena" / "trusted_publishers"

# Binds a signature to "an optarena pack", specifically - the same detached
# SSH signature over the same bytes could otherwise be replayed as if it
# authorized something else entirely (ssh-keygen's own "sign for a
# different purpose" confusion this namespace exists to prevent).
_SIGN_NAMESPACE = "optarena-pack"


def _version_key(v: str) -> tuple:
    """
    Best-effort SEMANTIC version sort key (F-11) - versions were previously
    compared as plain strings, so "1.9.0" sorted ABOVE "1.10.0" (lexical '9'
    > '1'), which would resolve a bare `--pack name` reference to the older
    release. Parses a MAJOR[.MINOR[.PATCH]] numeric prefix and compares those
    components as integers; anything after that (pre-release/build metadata,
    e.g. "-rc.1") compares as a string tail. A version string with no numeric
    prefix at all still sorts consistently (not crashing) rather than being
    "correct" semver - this project has no external semver dependency to
    reach for and doesn't need full spec compliance, just "1.10 beats 1.9".
    """
    m = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(.*)$", v or "")
    if not m:
        return (-1, -1, -1, v or "")
    major, minor, patch, rest = m.groups()
    return (int(major), int(minor or 0), int(patch or 0), rest or "")


# F-03: a pack can legitimately bundle many cases, so this is far more
# generous than the backend-response cap, but still bounds the worst case -
# `resp.read()` with no limit at all would buffer an unbounded amount of
# memory for a malicious/misconfigured pack URL before anything downstream
# (JSON parsing, hash check, schema validation) gets a chance to reject it.
_MAX_PACK_BYTES = 64 * 1024 * 1024


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


def _signable_blob(pack: dict) -> bytes:
    """Canonical bytes a signature covers: name, version, content hash, and
    case count - the pack's own core identity, not the whole file. `hash`
    already commits to every case's exact content (content_hash), so
    signing it transitively covers the case bodies without needing to sign
    (or verify) a potentially large blob directly - the same "sign the
    digest, not the payload" shape this project's Docker image signing
    already uses (cosign/attest-build-provenance sign an image DIGEST).
    Stable regardless of dict key order or which OTHER fields (a live
    `signature` block, timestamps, the actual case bodies) are present."""
    core = {"name": pack["name"], "version": pack["version"],
            "hash": pack["hash"], "case_count": pack["case_count"]}
    return json.dumps(core, sort_keys=True, ensure_ascii=False).encode("utf-8")


def sign_pack(pack: dict, key_path: "str | Path", signer_id: "str | None" = None) -> dict:
    """
    P1-06: sign a pack's identity (name/version/hash/case_count) with an SSH
    private key, embedding the detached signature and signer identity into
    the pack. Uses ``ssh-keygen -Y sign`` (OpenSSH >=8.0's own file-signing
    feature - real Ed25519/RSA/ECDSA signatures, not a hand-rolled scheme)
    via `subprocess` rather than adding a pip cryptography dependency to
    this stdlib-only package - the same "shell out to an established
    external tool for real signing" shape this project's Docker image
    publishing already uses for cosign, just invoked from the CLI itself
    instead of only from CI.

    ``signer_id`` is the identity string recorded in the signature and
    later matched against a verifier's trusted-publisher keyring - defaults
    to the key file's own name (e.g. signing with ``trysti-labs.key``
    records signer ``"trysti-labs.key"``) when not given explicitly.
    """
    key_path = Path(key_path)
    if not key_path.is_file():
        raise FileNotFoundError(f"signing key not found: {key_path}")
    signer_id = signer_id or key_path.stem
    blob = _signable_blob(pack)
    with tempfile.TemporaryDirectory(prefix="optarena_packsign_") as td:
        data_path = Path(td) / "pack.blob"
        data_path.write_bytes(blob)
        try:
            proc = subprocess.run(
                ["ssh-keygen", "-Y", "sign", "-f", str(key_path), "-n", _SIGN_NAMESPACE, str(data_path)],
                capture_output=True, text=True, timeout=30,
            )
        except OSError as exc:
            raise ValueError(
                f"could not run ssh-keygen to sign this pack (is OpenSSH installed?): {exc}") from exc
        if proc.returncode != 0:
            raise ValueError(f"pack signing failed: {(proc.stderr or proc.stdout).strip()}")
        sig_path = data_path.with_name(data_path.name + ".sig")
        signature = sig_path.read_text(encoding="utf-8")
    pack["signature"] = {"signer": signer_id, "namespace": _SIGN_NAMESPACE, "sig": signature}
    return pack


def verify_pack_signature(pack: dict, trusted_publishers_file: "Path | None" = None) -> dict:
    """
    P1-06: returns ``{"signed": bool, "signer": str | None, "trusted": bool,
    "tampered": bool, "detail": str}`` - never raises. Signature
    verification is information for the CALLER to act on (refuse an
    install, print a warning), not an exception path on its own: an
    unsigned pack is the common, expected, and still-installable case, not
    a malformed one.

    "Trusted" means: signed, AND the signer identity is present in
    ``trusted_publishers_file`` (an explicit, locally-maintained keyring -
    see ``add_trusted_publisher``) with a key that verifies against
    ``ssh-keygen -Y verify``. A well-formed signature from an UNKNOWN
    signer (not in the keyring, or no keyring configured at all) is
    reported as signed-but-not-trusted, same as no signature at all for
    any decision that gates on trust.

    "Tampered" is a NARROWER, stronger signal than "not trusted": it means
    the signer identity WAS found in the keyring, but the cryptographic
    check against that specific key still failed - i.e. this pack's
    content changed after signing, or something is impersonating a known
    identity without holding its private key. Distinguished from "identity
    simply not in my keyring yet" by ``ssh-keygen -Y verify``'s own stderr
    (confirmed live: an unknown identity produces only "Could not verify
    signature.", while a known identity with a bad signature additionally
    prints "Signature verification failed: incorrect signature." first) -
    `load_pack` refuses a tampered pack unconditionally, even a local file,
    since this is never a legitimate "haven't decided to trust this yet"
    state the way an unknown signer is.
    """
    sig = pack.get("signature")
    if not sig:
        return {"signed": False, "signer": None, "trusted": False, "tampered": False,
                "detail": "pack is not signed"}
    # P1-10: `pack["signature"]` is untrusted input (part of the pack JSON
    # being verified, not derived independently) - a malformed shape (not a
    # dict, or missing/non-string required fields) can't be safely used at
    # all. Treated as `tampered=True`, not merely "unsigned": a pack that
    # carries something signature-SHAPED but unparseable is a stronger red
    # flag than carrying nothing, and `tampered` is the one state
    # `load_pack` already refuses unconditionally (even local, even with
    # `--allow-unsigned`) - the same conservative treatment belongs here.
    if (not isinstance(sig, dict)
            or not isinstance(sig.get("signer"), str) or not sig.get("signer")
            or not isinstance(sig.get("sig"), str) or not sig.get("sig")):
        return {"signed": True, "signer": (sig.get("signer") if isinstance(sig, dict) else None),
                "trusted": False, "tampered": True,
                "detail": "malformed signature block (not a well-formed {signer, sig} object) - "
                          "refusing rather than guessing what it means"}
    signer_id = sig["signer"]
    signers_file = trusted_publishers_file or TRUSTED_PUBLISHERS_FILE
    if not Path(signers_file).is_file():
        return {"signed": True, "signer": signer_id, "trusted": False, "tampered": False,
                "detail": f"signed by {signer_id!r}, but no trusted-publisher keyring exists at "
                          f"{signers_file} - nothing can be verified as trusted yet "
                          f"(see `optarena cases trust-publisher`)"}
    blob = _signable_blob(pack)
    with tempfile.TemporaryDirectory(prefix="optarena_packverify_") as td:
        sig_path = Path(td) / "pack.blob.sig"
        sig_path.write_text(sig["sig"], encoding="utf-8")
        try:
            proc = subprocess.run(
                # P1-10: the namespace is ALWAYS the fixed constant, never
                # `sig.get("namespace")` - a namespace is a domain-separation
                # boundary the VERIFIER dictates, not something the
                # untrusted pack itself gets to choose. Reading it from the
                # pack let a signature a trusted publisher made for an
                # unrelated purpose (or under a namespace of an attacker's
                # choosing that the same key happens to have signed
                # something under) verify successfully here even though it
                # was never meant to authorize an optarena pack - confirmed
                # live as a real, reproducible bypass before this fix.
                ["ssh-keygen", "-Y", "verify", "-f", str(signers_file),
                 "-I", signer_id, "-n", _SIGN_NAMESPACE, "-s", str(sig_path)],
                input=blob, capture_output=True, timeout=30,
            )
        except OSError as exc:
            return {"signed": True, "signer": signer_id, "trusted": False, "tampered": False,
                    "detail": f"could not run ssh-keygen to verify this signature "
                              f"(is OpenSSH installed?): {exc}"}
        except subprocess.TimeoutExpired:
            return {"signed": True, "signer": signer_id, "trusted": False, "tampered": False,
                    "detail": "ssh-keygen verification timed out"}
    if proc.returncode == 0:
        return {"signed": True, "signer": signer_id, "trusted": True, "tampered": False,
                "detail": f"good signature from trusted publisher {signer_id!r}"}
    tail = (proc.stderr or proc.stdout or b"").decode(errors="replace").strip()
    tampered = "signature verification failed" in tail.lower()
    return {"signed": True, "signer": signer_id, "trusted": False, "tampered": tampered,
            "detail": f"signature present but NOT verified as trusted ({tail or 'no matching trusted key'})"}


def add_trusted_publisher(identity: str, public_key_path: "str | Path",
                           trusted_publishers_file: "Path | None" = None) -> None:
    """Append (or replace, if ``identity`` already has an entry) a publisher
    to the local trusted-publisher keyring, in ``ssh-keygen -Y verify``'s
    own allowed-signers line format. This is the explicit, locally-owned
    trust decision P1-06 asks for - nothing is auto-trusted from a pack
    itself; a publisher's public key has to be added here deliberately,
    out of band from installing any specific pack."""
    pubkey_path = Path(public_key_path)
    if not pubkey_path.is_file():
        raise FileNotFoundError(f"public key not found: {pubkey_path}")
    key_line = pubkey_path.read_text(encoding="utf-8").strip()
    if not key_line or len(key_line.split()) < 2:
        raise ValueError(f"{pubkey_path} does not look like an SSH public key "
                         f"(expected '<type> <base64-key> [comment]')")
    key_type, key_b64 = key_line.split()[0], key_line.split()[1]
    signers_file = Path(trusted_publishers_file or TRUSTED_PUBLISHERS_FILE)
    signers_file.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = []
    if signers_file.is_file():
        existing_lines = [ln for ln in signers_file.read_text(encoding="utf-8").splitlines()
                          if ln.strip() and not ln.split()[0] == identity]
    existing_lines.append(f"{identity} {key_type} {key_b64}")
    signers_file.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")


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


def load_pack(source: str, allow_insecure: bool = False, allow_unsigned: bool = False) -> dict:
    """Load a pack from a local path or an http(s) URL, validating its shape and
    that its declared hash matches its contents (tamper/corruption check).

    P1-07: a pack is executable-ish content (its cases drive host-side
    workspace preparation and agent prompts), fetched over the network from
    a URL the user typed - plain `http://` has no confidentiality or
    integrity against an on-path attacker, who could swap in a malicious
    pack en route. The content hash check below catches accidental
    corruption but is self-declared by the pack itself, so a swapped pack
    just carries a matching hash for its own (malicious) content - it is not
    a substitute for transport security. `https://` is required unless the
    caller explicitly passes `allow_insecure=True` (CLI: `--allow-insecure`),
    for local/offline testing against a plain-http fixture server.

    P1-06: HTTPS authenticates the TRANSPORT endpoint, not the pack's
    AUTHOR - a compromised or malicious host serving over valid HTTPS can
    still serve a hostile pack with a self-consistent hash. A REMOTE
    (http/https) pack that isn't signed by a publisher in the local
    trusted-publisher keyring (see `verify_pack_signature`) is refused
    unless the caller explicitly passes `allow_unsigned=True` (CLI:
    `--allow-unsigned`) - a LOCAL file path is never gated on this: a pack
    already sitting on this machine's filesystem needed no network trust
    decision to get there.
    """
    is_remote = bool(re.match(r"^https?://", source))
    if is_remote:
        if source.startswith("http://") and not allow_insecure:
            raise ValueError(
                f"refusing to install a pack over plain http: {source!r} - "
                "use an https:// URL, or pass --allow-insecure if you "
                "understand the risk (e.g. a local test server)"
            )
        with urllib.request.urlopen(source, timeout=30) as resp:  # noqa: S310 - user-supplied URL, documented
            raw_bytes = resp.read(_MAX_PACK_BYTES + 1)
            if len(raw_bytes) > _MAX_PACK_BYTES:
                raise ValueError(
                    f"pack at {source} exceeded {_MAX_PACK_BYTES} bytes - refusing to "
                    f"buffer further"
                )
            raw = raw_bytes.decode("utf-8")
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
    # F-11: `cases` itself, and every case inside it, were previously taken
    # on faith from an external source (a URL or a handed-around file) and
    # written straight to disk unvalidated - `build_pack()` validates each
    # case when CREATING a pack, but nothing re-checked one being INSTALLED,
    # so a malformed/hand-edited pack only surfaced later, at `run --pack`
    # time, against a registry entry that was already (partially) written.
    cases = pack["cases"]
    if not isinstance(cases, dict) or not cases:
        raise ValueError("pack 'cases' must be a non-empty object of {filename: case}")
    for fname, data in cases.items():
        if not isinstance(fname, str) or not fname:
            raise ValueError(f"pack has an invalid case filename: {fname!r}")
        validate_case(data, source=f"{source}::{fname}")
    validate_unique_case_names(list(cases.values()), source=source)
    declared_count = pack.get("case_count")
    if declared_count is not None and declared_count != len(cases):
        raise ValueError(
            f"pack declares case_count={declared_count} but has {len(cases)} case(s) - "
            f"refusing to install a pack whose own metadata is internally inconsistent"
        )
    # F-11: two different case filenames that sanitize to the SAME on-disk
    # name (see install_pack's use of _safe()) would otherwise silently
    # overwrite each other during install, losing a case with no warning.
    seen_safe: dict[str, str] = {}
    for fname in cases:
        safe_name = _safe(fname)
        if safe_name in seen_safe:
            raise ValueError(
                f"pack has a filename collision after sanitization: "
                f"{seen_safe[safe_name]!r} and {fname!r} both become {safe_name!r}"
            )
        seen_safe[safe_name] = fname
    actual = content_hash(pack["cases"])
    if pack.get("hash") and pack["hash"] != actual:
        raise ValueError(f"pack hash mismatch (declared {pack['hash']}, computed {actual}) "
                         "- refusing to install a tampered/corrupt pack")
    pack["hash"] = actual
    # P1-06: verified AFTER `hash` is recomputed above (not the pack's own
    # possibly-stale/tampered declared value) - the signature covers
    # `_signable_blob`, which reads `pack["hash"]` at call time, so this
    # ordering is what makes the signature actually bind to the REAL
    # content, not whatever hash the pack merely claims for itself.
    pack["verification"] = verify_pack_signature(pack)
    if pack["verification"]["tampered"]:
        # P1-06: a known signer identity whose signature does NOT verify is
        # never a legitimate "not yet trusted" state - it means the content
        # changed after signing (or something is impersonating a known
        # identity). Refused unconditionally, including a local file - no
        # flag bypasses this, unlike the softer "unknown signer" gate below.
        raise ValueError(
            f"refusing to install a pack with a BROKEN signature: {pack['verification']['detail']} "
            f"- this pack's content does not match what {pack['verification']['signer']!r} actually "
            f"signed (tampered, corrupted, or an impersonation attempt)"
        )
    if is_remote and not pack["verification"]["trusted"] and not allow_unsigned:
        raise ValueError(
            f"refusing to install an unsigned/untrusted remote pack: "
            f"{pack['verification']['detail']} - pass --allow-unsigned if you understand "
            f"the risk, or install from a publisher already in your trusted keyring "
            f"(see `optarena cases trust-publisher`)"
        )
    return pack


def install_pack(pack: dict, packs_dir: Path | None = None, force: bool = False) -> Path:
    """Extract a loaded pack into ``<packs_dir>/<name>@<version>/`` (one case
    file each) plus a ``_pack.json`` manifest. Refuses to overwrite a different
    build of the same name@version (hash mismatch) unless *force*.

    F-11: builds the whole install in a temporary SIBLING directory first,
    then atomically renames it into place - the previous version deleted old
    case files and wrote new ones in two separate loops directly on `root`,
    so a crash between (or during) them could leave a mixture of the old and
    new pack permanently in the registry, silently corrupting it.
    """
    root = (packs_dir or PACKS_DIR) / f"{_safe(pack['name'])}@{_safe(pack['version'])}"
    if root.exists() and not force:
        existing = root / "_pack.json"
        if existing.exists():
            try:
                prev = json.loads(existing.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                prev = {}
            if prev.get("hash") == pack["hash"]:
                return root  # already installed, identical - idempotent
            raise FileExistsError(
                f"{pack['name']}@{pack['version']} already installed with a DIFFERENT hash "
                f"({prev.get('hash')} vs {pack['hash']}); pass force=True to overwrite")

    parent = root.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{root.name}.staging-{uuid.uuid4().hex[:8]}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        for fname, data in pack["cases"].items():
            (staging / _safe(fname)).write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        manifest = {k: v for k, v in pack.items() if k != "cases"}
        (staging / "_pack.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    staging.replace(root)
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


def verify_installed_pack(cases_dir: "str | Path") -> "dict | None":
    """
    P1-10: re-derive a pack's trust state from what's ACTUALLY on disk in
    ``cases_dir`` right now, rather than trusting ``_pack.json``'s
    install-time snapshot forever. ``install_pack`` writes
    ``pack["verification"]`` (computed once, in memory, against the
    pre-install content) verbatim into ``_pack.json`` - nothing previously
    re-checked it, so editing an installed case file after installation
    left every subsequent run still reporting the pack as signed and
    trusted, silently. Called at manifest-build time (every run that
    resolves cases through an installed pack), not just once at install.

    Returns ``None`` (not an error) when ``cases_dir`` isn't an installed
    pack at all (no ``_pack.json``) - same convention the caller already
    used before this existed. When it IS a pack, the on-disk case files are
    re-hashed with the exact same ``content_hash`` install-time used; if it
    no longer matches the hash recorded in ``_pack.json``, the returned
    ``verification`` is forced to ``trusted: False, tampered: True``
    regardless of what the stored snapshot said - a modified pack must
    never keep reporting itself as still-trusted. Unchanged content skips
    re-running signature verification entirely (redundant work with no
    security benefit - the stored verification was already computed
    against this exact content).
    """
    cases_dir = Path(cases_dir)
    manifest_path = cases_dir / "_pack.json"
    if not manifest_path.is_file():
        return None
    try:
        man = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    verification = man.get("verification") or {}
    try:
        cases: dict[str, dict] = {}
        for p in sorted(cases_dir.glob("*.json")):
            if p.name == "_pack.json":
                continue
            cases[p.name] = json.loads(p.read_text(encoding="utf-8"))
        actual_hash = content_hash(cases)
    except (OSError, ValueError) as exc:
        verification = {
            "signed": bool(verification.get("signed")), "signer": verification.get("signer"),
            "trusted": False, "tampered": True,
            "detail": f"could not re-read the installed case files to verify their content: {exc}"}
        return {"name": man.get("name"), "version": man.get("version"),
                "hash": man.get("hash"), "verification": verification}
    stored_hash = man.get("hash")
    if actual_hash != stored_hash:
        verification = {
            "signed": bool(verification.get("signed")), "signer": verification.get("signer"),
            "trusted": False, "tampered": True,
            "detail": f"installed content hash ({actual_hash}) no longer matches the hash "
                      f"recorded at install time ({stored_hash}) - case files were modified "
                      f"after installation"}
    return {"name": man.get("name"), "version": man.get("version"),
            "hash": man.get("hash"), "verification": verification}


def resolve_pack(ref: str, packs_dir: Path | None = None) -> Path:
    """Resolve ``name`` or ``name@version`` to an installed pack directory for
    `run --pack`. Bare name picks the highest installed version."""
    root = packs_dir or PACKS_DIR
    if "@" in ref:
        # A-19: was preceded by a dead `d = root / _safe(ref.replace("@", "@"))`
        # (a no-op replace, immediately overwritten below).
        name, version = ref.split("@", 1)
        d = root / f"{_safe(name)}@{_safe(version)}"
        if d.is_dir():
            return d
        raise FileNotFoundError(f"pack not installed: {ref} (see `optarena cases packs`)")
    candidates = [m for m in list_installed(packs_dir) if m.get("name") == ref]
    if not candidates:
        raise FileNotFoundError(f"pack not installed: {ref} (see `optarena cases packs`)")
    # F-11: numeric semver compare, not string compare - "1.10.0" must sort
    # above "1.9.0", which a plain string compare gets backwards (lexical '9'
    # > '1'). Falls back to newest install when versions tie or are absent.
    # (A-20: this comment used to state the requirement inverted.)
    candidates.sort(key=lambda m: (_version_key(str(m.get("version", ""))), m.get("created_at", "")), reverse=True)
    return Path(candidates[0]["path"])
