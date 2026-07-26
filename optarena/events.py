"""
optarena/events.py
───────────────────
F-18: a run previously had no output an automation/CI wrapper could consume
short of scraping human-formatted print() text - no log level, no quiet mode,
no structured event stream. `RunEvents` is a thin sink threaded through
runner.py: `.say()`/`.detail()` for the existing human console lines
(suppressed under --quiet; `.detail()` lines also drop out at --log-level
warn/error), `.emit()` for structured lifecycle events (`run_started`,
`case_started`, `case_completed`, `checkpoint_saved`, `run_completed`; one
JSON object per line on stdout, opt-in via --json-events). Human output
stays the default - a caller that never constructs a RunEvents (or passes
None) gets exactly the old always-print behavior.
"""

from __future__ import annotations

import json
import time

LOG_LEVELS = ("debug", "info", "warn", "error", "quiet")


class RunEvents:
    """Per-run output sink. `quiet` and `json_events` are independent: a run
    can print human text, JSON lines, both (an operator watching a
    machine-consumed run), or neither."""

    def __init__(self, *, quiet: bool = False, json_events: bool = False,
                 log_level: str = "info") -> None:
        if log_level not in LOG_LEVELS:
            raise ValueError(f"invalid log level {log_level!r} (expected one of {LOG_LEVELS})")
        # `quiet=True` and `--log-level quiet` are the same thing; keeping
        # --quiet as its own flag matches the common CLI convention (git,
        # curl, ...) of a shorthand for the maximum-silence level.
        self.quiet = quiet or log_level == "quiet"
        self.json_events = json_events
        # debug/info show every per-case detail line (diff, off-target
        # edits, security findings, oracle description, ...); warn/error
        # keep only the PASS/FAIL/ERROR headline and (via .say(), not
        # .detail()) failure attribution - the two lines that matter for a
        # CI log, without the rest of the noise a large run produces.
        self.verbose = log_level in ("debug", "info")

    def say(self, *args, **kwargs) -> None:
        """The existing human console line, suppressed under --quiet."""
        if not self.quiet:
            print(*args, **kwargs)

    def detail(self, *args, **kwargs) -> None:
        """A secondary per-case line, additionally dropped at --log-level warn/error."""
        if self.verbose:
            self.say(*args, **kwargs)

    def emit(self, event: str, **fields) -> None:
        """One structured lifecycle event, emitted only under --json-events.
        `default=str` so an unexpected non-JSON-native field (e.g. a Path)
        degrades to its string form instead of raising mid-run."""
        if self.json_events:
            print(json.dumps({"event": event, "ts": time.time(), **fields}, default=str), flush=True)
