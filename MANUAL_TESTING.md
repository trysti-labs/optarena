# OptArena - Manual Testing Guide

This document covers every scenario that should be tested by hand. The unit
suite (`tests/test_optarena.py`, `python -m unittest discover tests`)
verifies internal contracts in isolation; this guide covers observable
behavior through the real CLI, real Docker sandbox, real backends, and real
tool UIs.

Sections are ordered from the most foundational (install, catalogue) to the
most integrated (VS Code UI agents, remote comparisons). Every test includes
**setup**, the **action** to perform, and the **expected result**.

---

## Prerequisites

Before running any test, establish a working baseline:

- Python >= 3.10, installed with `pip install -e .` from the repo root (provides the `optarena` command, stdlib-only - no dependencies to install)
- Ollama running locally (`ollama serve`) with at least one small model pulled (e.g. `ollama pull gemma3:1b`) for the free/fast tests, and optionally a stronger model (e.g. `ollama pull qwen3-coder:30b`) for tests that expect a real pass
- Docker Desktop installed and running, for the sandboxed check_command tests (section 4) - most tests fall back to the host without it, but the Docker-specific tests need it
- For VS Code UI driver tests (section 12): Node >= 18, `cd ui-harness && npm install`, and the relevant extension installed in `~/.vscode/extensions` (Cline, Roo Code, Continue, or Kilo Code)
- For CLI agent driver tests (section 11): the relevant tool installed and on PATH (`aider`, `claude`, `codex`, `opencode`, `goose`, `qwen`) - `optarena doctor` reports which are actually available
- For cost-tracking tests against a real paid backend (section 8): an API key for a provider in `optarena/pricing.py`'s table (OpenAI, Anthropic, etc.) - optional, the free/local path is fully testable without one

All commands assume you're in the `optarena/` repo root. `python -m optarena` and `optarena` (the installed console script) are interchangeable throughout.

---

## 1. Installation and Catalogue

### 1.1 Install and verify the CLI is on PATH

**Action:**
```bash
pip install -e .
optarena --help
```

**Expected:**
- No dependency errors during install (stdlib-only core)
- `--help` prints the subcommand list: `run, compare, regression, list, serve, doctor, init, docker`

---

### 1.2 List the driver registry

**Action:**
```bash
optarena list drivers
```

