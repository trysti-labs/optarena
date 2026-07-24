# Research note: dynamic / multi-agent evaluation and what it means for OptArena (July 2026)

Starting point: **REALM-Bench** (arXiv [2502.18836](https://arxiv.org/abs/2502.18836), Geng & Chang,
Stanford). Reading it + the surrounding 2025–26 literature surfaced a clear directional shift in
agent evaluation and a specific, defensible opening for OptArena. This note captures the paper, the
cluster around it, the cross-cutting themes, and concrete implications.

---

## 1. The seed paper — REALM-Bench

**What it is.** A benchmark for **LLM + multi-agent systems on real-world, dynamic planning and
scheduling** (logistics-flavored). 14 problems, basic → highly complex, each **scalable along three
axes**: number of parallel planning threads, inter-dependency complexity, and **frequency of
unexpected disruptions requiring real-time adaptation**. Tests GPT-4o / Claude-3.7 / DeepSeek-R1
across LangGraph, AutoGen, CrewAI, Swarm (and ALAS), against 15 classical scheduling baselines
(LPT/SPT/DRL variants, GP/GEP, …).

**The thesis that matters for us.** Existing benchmarks over-index on **static, single-shot** tasks.
Real operational work has **temporal dynamics, interdependencies, and mid-execution disruptions**
(equipment failure, resource shortage, timing conflict) that demand **replanning and recovery** —
and current systems are weak exactly there. Its distinguishing move is measuring **reliability under
disruption**, not just success on a fixed task.

---

## 2. This is a cluster, not a one-off — the 2025–26 shift

REALM-Bench sits in a wave of benchmarks all pushing past static, outcome-only evaluation:

| Benchmark | Domain | The new axis it adds |
|---|---|---|
| **REALM-Bench** ([2502.18836](https://arxiv.org/abs/2502.18836)) | planning/scheduling, multi-agent | disruptions → replanning; reliability under perturbation |
| **PlanBench-XL** ([2606.22388](https://arxiv.org/pdf/2606.22388)) | long-horizon tool-use | **dynamic blocking events**, partial tool observability, unreliable tools |
| **CostBench** ([2511.02734](https://arxiv.org/pdf/2511.02734)) | multi-turn tool-use | 4 disruption types (cost/preference change) → **cost-optimal replanning** |
| **LoCoBench-Agent** ([2511.13998](https://arxiv.org/abs/2511.13998)) | **software engineering** | interactive, multi-turn, **long-context (10K–1M tok)**; 9 metrics for comprehension **vs efficiency** |
| **SentinelBench** ([2606.05342](https://arxiv.org/pdf/2606.05342)) | monitoring | **long-running** agents |
| **"Seeing the Whole Elephant"** ([2604.22708](https://arxiv.org/pdf/2604.22708)) | multi-agent | **failure attribution** (which agent/step caused the failure) |
| Surveys ([2507.21504](https://arxiv.org/html/2507.21504v1), [2503.16416](https://arxiv.org/html/2503.16416v2), [2602.02760](https://arxiv.org/html/2602.02760)) | — | codify: process metrics, robustness, adaptation |

A widely-cited empirical result from this line: **agent performance drops ~40% under dynamic
conditions** vs the static version of the same task — i.e. static benchmarks systematically
*over-report* real-world capability.

---

## 3. Five cross-cutting themes (and where OptArena stands on each)

1. **Static → dynamic.** Grade behavior when the environment *changes mid-task* (a file/dep/test
   shifts), forcing replanning/recovery.
   *OptArena:* **static** — the filesystem oracle judges the *final* state only. **Gap.**
2. **Outcome → process/trajectory.** Score the path (tool calls, turns, exploration cost,
   comprehension-vs-efficiency), and attribute failures to steps.
   *OptArena:* **partially closed** — the recent trajectory work (off-target edits, per-step
   timing/tokens, `clean_passes`) is exactly this direction, but there's no exploration-cost or
   comprehension metric and no step-level failure attribution.
3. **Single-shot → long-horizon / multi-turn.** Extended sessions; efficiency and architectural
   consistency over many turns.
   *OptArena:* **early** — multi-prompt cases + a new `build_calculator_longhorizon` exemplar exist,
   but nothing near LoCoBench-Agent's 10K–1M-token, many-turn sessions. **Gap.**
4. **Single-agent → multi-agent.** Coordination, role specialization, conflict resolution,
   inter-agent dependencies; failure attribution across agents.
   *OptArena:* **minimal** — driver model is single-tool; the crewAI SDK driver is a bare baseline.
   **Gap (but arguably out of OptArena's niche).**
5. **Success → reliability/robustness.** Consistency and safe recovery under perturbation and repeats.
   *OptArena:* **strong foundation** — `--trials` majority + flaky flag + Wilson CIs + paired
   McNemar. Missing: robustness *under injected perturbation* (theme 1).

---

## 4. Implications for OptArena

### 4a. The static-oracle limitation is now the field's central critique
OptArena's core design — run the generated code, assert on the final workspace — is precisely what
this literature argues *over-reports* capability. That's not fatal (real execution is still the
right foundation and most of these benchmarks don't even execute code), but it means the **"grade
the final state" story is no longer sufficient as a headline** for a 2026 audience. The trajectory
work already shipped is the right hedge; the missing piece is **dynamic disruption**.

### 4b. The white space OptArena is uniquely positioned to own
Almost every dynamic/multi-agent benchmark above is **planning/scheduling/tool-use, not code** — or,
where it *is* code (LoCoBench-Agent), it tests **long-context comprehension, not disruption**. **No
one is doing dynamic, disruption-based evaluation of coding agents with real execution
verification.** OptArena already has the two hard prerequisites nobody else combines:
- **real execution + self-verified oracle** (so a post-disruption state can be objectively graded), and
- **UI-native driving** (it literally drives a live editor session, the natural place to inject a
  mid-session change — a file edit, a failing test, a dependency bump — between the agent's turns).

That is a genuinely differentiated position: **"the dynamic coding-agent arena."**

### 4c. Where OptArena is *ahead* of this cluster
Reproducibility rigor. REALM-Bench et al. emphasize reliability but many agent benchmarks are hard
to reproduce and don't execute code. OptArena's **self-verified corpus (100% reference-checked),
fail-closed hardened sandbox, run manifest + content hashes, and packs** give it reproducibility and
provenance most of these lack. Lead with that when positioning against dynamic benchmarks — it's
complementary, not behind.

---

## 5. Concrete recommendations (prioritized)  — ✅ Tiers 1–3 IMPLEMENTED (2026-07-22), hardened + extended (2026-07-23)

**Tier 1 — the differentiated bet: a disruption hook (dynamic coding eval). ✅ DONE, and now UI-native too.**
An optional `disruptions` block on a case fires **between prompts** (reusing the multi-prompt loop):
either a **fixed** `after_prompt: N` (a config/dependency that changed, a reverted edit, a now-failing
test) or a **reactive** `when: {file_exists: <path>} | {file_contains: {path, pattern}}` trigger that
fires the first time the *workspace itself* satisfies a condition - responding to what the agent
actually did rather than a hardcoded turn count. Drivers (`cli_agents`, `openai_chat`, and now the
**VS Code UI harness** - `ui-harness/src/oracle.js` + `test/agent.e2e.js`) call `apply_disruptions`
after each prompt so the next prompt runs in the changed world; the oracle then grades whether the
agent **adapted**. `verify-corpus` force-applies **every** disruption, fixed or reactive, before
laying the solution down (`apply_all_disruptions`, ascending `after_prompt` order then reactive-by-
declaration-order), so a reference is proven correct *through* the disruption and a "didn't adapt"
variant fails there. The JS and Python engines are proven byte-for-byte identical via a new
`apply_disruptions` conformance op (`ui-harness/test/conformance-runner.mjs` +
`tests/test_conformance.py`), the same H-09 discipline already used for the rest of the oracle.

Corpus grew from **1 to 6 self-verified dynamic cases** spanning 6 languages and every disruption
shape: `adapt_config_change_dynamic` (python, fixed, value rewrite), `adapt_removed_dependency_go_dynamic`
(go, fixed, file deletion), `adapt_reactive_config_java_dynamic` (java, **reactive** `file_contains`),
`adapt_reactive_retry_config_node_dynamic` (node, **reactive** `file_exists`),
`adapt_compounding_config_rust_dynamic` (rust, **two fixed disruptions in one case** - the agent must
re-adapt twice), `adapt_removed_helper_ruby_dynamic` (ruby, `bug_fix` task_type + deletion, exercises
the `MUST_FAIL_UNMODIFIED` path too). All 19 variants across the 6 cases pass the real Docker oracle.

Failure attribution is now **precise, not just cheap**: for cases with `disruptions`, every non-final
prompt also runs the REAL oracle (`check_command`, not just the expected-file shape check) via
`evaluate_case_isolated` - graded on a disposable sibling copy of the workspace, never the live one a
CLI agent's next turn would `cwd` into (a real bug caught and fixed in this pass: an earlier version
wrote the hidden `test_setup_files` straight into the live workspace between prompts, which would have
let a real agent read its own hidden test on the next turn - see `evaluate_case_isolated`'s docstring
and the regression test `test_precise_step_check_does_not_leak_hidden_tests_into_live_workspace`).
`metrics.attribute_failure` now says "the case's real oracle (behavior) passed after prompt 2,
regressed by prompt 3" when that precise signal is available, falling back to the cheap
expected-file wording otherwise.

**Validated against a real, running model - not just a mock.** No full agentic CLI tool (Claude Code /
Codex / OpenCode / Goose / Qwen Code) or a VS Code+extension display was available in this sandboxed
dev environment, so the strongest validation achievable here was the `ollama-chat` baseline driver
against a locally-pulled `qwen2.5-coder:1.5b`, run for real over HTTP against a real Ollama server: one
fixed-trigger case (`adapt_config_change_dynamic`) and one reactive-trigger case
(`adapt_reactive_retry_config_node_dynamic`). Both runs show the real pipeline working end to end - the
disruption fires and is logged (`disruptions fired: MAX_RETRIES changed 3 -> 7, triggered reactively
once client.js first appeared`), the live workspace reflects the post-disruption state, the Docker
oracle runs for real, and the (small, non-agentic, single-shot) model correctly FAILS both, with
precise attribution ("the case's real oracle never passed at any prompt"). This proves the mechanism,
not tool-specific competence - validating an actual agentic tool's adaptation *rate* is still open,
gated on installing one of those tools (or a GUI-capable host for the UI harness) outside this sandbox.

**Still deferred (mechanism ships, breadth doesn't):** the UI harness's disruption support is
implemented and conformance-tested against the Python engine, but has never been run through a real
`npm test` VS Code + extension session (no display server / no extension install in this environment) -
that's the next thing to validate once run somewhere with a GUI. The 6-case dynamic corpus is a
credible proof of the mechanism across languages and trigger types, not yet corpus-scale breadth
(dozens of cases per language) - grow it opportunistically alongside the rest of the corpus rather than
as a one-time push.

**Tier 2 — efficiency / long-horizon metrics. ✅ DONE.** `metrics.aggregate` now reports
**`tokens_per_pass`** and **`steps_per_pass`** (LoCoBench-Agent's "cheaper to get right"), shown in
the CLI run summary, `compare`, and the dashboard. Off-target-edit exploration cost + per-step
tokens/timing were already captured; this is the success-cost rollup on top.

**Tier 3 — failure attribution. ✅ DONE, upgraded to precise (see Tier 1).** Drivers record a cheap
per-step `expected_ok` (do the expected-file checks pass right now, measured before any disruption)
and `ok`/`disrupted` flags for every case, plus a precise `oracle_ok` (the real check_command-backed
verdict) for disruption cases specifically. `metrics.attribute_failure` turns those into a one-line
"regressed by prompt N (disruption fired here: …)" / "tool reported failure at prompt N" / "never
satisfied", printed under a failed case - the coding analog of multi-agent failure attribution.

**Deliberately deferred — full multi-agent coordination.**
REALM-Bench-style multi-agent orchestration is a different product surface (and OptArena's niche is
*real single-agent coding tools*). Keep the crewAI/SDK driver path as-is; revisit only if the
multi-agent coding-tool market (e.g. orchestrated sub-agents inside one tool) matures.

---

## 6. One-paragraph takeaway
The 2025–26 evaluation frontier is moving from **static, outcome-only, single-agent** to **dynamic,
process-aware, multi-turn/multi-agent**, and static benchmarks are shown to over-report real
capability by ~40%. OptArena is exposed on *dynamic* and *long-horizon*, partially hedged on
*process* (the new trajectory work), and out-of-scope on *multi-agent*. But the same execution +
self-verified + UI-native foundation that made it distinctive also makes it **the only tool
positioned to do disruption-based, real-execution evaluation of coding agents** — a white space the
whole cluster leaves open. The highest-leverage next step is a **between-prompt disruption hook**;
the trajectory groundwork for it is already in place.

## Sources
- REALM-Bench — https://arxiv.org/abs/2502.18836 (HTML v2: https://arxiv.org/html/2502.18836v2)
- PlanBench-XL — https://arxiv.org/pdf/2606.22388
- CostBench — https://arxiv.org/pdf/2511.02734
- LoCoBench-Agent — https://arxiv.org/abs/2511.13998
- SentinelBench — https://arxiv.org/pdf/2606.05342
- Failure attribution in multi-agent systems — https://arxiv.org/pdf/2604.22708
- Survey: Evaluation and Benchmarking of LLM Agents — https://arxiv.org/html/2507.21504v1
- Survey: Evaluation of LLM-based Agents — https://arxiv.org/html/2503.16416v2
- From Task Solving to Robust Real-World Adaptation — https://arxiv.org/html/2602.02760
