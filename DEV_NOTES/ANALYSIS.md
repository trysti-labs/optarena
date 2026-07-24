# OptArena Code Analysis

_Date: 2026-07-02. Scope: full review of the standalone OptArena repo (Python package,
ui-harness, dashboard, docs) against ARCH.md and README.md after the split from SelfOpt._

## 1. Summary

The codebase is small (~2,300 lines), coherent, and matches its architecture document
closely. The domain model (Case / Backend / Scenario / Driver / CaseResult / RunRecord /
Comparison) is implemented exactly as documented, the driver registry and lifecycle
contract are honored by all five drivers, and the Python and JS filesystem oracles agree
on semantics. The separation from SelfOpt is complete: the package has no import,
path, or config dependency on SelfOpt; remaining mentions are illustrative (SelfOpt as
one example backend in scenario files and prose), which is intentional per ARCH §1.

Issues found: 1 blocking, 5 minor. All were fixed in this pass (section 3). Remaining
observations that were deliberately not changed are in section 4.

## 2. Doc-vs-code verification

| ARCH.md claim | Verified |
|---|---|
| Repo layout (§2) | Matches. All listed files exist; nothing extra outside `legacy/` and gitignored runtime dirs. |
| Zero-dependency Python core (§1.4) | Confirmed: only stdlib imports in `optarena/`; `crewai` is lazy + optional extra. |
| Driver registry, lazy imports (§6.1) | Confirmed in `drivers/__init__.py`; all 7 names resolve. |
| Driver lifecycle: UI driver runs all cases in `prepare()` (§4) | Confirmed in `vscode_ui.py`. |
| `run_case()` never raises for tool failure (§4) | Confirmed: all drivers catch and set `error`. |
| `ELECTRON_RUN_AS_NODE` / `VSCODE_*` scrub on both sides (§8.1) | Confirmed in `vscode_ui.py`, `aider_cli.py`, and `wdio.conf.js`. |
| Python <-> Node env/JSONL protocol (§6.3) | Env names and JSONL record fields match on both sides. |
| Filesystem oracle parity Python/JS (§3.1) | Same signature (`size:mtime`), same new-or-modified diff, same glob + case-insensitive substring checks. Ignore lists now aligned (see 3.6). |
| Exit code 0 iff no failed cases (§4) | Confirmed in `cli.cmd_run`. |
| `index.json` rebuilt on each save (§7.1) | Confirmed in `store.save_run`. |
| Dashboard: static, same-origin serve, client-side compare (§7.2) | Confirmed; `optarena serve` binds 127.0.0.1 and roots at the repo. |
| mkstemp fd closed before unlink (§8.3) | Confirmed in `vscode_ui.prepare`. |
| ASCII-safe console output (§8.3) | Was violated in two places; fixed (see 3.4). |

README quick-start commands (`list drivers`, `list cases`, `run`, `compare`, `serve`)
all parse and execute; the six shipped cases load and match the catalogue table.

## 3. Issues found and fixed

1. **`ui-harness/package.json` test script pointed at a missing file** (blocking).
   `"test": "wdio run ./wdio.conf.ts"` but the config is `wdio.conf.js` (the `.ts`
   name survived the move from the old in-tree harness). The Python `cline-ui` driver
   invokes `npm test`, so every UI run would fail in a fresh checkout.
   Fixed to `./wdio.conf.js`; package renamed `cline-ui-tests` -> `optarena-ui-harness`
   and description made backend-agnostic.

2. **`Backend.openai_base` was dead code and drivers hardcoded `/v1`** (bug). Every
   OpenAI-protocol consumer (`openai_chat.py`, `aider_cli.py`, `crewai_sdk.py`)
   appended `/v1` to `base_url` unconditionally, producing `/v1/v1/...` for
   `kind: openai` backends whose base URL already contains `/v1` (the property's
   documented convention). `openai_base` now appends `/v1` only when missing, and all
   three drivers use it.

3. **`cli.py` docstring advertised an unimplemented `--ab "driverA:driverB"` flag.**
   No such argument exists in the parser. Docstring corrected (two `--scenario`
   flags are the supported A/B form).

4. **Non-ASCII console output** violated ARCH §8.3 (cp1252 consoles): `->` arrows in
   `compare.format_table` were `→`, and `vscode_ui` printed a `…` ellipsis.
   Both can raise `UnicodeEncodeError` when stdout is redirected on Windows. Fixed to
   ASCII.

5. **UI-harness subprocess timeout was a flat 30 minutes** regardless of workload.
   A scenario with all six cases at a 180 s budget plus VS Code startup can exceed it,
   killing a healthy run. Now scaled: `max(30 min, n_cases * (case_timeout + 120) + 600)`.

6. **Oracle ignore-list drift**: the JS oracle did not ignore `__pycache__` or
   `.aider` (the Python one does). A tool running Python in the workspace would have
   its cache files reported as "created". Aligned.

Also fixed: stale references from the pre-split layout (`tests/tools/cline-ui` comment
in `paths.js`, `arena compare` in `store.py`, "persist via store" claim in `runner.py`
whose persistence actually lives in the CLI), removal of the legacy `SELFOPT_URL` env
alias, and backend-agnostic wording in `wdio.conf.js` / ui-harness README.

## 4. Observations (not changed)

- **`summary.failed` includes errored cases** (`failed = cases - passed`). ARCH
  distinguishes oracle failures from infrastructure errors, and the summary does carry
  a separate `errors` count, but a reader may expect `failed + errors + passed = cases`.
  Consumers (dashboard, compare) only use `passed`/`pass_rate`, so this is cosmetic;
  changing it would alter the on-disk summary schema of existing runs.
- **`faster` verdict has no `tie`**: `more_accurate` reports `"tie"` on equal pass
  rates, but equal mean durations report run A as faster. Durations are floats, so
  exact ties are unlikely; noted for symmetry.
- **aider driver timeout is per prompt, not per case**: a multi-prompt case can take
  `n_prompts * timeout`. The UI harness has the same shape (INTER_PROMPT_WAIT budget per
  intermediate prompt) and documents it; acceptable, but worth knowing when comparing
  wall times across drivers.
- **Case descriptions say "Cline creates ..."** for tool-neutral cases (a relic of the
  original Cline-only harness). Purely descriptive text; renaming would churn the case
  files' history for no functional gain, but new cases should use tool-neutral wording.
- **`ts-node` / `typescript` devDependencies** in ui-harness are unused now that the
  config is plain JS. Removing them requires an `npm install` to regenerate the
  lockfile; left for the next dependency update.
- **`legacy/`** is reference-only and was not reviewed to the same depth; it still
  contains SelfOpt-era wording, which is accurate for what it is (the retired
  first-generation harness).

## 5. Concurrency pass (added July 2)

The runner is strictly sequential, so in-process races do not apply. The one
real exposure was `optarena serve` serving `results/` while a concurrent
`optarena run` (another process) wrote `index.json`, run files, or
comparisons non-atomically - the dashboard could fetch a partially written
JSON file. All results-store writes now go through an atomic tmp + replace
helper (`store._write_atomic`), used by `save_run`, `rebuild_index`, and
`save_comparison`.

## 6. Test/verification performed

- `python -m compileall optarena` clean.
- `optarena list drivers`, `optarena list cases` produce the documented output.
- `Backend.openai_base` unit-checked for both kinds and for base URLs with and
  without a trailing `/v1`.
- Existing saved runs in `results/` still load through `store.load_run` and the
  dashboard index schema is unchanged.
