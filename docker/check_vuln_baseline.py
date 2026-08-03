#!/usr/bin/env python3
"""
P1-03: compare a fresh Trivy JSON scan against this image's checked-in
vulnerability baseline (docker/vuln-baseline/<image>.json) and fail on
anything the baseline doesn't already know about.

Why a baseline instead of `continue-on-error: true` (the previous state) or
a flat `.trivyignore`: the first real scan of these 9 images (2026-08-02)
found a genuine, non-trivial CRITICAL/HIGH backlog - real findings, not a
scanner or wiring problem (confirmed by running Trivy directly against
locally-built images, and a remediation pass that fixed 45% of it: base
images bumped, apt-get upgrade added everywhere, an unused ImageMagick
suite removed from the Rust image - see docker/vuln-baseline/*.json's own
`note` field for details). Shipping hard-blocking against the FULL
remaining backlog on day one would stop every PR on findings nobody has
individually triaged, which is the mistake this whole mechanism exists to
avoid; a flat `.trivyignore` only supports per-CVE-ID entries, and this
backlog is dominated by Linux-kernel-header and Debian-perl-tooling noise
(707 of 1367 remaining findings) that will keep growing indefinitely as
new kernel CVEs get published - individually enumerating them would be
both impractical today and guaranteed to silently miss new ones tomorrow.

A per-image baseline of exactly what's already been seen and accepted, with
an explicit owner and expiry, means: a genuinely NEW CRITICAL/HIGH finding
(a newly introduced dependency, a freshly disclosed CVE in something
already accepted-as-"no fix available" that now HAS a fix) fails CI
immediately - exactly what a blocking scan is for - while the pre-existing,
documented backlog does not block every unrelated PR. `expires_at` passing
also fails CI, forcing periodic re-triage instead of silent indefinite
acceptance.

P1-02 (2026-08-03): the original matching identity was just (CVE ID,
package name) - it didn't bind to the installed version, so an accepted
finding stayed "accepted" forever even after the installed package changed
underneath it, and it never noticed when a previously "no fix available"
finding gained a real fix (Trivy's DB gets updated with new fix info
independent of anything in this repo changing). The identity below is now
(target, package type, package, installed version, CVE ID) - bumping the
installed package version, or the same CVE resurfacing against a
DIFFERENT version, both correctly read as a new, unreviewed finding. Two
narrower signals a version-only key still can't catch are checked
separately: a fix becoming available for an unchanged installed version,
and Trivy re-classifying an accepted finding's severity upward (its CVSS
database gets revised independent of this repo too) - both fail the gate
even though the (target, type, package, version, CVE) key still matches.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_DIR = REPO_ROOT / "docker" / "vuln-baseline"

_SEVERITY_ORDER = {"UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}

#: A baseline entry without these can't be matched against a real finding at
#: all - reject it outright rather than silently treating it as inert.
_REQUIRED_ENTRY_FIELDS = {"id", "package", "package_type", "installed_version", "target", "severity"}

_FindingKey = tuple[str, str, str, str, str]  # (target, package_type, package, installed_version, cve_id)


def _normalize_target(target: str) -> str:
    """Trivy formats an OS-package Target as "<image-ref> (<os> <version>)"
    - the image-ref prefix is whatever tag/registry THIS scan happened to
    use (a git-sha tag in publish-images.yml, a bare local tag in ci.yml's
    PR-time scan, ...), so keeping it in the match key would make the same
    underlying finding fail to match across two legitimate scans of the
    same content. Keep only the "(os version)" suffix for that shape.
    Language-lockfile targets (a bare file path, no trailing parens) pass
    through unchanged - they don't have this problem."""
    m = re.match(r"^.*\(([^()]+)\)\s*$", target)
    return m.group(1) if m else target


def _pkg_type(purl: "str | None") -> str:
    """The ecosystem segment of a package URL - "pkg:deb/debian/bsdutils@..."
    -> "deb", "pkg:gem/rack@..." -> "gem". Distinguishes same-named packages
    across ecosystems (P1-02's "package type" ask) rather than assuming a
    bare package name is unambiguous."""
    if not purl or not purl.startswith("pkg:"):
        return "unknown"
    return purl[4:].split("/", 1)[0]


def _findings(trivy_json: dict) -> dict[_FindingKey, dict]:
    """Match key -> {fixed_version, severity, status}, deduplicated across
    scan results the same way as before - the same CVE+package+version can
    legitimately appear in multiple vendored copies (e.g. two .deps.json
    files bundling the same vulnerable library), which is a detail of WHERE
    it was found, not a distinct vulnerability to triage separately."""
    out: dict[_FindingKey, dict] = {}
    for res in trivy_json.get("Results") or []:
        target = _normalize_target(res.get("Target", ""))
        for v in res.get("Vulnerabilities") or []:
            pkg_type = _pkg_type((v.get("PkgIdentifier") or {}).get("PURL"))
            key = (target, pkg_type, v["PkgName"], v.get("InstalledVersion", ""), v["VulnerabilityID"])
            out[key] = {
                "fixed_version": v.get("FixedVersion", "") or "",
                "severity": (v.get("Severity", "") or "").upper(),
                "status": v.get("Status", "") or "",
            }
    return out


