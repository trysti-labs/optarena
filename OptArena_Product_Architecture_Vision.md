# OptArena Product Vision & Architecture

# Mission

Build the reproducible evaluation platform for AI software engineering agents.

The objective is not to compare models.

The objective is to make AI coding improvements measurable.

---

# Problem

Today teams cannot objectively answer:

- Did our new model improve coding?
- Did our new prompts help?
- Should we migrate to another coding tool?
- Did the latest release regress?

Most decisions are based on subjective experience.

---

# Product Position

OptArena is evaluation infrastructure.

It provides:

- repeatable execution
- repeatable verification
- reproducible metrics
- reproducible comparisons

---

# Core USP

Run the same software engineering task through different agents and objectively determine which performs better.

---

# Pillars

## 1. Real Agents

Supports actual tools:

- Cline
- Roo
- Continue
- Claude Code
- Codex
- Aider
- Goose
- OpenCode

## 2. Real Verification

Generated code is executed inside Docker.

No keyword matching.

Compilation and hidden tests determine correctness.

## 3. Reproducibility

Same:

- prompt
- repository
- workspace
- oracle

Different:

- agent
- backend
- model

## 4. Regression

Historical comparisons should become first-class.

Future command:

optarena regression

---

# Target Users

Priority:

1. AI coding tool companies
2. Enterprise platform teams
3. Internal agent builders
4. Researchers

---

# Long-term Vision

OptArena should become the equivalent of CI for AI coding systems.

Today CI checks:

- unit tests
- lint
- formatting

Future CI should also check:

- agent accuracy
- regression
- cost
- runtime
- success rate

---

# Strategic Direction

The long-term moat is NOT UI automation.

The moat is the benchmark ecosystem.

High-quality benchmark cases should be easy to create, version and share.

Community contributions should expand the benchmark library over time.

---

# Roadmap

Phase 1
- Core runner
- Drivers
- Docker verification
- Reports

Phase 2
- Regression
- Historical comparisons
- Dashboard improvements

Phase 3
- Public benchmark suites
- CI integrations
- Hosted leaderboards

---

# Success Metric

The project succeeds when teams ask:

'Did this make our coding agent better?'

and the answer is:

'Run OptArena.'
