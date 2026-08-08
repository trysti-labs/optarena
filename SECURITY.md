# Security

## Reporting a vulnerability

Please email **arun@trysti.com** with details (affected file/function, a
reproduction, and impact). Do not open a public issue for anything
exploitable; you'll get an acknowledgment within a few days and credit in the
fix commit unless you prefer otherwise.

## Trust model

OptArena runs four kinds of untrusted-or-semi-trusted code, each with its own
boundary:

1. **Case-defined `check_command`s and test files** (from the built-in corpus
   or any `--cases-dir` pack you load). These execute inside a hardened
   container (Docker or Podman, auto-detected -
   `OPTARENA_CONTAINER_ENGINE` forces one): `--network none`, `--cap-drop=ALL`,
   `--security-opt no-new-privileges`, `--pids-limit`, read-only rootfs with a
   bounded exec tmpfs, and memory/CPU limits. **If no container engine is
   available, OptArena refuses to run them** - host execution requires an
   explicit opt-in (`OPTARENA_DISABLE_SANDBOX=1` or `OPTARENA_ALLOW_UNSAFE_HOST_EXEC=1`).
   All case-supplied file paths are containment-checked before anything is
   read or written, at two layers: schema validation rejects an unsafe path
   before a case is even loaded (before any model/API call it could waste),
   and every write site re-checks immediately before touching disk.
   `setup_files`, `test_setup_files`, `reference_solution`,
   `broken_solutions[].files`, `expected_files[].path_pattern`, disruption
   `write_files`/`delete_files`, and `setup_repo` all reject traversal
   (`../`), absolute paths, and symlinked escapes, in both directions (a
   `setup_repo` naming a directory outside `repos/` is refused, not just a
   destination outside the workspace). The same paths also reject any `.git`
   segment: `git_init` runs host-native `git init`/`add`/`commit` after a
   case's files are written, so a case-controlled `.git/config` or
   `.gitattributes` could otherwise get a malicious filter/hook executed on
   the host during `git add`. `git_init` additionally runs with a minimal
   explicit environment (no inherited host env, no system/global gitconfig,
   an empty scratch `HOME`) as a second, independent layer. A case's `image` is validated as a container image reference,
   so it cannot smuggle flags onto the engine's command line. Only load case
   packs from sources you trust enough to run in that sandbox: the pack format
   carries a content hash, which detects corruption and accidental tampering,
   but it is self-declared - it is not a signature and proves nothing about
   who wrote the pack.

   Containers run as root by default with an otherwise-minimal capability set.
   Set `OPTARENA_SANDBOX_USER=1000:1000` to run them as the unprivileged
   `optarena` account every published image now provides (the runner relaxes
   workspace permissions to match). This is opt-in rather than the default
   because the run root is a host bind mount whose ownership OptArena cannot
   guarantee across Docker Desktop, rootless Podman, and CI. Note that for the
   built-in corpus, one container is shared across a run, so a case's
   `check_command` can reach sibling cases' workspaces through the shared
   mount; custom `--cases-dir` packs never share a container (each gets an
   ephemeral one mounting only its own workspace).

2. **The coding agents under evaluation.** `optarena run` refuses to start a
   `cli`-kind driver (aider, Claude Code, Codex, opencode, goose, qwen-code)
   without confirmation: an interactive terminal gets a y/N prompt, and
   non-interactive use (CI, scripts) requires
   `--yes-i-understand-host-execution` explicitly. CLI agents run headlessly
   with auto-approval flags - that is the point of
   the evaluation - but receive an **explicit environment allowlist** (PATH,
   HOME, locale, temp dirs, plus exactly the provider credential the scenario
   configures), never a copy of your full shell environment. SDK/agent-
   framework drivers (crewAI, OpenAI Agents SDK, smolagents, LangGraph,
   AutoGen, Semantic Kernel) run **in-process** instead of as a subprocess,
   so the environment allowlist doesn't apply to them the same way - each
   constructs its own explicit client scoped to the scenario's own backend
   URL/key rather than reading ambient credentials, and none are given file
   or shell tools (they reply with a single code block, which the driver
   writes to disk itself). Neither CLI nor SDK agent processes are
   containerized - only the verifier is - so an agent can still do what your
   user account can do within those env/tool limits. Don't run agents you
   don't trust with prompts you don't control.

3. **Hidden tests vs. the agent.** `test_setup_files` are never visible to the
   agent while it works: **temporal** isolation - the hidden tests are
   written into the workspace only *after* the tool process has exited, so
   the agent never runs concurrently with them. For multi-prompt cases with
   disruptions, the mid-session per-step attribution check additionally
   grades a **private copy** of the workspace rather than the live one it
   will read again on its next turn. The model is graded against tests it
   could not read or watch during its run.