def _load_accepted(baseline: dict, image: str) -> "tuple[dict[_FindingKey, dict], list[str]]":
    """Parse accepted_vulnerabilities into {match key: entry}, reporting a
    problem (not raising) for any malformed or duplicate/conflicting entry -
    a baseline file is committed content, not a trusted internal data
    structure, so a hand-edit mistake should fail loudly here rather than
    silently matching nothing (or the wrong thing) at check time."""
    accepted: dict[_FindingKey, dict] = {}
    problems: list[str] = []
    for e in baseline.get("accepted_vulnerabilities", []):
        missing = _REQUIRED_ENTRY_FIELDS - e.keys()
        if missing:
            problems.append(
                f"{image}: malformed baseline entry (missing {sorted(missing)}): {e}")
            continue
        key = (e["target"], e["package_type"], e["package"], e["installed_version"], e["id"])
        if key in accepted:
            problems.append(
                f"{image}: duplicate/conflicting baseline entry for "
                f"{e['id']} ({e['package']} {e['installed_version']} @ {e['target']})")
            continue
        accepted[key] = e
    return accepted, problems


def check(image: str, trivy_json: dict, today: "datetime.date | None" = None) -> list[str]:
    """Returns a list of problems (empty = clean). Never raises on a missing
    baseline file - that's itself a problem to report, not a crash."""
    today = today or datetime.date.today()
    baseline_path = BASELINE_DIR / f"{image}.json"
    if not baseline_path.is_file():
        return [f"no baseline file for {image!r} at {baseline_path} - "
                 f"every scanned image needs one (see check_vuln_baseline.py)"]
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    problems: list[str] = []
    expires_at = baseline.get("expires_at")
    if expires_at:
        try:
            if datetime.date.fromisoformat(expires_at) < today:
                problems.append(
                    f"{image}: baseline expired {expires_at} - re-triage the accepted "
                    f"findings and refresh docker/vuln-baseline/{image}.json "
                    f"(owner: {baseline.get('owner', 'unknown')})"
                )
        except ValueError:
            problems.append(f"{image}: baseline expires_at {expires_at!r} is not a valid date")

    accepted, malformed = _load_accepted(baseline, image)
    problems.extend(malformed)

    found = _findings(trivy_json)
    for key, detail in sorted(found.items()):
        target, pkg_type, pkg, installed, cve = key
        entry = accepted.get(key)
        if entry is None:
            # P1-02: this also naturally covers "installed version changed"
            # and "same CVE against a different version" - both simply
            # don't match any accepted key anymore, exactly like a
            # brand-new finding, without needing separate logic for it.
            problems.append(
                f"{image}: NEW finding not in baseline: {cve} ({pkg} {installed} @ {target})")
            continue
        # P1-02: a fix becoming available for a previously-unfixable (or
        # differently-fixed) accepted finding, independent of anything in
        # this repo changing - Trivy's own DB gets revised.
        if detail["fixed_version"] and detail["fixed_version"] != (entry.get("fixed_version") or ""):
            problems.append(
                f"{image}: {cve} ({pkg} {installed}) gained fixed_version "
                f"{detail['fixed_version']!r} (baseline recorded "
                f"{entry.get('fixed_version') or 'none'!r}) - re-triage and refresh the baseline")
        # P1-02: severity re-classified upward since acceptance - same reason.
        old_sev = _SEVERITY_ORDER.get((entry.get("severity") or "").upper(), -1)
        new_sev = _SEVERITY_ORDER.get(detail["severity"], -1)
        if new_sev > old_sev:
            problems.append(
                f"{image}: {cve} ({pkg} {installed}) severity increased "
                f"{entry.get('severity')} -> {detail['severity']} - re-triage and refresh the baseline")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="baseline key, e.g. optarena-tester-go")
    parser.add_argument("trivy_json", type=Path, help="path to a Trivy --format json report")
    args = parser.parse_args()

    trivy_json = json.loads(args.trivy_json.read_text(encoding="utf-8"))
    problems = check(args.image, trivy_json)
    if problems:
        print(f"{args.image}: vulnerability baseline check FAILED ({len(problems)} problem(s)):",
              file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"{args.image}: OK, no new CRITICAL/HIGH findings outside the accepted baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
