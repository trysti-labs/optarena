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
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_DIR = REPO_ROOT / "docker" / "vuln-baseline"


def _findings(trivy_json: dict) -> set[tuple[str, str]]:
    """(CVE ID, package name) pairs, deduplicated across scan targets - the
    same CVE+package can legitimately appear in multiple vendored copies
    (e.g. two .deps.json files bundling the same vulnerable library), which
    is a detail of WHERE it was found, not a distinct vulnerability to
    triage separately."""
    out: set[tuple[str, str]] = set()
    for res in trivy_json.get("Results") or []:
        for v in res.get("Vulnerabilities") or []:
            out.add((v["VulnerabilityID"], v["PkgName"]))
    return out


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

    accepted = {(e["id"], e["package"]) for e in baseline.get("accepted_vulnerabilities", [])}
    found = _findings(trivy_json)
    new = sorted(found - accepted)
    for cve, pkg in new:
        problems.append(f"{image}: NEW finding not in baseline: {cve} ({pkg})")
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