**Expected:**
- A table with columns `driver | kind | backend | status | summary`
- Exactly 13 rows: `openai-chat`, `ollama-chat`, `aider`, `cline-ui`, `roo-ui`, `continue-ui`, `kilo-ui`, `claude-code`, `codex`, `opencode`, `goose`, `qwen-code`, `crewai`
- `kind` is one of `baseline | ui | cli | sdk`
- `backend` is `scenario` for most, `fixed` for exactly `claude-code` and `codex` (they use their own logged-in account, not the scenario's backend URL)
- `status` is `stable` for `openai-chat`, `ollama-chat`, `aider`, `cline-ui`; `experimental` for the rest; `optional` for `crewai`

---

### 1.3 List the built-in case catalogue

**Action:**
```bash
optarena list cases
```

**Expected:**
- 7 rows: `create_factorial` (c), `create_fibonacci` (python), `create_hello_world` (python), `create_reverse_string_js` (javascript), `create_server_c` (c), `modify_add_type_hints` (python), `multi_prompt_session` (python)
- Each row shows `name`, `language`, `description`

---

### 1.4 Filter the catalogue by language

**Action:**
```bash
optarena list cases --language python
```

**Expected:**
- Only the 4 Python-tagged cases print (`create_fibonacci`, `create_hello_world`, `modify_add_type_hints`, `multi_prompt_session`)
- `optarena list cases --language javascript` shows only `create_reverse_string_js`
- `optarena list cases --language ruby` (a language nothing is tagged with) prints nothing and exits 0

---

### 1.5 List saved runs (empty state)

**Setup:** `results/runs/` is empty or doesn't exist yet.

**Action:**
```bash
optarena list runs
```

**Expected:**
- No error, no output (or an empty table) - doesn't crash on a fresh checkout

---

## 2. Preflight (`optarena doctor`)

### 2.1 Doctor on a fresh machine

**Action:**
```bash
optarena doctor
```

**Expected:**
- Sections print in order: `backend:`, `cli drivers:`, `docker (sandboxed check_command execution):`, `ui drivers:`, and (on Windows) `environment:`
- Each line is `[ok ]` or `[MISS]` with a fix hint on miss (e.g. `[MISS] aider - pip install aider-chat`)
- The Docker section is **advisory only**: `[MISS] docker daemon reachable` does not fail the overall exit code
- Final line: `doctor result: all good` or `doctor result: some checks failed (see MISS lines)`
- Exit code is `0` only if every **non-Docker** check passed

---

### 2.2 Doctor detects a stuck VS Code updater (Windows)

**Setup:** A `CodeSetup-stable-*.exe` process is running (e.g. an interrupted VS Code auto-update).

**Action:**
```bash
optarena doctor
```

**Expected:**
- `[MISS] no stuck VS Code updater - kill CodeSetup*.exe - it blocks every VS Code launch (ARCH 8.2)`
- Killing the process and re-running doctor clears this line

---

### 2.3 Doctor against a custom backend

**Action:**
```bash
optarena doctor --base-url http://localhost:11434 --kind ollama
optarena doctor --base-url http://localhost:1234 --kind openai
```

**Expected:**
- `backend:` line reflects the URL/protocol requested
- A reachable Ollama at 11434 shows `[ok ]`; an unreachable port shows `[MISS]` with the exception type/message as the hint

---

## 3. Running Scenarios - Baseline Drivers

These are the fastest, dependency-free tests - `ollama-chat`/`openai-chat` need only a local Ollama.

### 3.1 Single inline scenario, weak model (expect failures)

**Setup:** `ollama pull gemma3:1b` (a small, low-quality model - used deliberately to exercise real failure paths).

**Action:**
```bash
optarena run --driver ollama-chat --name weak-check --model gemma3:1b
```

**Expected:**
- Banner: `=== RUN <timestamp>_weak-check  driver=ollama-chat  backend=gemma3:1b@localhost:11434  cases=7 ===`
- A `[optarena] docker sandbox: ...` line if Docker is available (see section 4)
- Most or all cases show `FAIL`, each with a `[failure_class]` tag (`syntax_error`, `assertion_failure`, `runtime_error`, etc.) and a real captured-output tail - not a generic "wrong keyword" message
- Summary line: `-> N/7 passed (mean X.Xs) - saved <run_id>.json`
- Exit code is `1` (any failed case) - useful to confirm in a script: `optarena run ...; echo $?`

---

### 3.2 Single inline scenario, strong model (expect passes)

**Setup:** `ollama pull qwen3-coder:30b` (or another strong local coding model).

**Action:**
```bash
optarena run --driver ollama-chat --name strong-check --model qwen3-coder:30b
```

**Expected:**
- Most/all 7 cases `PASS`
- Each passing case shows `output: PASS` - the literal stdout of the hidden test script, not just an exit code, proving the test actually executed
- Exit code `0` if all 7 passed

---

### 3.3 OpenAI-compatible baseline

**Setup:** Any OpenAI-compatible endpoint (LM Studio, an OpenAI key, or a proxy like SelfOpt) reachable at some base URL.

**Action:**
```bash
optarena run --driver openai-chat --name openai-check --base-url http://localhost:1234 --model <model> --kind openai
```

**Expected:**
- Requests go to `<base-url>/v1/chat/completions`
- Same case/oracle behavior as `ollama-chat`

---

### 3.4 Scoped run via `--cases`

**Action:**
```bash
optarena run --driver ollama-chat --name scoped --model gemma3:1b --cases create_hello_world,create_fibonacci
```

**Expected:**
- Banner shows `cases=2`
- Only those two cases run, in the order listed in the catalogue (not necessarily the `--cases` order)

---

### 3.5 Scoped run via `--language`

**Action:**
```bash
optarena run --driver ollama-chat --name lang-scoped --model qwen3-coder:30b --language javascript
```

**Expected:**
- Banner shows `cases=1`
- Only `create_reverse_string_js` runs
- Combining `--language` with an unrelated `--cases` list that shares no cases with that language results in `cases=0` and `Nothing to run` is NOT printed (the scenario still executes with an empty case list) - confirm this doesn't crash

---

### 3.6 Unknown case name errors clearly

**Action:**
```bash
optarena run --driver ollama-chat --model gemma3:1b --cases does_not_exist
```

**Expected:**
- Raises/prints `Unknown case(s): does_not_exist` rather than silently running zero cases

---

### 3.7 No driver and no scenario - usage error

**Action:**
```bash
optarena run
```

**Expected:**
- `Nothing to run: pass --scenario file.json or --driver ...` printed to stderr
- Exit code `2`

---

## 4. The Docker Sandbox

### 4.1 Build the sandbox image

**Action:**
```bash
optarena docker build
```

**Expected:**
- Builds from `docker/Dockerfile` (Debian + gcc/build-essential + python3 + python-is-python3 + nodejs/npm)
- Final line: `built optarena-tester:latest - check_command now runs sandboxed for every case`
- `docker images` shows `optarena-tester:latest`

---

### 4.2 One container serves the entire run, not one per case

**Setup:** Image built (4.1). Docker running.

**Action:**
```bash
optarena run --driver ollama-chat --name sandbox-check --model gemma3:1b --trials 3
```

**Expected:**
- Exactly one line: `[optarena] docker sandbox: optarena-sandbox-<hex> (image optarena-tester:latest) - one container for this whole run`
- Every case's and every trial's detail line shows the **same** container id (e.g. `container optarena-sandbox-a1b2c3d4e5f6`) - grep the output for `container ` and confirm only one distinct id appears across all 7 cases x 3 trials (21 check_command invocations)
- After the run, `docker ps -a` shows **no** lingering `optarena-*` containers (the container is stopped/removed at the end, even if the run fails partway through - check by killing `optarena run` mid-flight with Ctrl+C)

---

### 4.3 Docker sandbox is skipped when no case needs it

**Setup:** A custom case pack (see 6.1) where no case defines `check_command`.

**Action:**
```bash
optarena run --driver ollama-chat --model gemma3:1b --cases-dir ./my-cases
```

**Expected:**
- No `[optarena] docker sandbox:` line appears - the sandbox is only started when at least one loaded case has a `check_command`

---

### 4.4 Fallback to host when Docker is unavailable

**Setup:** Stop Docker Desktop (or set `OPTARENA_NO_DOCKER=1`).

**Action:**
```bash
OPTARENA_NO_DOCKER=1 optarena run --driver ollama-chat --name no-docker --model gemma3:1b --cases create_hello_world
```

**Expected:**
- A one-time stderr warning: `[optarena] Docker not available - running check_command directly on the host...` (or, if Docker is simply disabled via the env var, no sandbox line and check_command still runs, just on the host)
- Each case's detail line shows `check_command via host (no Docker sandbox)` instead of `docker:...`
- The case still passes/fails correctly - the fallback is fully functional, not degraded

---

### 4.5 Docker image missing falls back cleanly

**Setup:** Docker running, but `optarena-tester:latest` not built (skip 4.1, or `docker rmi optarena-tester:latest`).

**Action:**
```bash
optarena run --driver ollama-chat --model gemma3:1b --cases create_hello_world
```

**Expected:**
- Stderr warning: `[optarena] Docker image 'optarena-tester:latest' not found - check_command will run on the host. Run \`optarena docker build\`...`
- Run completes via the host fallback, doesn't hang or crash

---

### 4.6 Sandbox network isolation

**Setup:** Image built, Docker running.

**Action:**
```bash
optarena run --driver ollama-chat --name net-check --model qwen3-coder:30b --cases create_server_c
```

**Expected:**
- The case's `test_server.py` (written as a hidden `test_setup_files` script) binds a TCP server on `127.0.0.1:8080` and connects a client to it, **inside** the sandbox - this must succeed even though the container runs with `--network none`, because loopback works within a single network namespace regardless of external network access
- Case passes when the model's server correctly echoes bytes back

---

### 4.7 Port-reuse trade-off across trials (known, documented behavior)

**Setup:** Image built.

**Action:**
```bash
optarena run --driver ollama-chat --name port-reuse --model qwen3-coder:30b --cases create_server_c --trials 3
```

**Expected:**
- Usually all 3 trials pass
- Occasionally one trial fails with `Bind failed: Address already in use` (or the client times out connecting) - this is an accepted, documented trade-off of one shared container/network-namespace serving all trials (see ARCH.md section on DockerSandbox); it is not a bug to file, though repeated failures on every trial would be worth investigating

---

## 5. Trials, Parallel, and Matrix Runs

### 5.1 Trials - majority verdict

**Action:**
```bash
optarena run --driver ollama-chat --name trials-check --model gemma3:1b --trials 3 --cases create_hello_world
```

**Expected:**
- Banner shows `trials=3`
- Result line shows `[X/3 trials]` after the PASS/FAIL status
- Detail lines are prefixed `[trial 1]`, `[trial 2]`, `[trial 3]`, each with its own sandbox/exit/timing
- Overall `passed` is the majority (>=2 of 3) verdict, not "all must pass"

---

### 5.2 Trials are ignored for session-caching drivers

**Setup:** A VS Code UI driver (e.g. `cline-ui`) configured and working (section 12).

**Action:**
```bash
optarena run --driver cline-ui --name ui-trials --trials 3 --cases create_hello_world
```

**Expected:**
- A note prints: `[runner] NOTE: cline-ui runs all cases in one session; --trials ignored for this driver`
- The case runs exactly once, not three times

---

### 5.3 Parallel fan-out for parallel-safe drivers

**Action:**
```bash
optarena run --driver ollama-chat --name parallel-check --model gemma3:1b --parallel 4
```

**Expected:**
- Banner shows `parallel=4`
- Wall-clock time for the whole run is noticeably shorter than a serial run of the same 7 cases (cases execute concurrently via a thread pool)
- All 7 case results still print (order may differ slightly from serial runs since results are collected after all futures complete)

---

### 5.4 Parallel is ignored for non-parallel-safe drivers

**Setup:** A UI driver.

**Action:**
```bash
optarena run --driver cline-ui --parallel 4 --cases create_hello_world
```

**Expected:**
- Note: `[runner] NOTE: cline-ui is not parallel-safe; running serially`
- Runs serially despite the flag

---

### 5.5 Matrix run across drivers and models

**Setup:** `aider` installed (`pip install aider-chat`), or substitute a second baseline model.

**Action:**
```bash
optarena run --matrix-drivers ollama-chat,aider --matrix-models gemma3:1b,qwen3-coder:30b --cases create_hello_world
```

**Expected:**
- Expands to 4 scenarios (2 drivers x 2 models), each named distinctly (e.g. `ollama-chat-gemma3:1b`, `aider-qwen3-coder:30b`)
- All 4 run in sequence (or in parallel if `--parallel` is also set)
- Because there are more than 2 records, a terminal matrix table prints: columns `scenario | pass rate | mean time | tokens`, plus a hint: `(pairwise compare of any two: optarena compare <a> <b>)`
- Exit code is `0` only if every scenario in the matrix had zero failures

---

### 5.6 Exactly two scenarios auto-compare

**Action:**
```bash
optarena run --scenario scenarios/example-a.json --scenario scenarios/example-b.json
```
*(create two minimal scenario JSON files first if none exist - see 6.1's schema)*

**Expected:**
- Both scenarios run
- Because exactly 2 records exist, the full side-by-side comparison table prints automatically (not the >2 matrix summary)
- Line: `comparison saved: <path>`

---

## 6. Case Packs and Custom Cases

### 6.1 Scaffold a project-local case pack

**Action:**
```bash
optarena init ./my-cases
```

**Expected:**
- Creates `./my-cases/sample_hello.json` with a full example: `prompts`, `expected_files` (with `content_patterns` and `not_content_patterns`), `test_setup_files` (a real hidden test script), and `check_command`
- Prints: `wrote ./my-cases/sample_hello.json` and a hint: `run with: optarena run --driver ollama-chat --cases-dir ./my-cases --name local-cases`
- Running `optarena init ./my-cases` again does **not** overwrite the file: `./my-cases/sample_hello.json already exists - not overwriting`

---

### 6.2 Run against a custom case pack

**Setup:** 6.1 completed.

**Action:**
```bash
optarena run --driver ollama-chat --cases-dir ./my-cases --name local-cases --model qwen3-coder:30b
```

**Expected:**
- Banner shows `cases=1`
- The sample case runs exactly like a built-in one (same oracle, same Docker sandboxing if applicable)

---

### 6.3 Custom case with `language` tag

**Note:** `optarena list cases` (and `--language` on it) always reads the built-in catalogue directory - it has no `--cases-dir` flag. `optarena list cases --cases-dir ...` fails with `unrecognized arguments`. To filter a custom pack by language, combine `--cases-dir` and `--language` on `run` instead.

**Setup:** Edit `./my-cases/sample_hello.json` to add `"language": "python"`.

**Action:**
```bash
optarena run --driver ollama-chat --cases-dir ./my-cases --language python --model qwen3-coder:30b
```

**Expected:**
- Banner shows `cases=1` - the case is included because its language matches the filter
- `optarena run --driver ollama-chat --cases-dir ./my-cases --language javascript --model qwen3-coder:30b` shows `cases=0` and completes without error (nothing to compare, but not a crash)

---

## 7. The Oracle - Real Test Execution

### 7.1 A keyword-plausible but broken file is correctly failed

**Setup:** `ollama pull gemma3:1b` (small models reliably produce this kind of failure).

**Action:**
```bash
optarena run --driver ollama-chat --name oracle-check --model gemma3:1b --cases create_factorial
```

**Expected:**
- If the model writes non-C code (e.g. C++ with `#include <iostream>`) that nonetheless contains the required substrings (`int main`), the case still **fails**, because `check_command` actually invokes `gcc` and it rejects the file
- The failure detail includes a real compiler/runtime error tail, e.g. `error: 'cout' was not declared` or similar - proving execution happened, not substring matching

---

### 7.2 `test_setup_files` are hidden from the model

**Setup:** Any case with `test_setup_files` (all 7 built-in cases have one).

**Action:**
```bash
optarena run --driver ollama-chat --model qwen3-coder:30b --cases create_fibonacci
```

**Expected:**
- Detail line: `test files (hidden from the model): test_fibonacci.py`
- Nothing in the prompt sent to the model (check server/driver logs, or the scenario JSON) mentions this file - it's written into the workspace only after the model's turn completes

---

### 7.3 Diff-size metric

**Action:** Any successful run, e.g.:
```bash
optarena run --driver ollama-chat --model qwen3-coder:30b --cases modify_add_type_hints
```

**Expected:**
- Detail line: `diff: 1 file(s), ~N line(s) changed`
- For `modify_add_type_hints` specifically, N should be a small positive number (the file existed before via `setup_files`, so this is a real delta, not the full file's line count)
- For a newly-created file case (e.g. `create_hello_world`), the diff line count equals the file's total line count (no prior version to diff against)

---

### 7.4 Failure classification

**Setup:** `gemma3:1b` (reliably produces varied failure types).

**Action:** Run several cases and inspect the `[failure_class]` tag on each failure:
```bash
optarena run --driver ollama-chat --model gemma3:1b --name classify-check
```

**Expected:**
- At least one case shows `[syntax_error]` (e.g. a Python `IndentationError`/`SyntaxError` in captured output)
- At least one shows `[assertion_failure]` (an `AssertionError` in the hidden test's output)
- A passing case shows no `[...]` tag at all (failure_class is only set on failure)
- Re-run the saved run's JSON (`results/runs/<id>.json`) and confirm `cases[].extra.oracle.failure_class` matches what was printed

---

### 7.5 Sandboxed vs. host execution is visible in every case

**Action:** Any run with Docker available.

**Expected:**
- Every detail line reads `check_command via docker:optarena-tester:latest (container ...)`, never silently falls back without the earlier warning (4.4/4.5) having printed first

---

## 8. Cost Tracking

### 8.1 Local backend is always free

**Action:**
```bash
optarena run --driver ollama-chat --name free-check --model gemma3:1b --cases create_hello_world
optarena list runs
```

**Expected:**
- Inspect `results/runs/<id>.json`: `cases[].extra.cost_usd == 0.0` for every case
- `summary.total_cost_usd` is `null`/`None` (not `0.0`) - "free" is represented as the absence of a cost, not a fabricated zero-dollar charge

---

### 8.2 Paid model is priced from the built-in table

**Setup:** A scenario pointed at a real paid endpoint (e.g. OpenAI) with a model present in `optarena/pricing.py` (e.g. `gpt-4o`), OR simulate it by calling the function directly:
```bash
python -c "from optarena.pricing import estimate_cost; print(estimate_cost('gpt-4o', 1_000_000, 1_000_000))"
```

**Expected:**
- Returns a positive number (`2.5 + 10.0 = 12.5` for `gpt-4o` at 1M+1M tokens)
- `python -c "from optarena.pricing import estimate_cost; print(estimate_cost('gpt-4o-mini', 1_000_000, 0))"` returns `0.15`, **not** the pricier `gpt-4o` rate - confirms longest-match-wins substring pricing

---

### 8.3 Unknown/unpriced model is free, not guessed

**Action:**
```bash
python -c "from optarena.pricing import estimate_cost; print(estimate_cost('some-random-model-xyz', 500000, 500000))"
```

**Expected:**
- Returns `0.0` - an unrecognized model is never assigned a fabricated cost

---

### 8.4 Overriding the price table

**Setup:**
```bash
mkdir -p ~/.optarena
echo '{"my-custom-model": [2.0, 8.0]}' > ~/.optarena/pricing.json
```

**Action:**
```bash
python -c "from optarena.pricing import estimate_cost; print(estimate_cost('my-custom-model', 1_000_000, 1_000_000))"
```

**Expected:**
- Returns `10.0` (2.0 + 8.0), reflecting the override file
- Delete `~/.optarena/pricing.json` afterward to avoid affecting later tests

---

### 8.5 Dashboard cost tile

**Setup:** At least one saved run.

**Action:**
```bash
optarena serve
```
Open `http://localhost:8300/dashboard/` and select a run.

**Expected:**
- A "Cost" tile shows `free` for an all-local run, or `$X.XX` for a priced one
- A "Files changed" tile shows the mean files-changed per case

---

## 9. Comparing Runs

### 9.1 Compare two saved runs by reference

**Setup:** Two saved runs exist (e.g. from 3.1 and 3.2).

**Action:**
```bash
optarena list runs
optarena compare weak-check strong-check
```

**Expected:**
- Run references resolve by substring match against the run id/filename (the most recent match wins if ambiguous)
- Prints a table: per-case `A`/`B` pass/fail and durations, then a verdict block: `pass rate: X% vs Y% -> more accurate: <label>`, `mean duration: ... -> faster: <label>`
- If both runs have a priced cost, an additional `cost: $X vs $Y -> cheaper: <label>` line appears; if neither run was priced, this line is omitted entirely (not shown as `$0.00 vs $0.00`)
- `comparison saved: <path>` printed; file appears under `results/comparisons/`

---

### 9.2 Compare a case present in only one run

**Setup:** Two runs with different `--cases` scopes (e.g. one ran `create_hello_world` only, the other ran all 7).

**Action:**
```bash
optarena compare <run_a> <run_b>
```

**Expected:**
- Cases missing from one side show `-` in that column, not a crash or a false "fail"

---

## 10. Regression Testing

### 10.1 A real improvement reports zero regressions

**Setup:** Runs from 3.1 (`gemma3:1b`, mostly failing) and 3.2 (`qwen3-coder:30b`, mostly passing) exist.

**Action:**
```bash
optarena regression weak-check strong-check
```

**Expected:**
```
  weak-check  ->  strong-check

  accuracy    <A>% -> <B>%   (+<delta>pp)
  mean time   <A>s -> <B>s  (+/-<delta>s)
  tokens      <A> -> <B>  (<delta>, <pct>%)

  regressed cases (passed in A, failed in B): 0
    (none)

  improved cases (failed in A, passed in B): N
    - <case names>
```
- Exit code `0` (no regressions)
- `comparison saved: <path>` printed

---

### 10.2 A real regression is named and fails the exit code

**Action:** Swap the argument order:
```bash
optarena regression strong-check weak-check
echo "exit code: $?"
```

**Expected:**
- `regressed cases` lists the same case names that were "improved" in 10.1 (the relationship is symmetric)
- Exit code `1`
- This is the intended CI-gate behavior: `optarena regression <baseline> <candidate> || exit 1` in a pipeline would correctly fail the build

---

### 10.3 Cost delta appears only when both runs are priced

**Setup:** Two runs where at least one has `total_cost_usd == None` (e.g. both are local Ollama runs, as in 10.1/10.2).

**Action:** Re-inspect the output of 10.1/10.2.

**Expected:**
- No `cost` line appears at all (not `cost $0.00 -> $0.00`) - confirms the omit-when-unpriced logic

---

### 10.4 No changes at all

**Setup:** Run the identical scenario twice with no code/prompt changes.

**Action:**
```bash
optarena run --driver ollama-chat --name rep-a --model qwen3-coder:30b --cases create_hello_world
optarena run --driver ollama-chat --name rep-b --model qwen3-coder:30b --cases create_hello_world
optarena regression rep-a rep-b
```

**Expected:**
- `regressed cases: 0` and `improved cases: 0`, both `(none)`
- Exit code `0`

---

## 11. Dashboard

### 11.1 Serve the dashboard

**Action:**
```bash
optarena serve --port 8300
```

**Expected:**
- Prints: `OptArena dashboard: http://localhost:8300/dashboard/  (Ctrl+C to stop)`
- Opening that URL in a browser loads the static dashboard, listing saved runs

### 11.2 Select a single run

**Action:** In the dashboard, pick one run.

**Expected:**
- Tiles show: Pass rate, Mean case time, Driver (+ model/backend), Cost, Files changed

### 11.3 Select two runs to compare

**Action:** Pick Run A and Run B from the two dropdowns.

**Expected:**
- Verdict tiles: More accurate, Faster, Pass-rate delta, Cheaper (or "free" if neither run was priced), Tokens
- Per-case duration bars for both runs, color-coded
- A trend chart if the same scenario name has multiple historical runs

### 11.4 Default selection is the two most recent runs

**Setup:** At least 2 saved runs exist.

**Action:** Load the dashboard fresh (no manual selection yet).

**Expected:**
- Run A defaults to the second-newest run, Run B to the newest - a natural "before vs after" default

### 11.5 Dashboard with zero runs

**Setup:** Empty `results/` directory (or point `optarena serve` at a fresh checkout).

**Action:** Load the dashboard.

**Expected:**
- A friendly empty-state message with a suggested first command (`optarena run --driver ollama-chat --name baseline`), not a blank page or a JS error

---

## 12. VS Code UI Drivers

### 12.1 Harness install check

**Action:**
```bash
cd ui-harness && npm install
optarena doctor
```

**Expected:**
- `doctor`'s `ui drivers:` section shows `[ok ] node`, `[ok ] ui-harness node_modules`
- Extension checks (`cline`, `roo`, `continue`, `kilo`) reflect what's actually installed in `~/.vscode/extensions`

### 12.2 Cline UI end-to-end

**Setup:** Cline extension installed. Ollama running with a model pulled.

**Action:**
```bash
optarena run --driver cline-ui --name cline-check --model qwen3-coder:30b --cases create_hello_world
```

**Expected:**
- A **visible** VS Code window launches (don't touch mouse/keyboard during the run)
- The Cline panel opens, the prompt is typed and submitted, approval buttons are auto-clicked
- `hello.py` appears in the temp workspace with the expected content
- Result reported the same way as a baseline driver run (PASS/FAIL, oracle detail) - the UI driver funnels through the identical oracle

### 12.3 Connection timeout - stuck updater

**Setup:** Deliberately leave a `CodeSetup-stable-*.exe` process running (see 2.2).

**Action:**
```bash
optarena run --driver cline-ui --cases create_hello_world
```

**Expected:**
- Run fails with `Connection timeout exceeded` or similar
- `optarena doctor` flags the stuck updater as the likely cause (2.2)
- Killing the process and re-running succeeds

### 12.4 Roo Code / Continue / Kilo Code (experimental)

**Setup:** The relevant extension installed.

**Action:**
```bash
optarena run --driver roo-ui --cases create_hello_world
optarena run --driver continue-ui --cases create_hello_world
optarena run --driver kilo-ui --cases create_hello_world
```

**Expected:**
- Each drives its extension's webview via the same generic wdio harness, using a per-extension descriptor (`ui-harness/src/extensions.js`)
- `optarena doctor` marks these `experimental` - expect occasional breakage if the extension's UI changed since the descriptor was written; note any breakage precisely (which selector/button failed) rather than just "it didn't work"

---

## 13. CLI Agent Drivers

### 13.1 aider (stable)

**Setup:** `pip install aider-chat`. Ollama running.

**Action:**
```bash
optarena run --driver aider --name aider-check --model qwen3-coder:30b --cases create_hello_world
```

**Expected:**
- aider runs headless in the case workspace, `hello.py` is created
- Env is scrubbed of unrelated vars before spawning (check via `optarena doctor` or by inspecting the driver's env-building logic if a leak is suspected)

### 13.2 Fixed-backend CLI agents (Claude Code, Codex)

**Setup:** `claude` or `codex` installed and logged in to their own account.

**Action:**
```bash
optarena run --driver claude-code --name cc-check --cases create_hello_world
optarena run --driver codex --name codex-check --cases create_hello_world
```

**Expected:**
- These ignore `--base-url`/`--model` for routing purposes (backend is `fixed` per the registry) - confirm `optarena list drivers` marks both `fixed`
- The case still runs against the tool's own account/provider and is judged by the identical oracle
- A comparison between `claude-code` and `ollama-chat` is a valid **tool-vs-tool** comparison, but explicitly **not** a backend-vs-backend one - the docs/dashboard should not imply otherwise

### 13.3 Scenario-backend CLI agents (OpenCode, Goose, Qwen Code)

**Setup:** The tool installed, pointed at an OpenAI-compatible or Ollama endpoint.

**Action:**
```bash
optarena run --driver opencode --name opencode-check --model qwen3-coder:30b --cases create_hello_world
optarena run --driver goose --name goose-check --model qwen3-coder:30b --cases create_hello_world
optarena run --driver qwen-code --name qwen-check --model qwen3-coder:30b --cases create_hello_world
```

**Expected:**
- Each correctly routes to the scenario's backend (confirm via the tool's own logs/config, or by pointing at a backend with a distinctive model name and confirming that model was used)
- `optarena doctor`'s `cli drivers:` section shows `[ok ]`/`[MISS]` per tool based on whether its binary is on PATH

### 13.4 Missing CLI binary fails clearly

**Setup:** A tool (e.g. `goose`) not installed.

**Action:**
```bash
optarena run --driver goose --cases create_hello_world
```

**Expected:**
- Fails with a clear "binary not found" error (an `error`, not a silent `failures` oracle mismatch) - infrastructure problems and oracle mismatches must stay visibly distinct
- `optarena doctor` would have already flagged this as `[MISS] goose`

---

## 14. SDK Driver (crewAI)

### 14.1 crewAI end-to-end

**Setup:** `pip install optarena[crewai]`.

**Action:**
```bash
optarena run --driver crewai --name crewai-check --model qwen3-coder:30b --cases create_hello_world
```

**Expected:**
- A minimal crewAI coder agent runs against the scenario backend
- Same oracle judgment as every other driver

### 14.2 crewAI without the optional dependency

**Setup:** `crewai` package not installed.

**Action:**
```bash
optarena run --driver crewai --cases create_hello_world
```

**Expected:**
- A clear import/dependency error naming the missing package and the install command, not a bare traceback

---

## 15. Error Handling and Edge Cases

### 15.1 Backend unreachable

**Action:**
```bash
optarena run --driver ollama-chat --base-url http://localhost:1 --model x --cases create_hello_world
```

**Expected:**
- Case result has `error` set (infrastructure problem), not `failures` (oracle mismatch) - the distinction matters because a comparison where one side errors is a broken experiment, not a lost one
- Exit code `1`

### 15.2 check_command timeout

**Setup:** A custom case with a `check_command` that sleeps longer than `check_command_timeout` (e.g. `"check_command": "sleep 120", "check_command_timeout": 2`).

**Action:**
```bash
optarena run --driver ollama-chat --cases-dir ./my-cases --cases slow-case
```

**Expected:**
- Fails with `check_command timed out after 2s (docker exec): sleep 120` (or the host-fallback equivalent)
- No orphaned process left running (the sandboxed exec is killed via the container's own `timeout` wrapper, not left to run indefinitely)

### 15.3 Interrupt mid-run (Ctrl+C)

**Setup:** A long-running scenario (many cases, or `--trials 5`+).

**Action:** Start a run, then press Ctrl+C partway through.

**Expected:**
- The Docker sandbox container (if active) is still stopped/removed - check `docker ps -a` afterward for lingering `optarena-sandbox-*` containers
- No partial/corrupt `results/runs/*.json` file is left behind (the run record is only written after all cases complete)

### 15.4 Two scenarios with the same name

**Action:**
```bash
optarena run --driver ollama-chat --name dup --model gemma3:1b --cases create_hello_world
optarena run --driver ollama-chat --name dup --model gemma3:1b --cases create_hello_world
```

**Expected:**
- Both runs are saved as distinct files (run ids are timestamp-prefixed, so same-name runs never collide on disk)
- `optarena list runs` shows both

### 15.5 Case JSON with a syntax error

**Setup:** A case pack directory containing one malformed JSON file.

**Action:**
```bash
optarena run --driver ollama-chat --cases-dir ./broken-cases --model gemma3:1b
```

**Expected:**
- Fails with a clear JSON parse error naming the file, rather than a generic crash with no file context
