# Drivers

OptArena supports these headless CLI coding-agent drivers through one generic
subprocess adapter:

| Driver | Backend | Status | Invocation |
|---|---|---|---|
| `claude-code` | fixed | experimental | `claude -p` |
| `codex` | fixed | experimental | `codex exec --full-auto` |
| `opencode` | scenario | experimental | `opencode run` |
| `goose` | scenario | experimental | `goose run -t` |
| `qwen-code` | scenario | experimental | `qwen -p` |
| `gemini-cli` | fixed | experimental | `gemini -p --output-format json` |

Fixed drivers use their own authenticated provider account. Scenario drivers
use the backend configured in the OptArena scenario. All CLI drivers run in an
isolated case workspace with an explicit subprocess environment allowlist.