4. **Real third-party MCP servers, under `--tool-service-mode sandboxed`.**
   Tool-use cases default to an in-process Python mock with no external
   surface at all; sandboxed mode instead runs the ACTUAL published
   reference server (npm/PyPI/Go binaries - `@modelcontextprotocol/server-filesystem`,
   `mcp-server-git`, `mcp-grafana`, and others) inside the same hardened
   container profile as category 1, one dedicated container per case,
   bind-mounting only that case's own directory. Two consequences worth
   stating plainly:

   - **Sandboxed execution is granted by you, never by case content.** A
     case's own `tool_service_mode` may opt *down* to `mock`, but only
     `--tool-service-mode sandboxed` (or the scenario field) can opt *up* -
     so an installed pack cannot decide by itself to start pulling and
     running third-party server binaries on your machine.
   - **Running tool-use cases through a real agent combines two of these
     boundaries at once.** `optarena agent --tool-call` puts a CLI agent
     (category 2 - host-native, NOT containerized) in front of a real
     third-party MCP server (this category - containerized). Each keeps its
     own boundary: the server stays in the hardened container, the agent
     stays on your host with the usual confirmation gate. What is new is
     that the model now drives a real server through an agent that can also
     act on your machine, so the two grants compound - and for
     `docker`/`kubernetes` they compound with host-daemon access, which is
     why those still need `OPTARENA_ALLOW_HOST_DOCKER=1` on top. The agent
     is pointed at exactly one server and explicitly isolated from your own
     configured MCP servers (`--strict-mcp-config` for Claude Code,
     `--no-profile` for goose), so a case can never reach a server you
     configured for unrelated work.
   - **`docker` and `kubernetes` are a materially larger boundary and are
     refused by default.** Their real servers work by talking to your
     *actual* host Docker daemon through a mounted `/var/run/docker.sock`
     (docker-outside-of-docker; the kubernetes one additionally creates a
     real `kind` cluster whose nodes are privileged sibling containers on
     that daemon). Because the model under test drives those tools, the
     effective principal holding the socket is the model - it can create
     containers with arbitrary host bind mounts, which is host-root
     equivalent. These two require `OPTARENA_ALLOW_HOST_DOCKER=1`
     explicitly and fail closed with a clear message otherwise. Every
     other sandboxed service keeps `--network none` and needs no such
     grant.

   `tool_service_seed` paths (`files`/`media_files`/`directories`) become
   real disk writes in this mode and go through the same two-layer
   containment as category 1's path fields: schema validation rejects
   traversal/absolute/`.git`/Windows-alias forms before a case loads, and
   the write site re-checks resolved containment against the case
   directory. Sandboxed servers are pinned by version and (for images)
   digest, but they are still third-party code: treat enabling a
   sandboxed service as a decision to run that project's binary.

## Credentials

- `backend.api_key` is **never persisted**: saved runs and comparisons store
  a redacted backend (`api_key: null`, plus an `api_key_set` boolean). Records
  written before that was true still carry the raw value on disk -
  `optarena runs scrub-secrets` reports them, and `--yes` redacts them in
  place.
- No driver puts the key in a subprocess's argv (process arguments are
  readable by other local users); every driver passes it through the
  subprocess environment or an explicit in-process client instead.
- Prefer `OPTARENA_API_KEY` (environment) over `--api-key` (visible in `ps`
  and shell history).
- `optarena serve` binds `127.0.0.1` and serves only `dashboard/` and the
  results directory - never the repository root - with directory listings
  disabled. `--host` can widen that, and warns when it does: the server has no
  authentication and the results directory holds prompts and generated code.

## What OptArena results are

The built-in corpus is public, *including* reference solutions - by design,
so the oracles are auditable and self-verifying (`optarena verify-corpus`).
That makes results trustworthy as **acceptance/regression evidence for tools
you are honestly evaluating**, and unsuitable as a tamper-proof public
leaderboard: a benchmark-aware agent could look answers up. For adversarial
settings, use a private case pack via `--cases-dir`.

## Data flow

Orchestration, results, and the dashboard are local. Prompts and generated
code are sent to whatever backend URL each scenario configures - if that URL
is a remote provider, your prompts and code go there. OptArena's own code has
no telemetry, and the dashboard makes no external requests - but the
in-process SDK/agent-framework drivers each pull in a real third-party
framework, and some of those frameworks ship their own telemetry, enabled by
default, independent of anything OptArena's own `subprocess_env()`
allowlisting can reach (that mechanism only scopes subprocess environments;
these drivers run in-process). The `crewai`, `openai-agents`, and `langgraph`
drivers explicitly disable their framework's default telemetry/tracing in
`prepare()` - confirmed by reading each framework's actual tracing code, not
assumed from its docs - specifically so a scenario aimed at a fully local
backend doesn't silently phone home. If you add a new SDK-agent driver or
bump one of these frameworks to a new major version, re-verify this hasn't
regressed.
