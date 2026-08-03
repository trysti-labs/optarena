#!/usr/bin/env python3
"""
Used by .github/workflows/integration-smoke.yml: exits non-zero only if the
given saved run recorded an infrastructure_error (F-10 - the ENVIRONMENT,
not the model/tool, is why a case failed: container engine down, exec
failed, sandbox setup broke). A case that simply failed the oracle because
the model got the task wrong is NOT what this checks for - that's expected
and unremarkable for a small local smoke-test model.

Kept as its own script rather than inlined into the workflow YAML: an
embedded multi-line `python -c "..."` inside a YAML block scalar is
fragile (indentation rules, nested quoting) and not independently testable.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_no_infra_errors.py <run-record.json>", file=sys.stderr)
        return 2
    data = json.loads(open(sys.argv[1], encoding="utf-8").read())
    infra = (data.get("summary") or {}).get("infrastructure_errors")
    if infra:
        print(f"::error::{infra} infrastructure_error(s) - the environment broke "
              f"this run, not just the model", file=sys.stderr)
        return 1
    print("integration OK (0 infrastructure_errors) - model correctness is not "
          "what this check verifies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
