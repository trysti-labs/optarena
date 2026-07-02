# OptArena

**Local-first testing & comparison framework for AI coding tools.**

OptArena runs the same task cases through real coding tools — Cline's actual
VS Code UI, aider's CLI, SDK agents, raw-model baselines — against **any
OpenAI/Ollama-compatible backend** (Ollama, LM Studio, a router/optimizer
proxy like SelfOpt, or a remote API), records per-case metrics, and
**compares scenarios side-by-side**:

- *tool vs tool* — Cline vs aider vs Continue on the same backend
- *backend vs backend* — Cline through an optimizing proxy vs raw Ollama
- *model vs model* — gemma4:8b vs gemma4:12b through the same tool
- *agent vs no-agent* — any tool vs the raw-model baseline (what does the tool add?)

It is **UI-native**: where a tool has a real UI, OptArena drives that UI — a
real VS Code window, real webview chat, real approval buttons — rather than
simulating API traffic. And it is **zero-infrastructure**: stdlib-only Python
core, JSON results, a static-HTML dashboard.

See **[ARCH.md](./ARCH.md)** for the full architecture.

## Install

```bash
git clone <this repo> && cd optarena
pip install -e .            # provides the `optarena` command (no dependencies)

# only for VS Code UI drivers (cline-ui / roo-ui / continue-ui):
cd ui-harness && npm install
```

Requirements: Python ≥ 3.10. UI drivers additionally need Node ≥ 18 and the
extension under test installed in `~/.vscode/extensions` (first UI run also
downloads a pinned VS Code, ~280 MB).

## Quick start

```bash
optarena list drivers                 # what tools can be driven
optarena list cases                   # task catalogue

# One scenario, inline (raw-model baseline against local Ollama)
optarena run --driver ollama-chat --name baseline --model llama3.2

# A/B: two scenarios in one command → auto-compares and saves the comparison
optarena run --scenario scenarios/cline-selfopt.json \
             --scenario scenarios/cline-ollama-direct.json

# Compare any two saved runs later
optarena list runs
optarena compare cline-selfopt cline-ollama-direct

# Dashboard at http://localhost:8300/dashboard/
optarena serve
```

Defaults: `--base-url http://localhost:11434`, `--model llama3.2` — override
per command, or set `OPTARENA_BASE_URL` / `OPTARENA_MODEL` once.

Scenario files are small JSON documents:

```json
{
  "name":    "cline-gemma12b",
  "driver":  "cline-ui",
  "backend": { "kind": "ollama", "base_url": "http://localhost:11434", "model": "gemma4:12b" },
  "cases":   ["create_factorial", "modify_add_type_hints"]
}
```

## Drivers

| Driver | Status | What it exercises |
|---|---|---|
| `cline-ui` | stable | The real Cline extension in an isolated VS Code (real webview typing, auto-approval, workspace diff) |
| `aider` | stable | aider CLI, headless |
| `openai-chat` | stable | Raw model via `/v1/chat/completions` — the no-agent baseline |
| `ollama-chat` | stable | Raw model via Ollama-native `/api/chat` |
| `roo-ui` | experimental | Roo Code via the same VS Code harness |
| `continue-ui` | experimental | Continue via the same VS Code harness |
| `crewai` | optional | crewAI SDK agent (`pip install optarena[crewai]`) |

Adding a driver = one file in `optarena/drivers/` implementing
`run_case(case, scenario, workspace) -> CaseResult`, plus a registry line.
Everything else — runner, metrics, compare, dashboard — is driver-agnostic.

## Cases & the oracle

Cases live in `optarena/cases/*.json`: prompts + `setup_files` +
`expected_files` (path pattern + required content substrings). The
**filesystem diff is the oracle** — a case passes when the expected files
exist with the expected content, no matter how the tool produced them.

Per-case metrics: pass/fail, failure reasons, wall time, files
created/changed, tokens (where the backend reports usage). `compare` adds
per-case deltas and a verdict (more-accurate / faster).

## Notes for UI runs

The VS Code UI drivers launch a **visible** editor window — don't touch mouse
or keyboard during a run. If a UI run fails with `Connection timeout
exceeded`, check for a stuck VS Code auto-updater (`CodeSetup*.exe`) holding
the global update mutex — see ARCH.md §8 for this and the other
environment gotchas the harness absorbs.

`legacy/` contains the retired first-generation (CDP + pyautogui) harness,
kept for reference only.
