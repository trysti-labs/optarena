# OptArena Website v2

# Vision

OptArena is **the reproducible evaluation platform for AI software engineering agents**.

The website should no longer position OptArena as "a testing framework" or "a comparison tool". Those are capabilities.

Instead it should position OptArena as infrastructure for answering one question:

> **Did this AI coding setup actually get better?**

## Core messaging

### Hero

**Headline**

> The reproducible evaluation platform for AI coding agents.

**Subheading**

Run the same real-world coding task through multiple AI coding tools, verify the generated code by compiling and executing it inside Docker, and compare results with objective metrics.

Primary CTA:
- Get Started

Secondary CTA:
- GitHub

---

## Problem

Developers and companies constantly ask:

- Should we switch from Cline to Claude Code?
- Did GPT-6 actually improve our workflow?
- Did our prompt optimization help?
- Did this extension release regress?
- Is Roo better than Continue for our repositories?

Today these questions are answered mostly by intuition.

OptArena replaces opinions with reproducible evidence.

---

## Hero Visualization

Show a live animated workflow:

Task
↓
Cline | Roo | Claude Code | Aider
↓
Docker Verification
↓
Leaderboard

Animation should show tools completing, tests running, leaderboard updating.

---

## Why OptArena?

Four cards:

1. Which coding agent should we use?
→ Benchmark them.

2. Did upgrading models help?
→ Measure it.

3. Did our latest release regress?
→ Compare against a baseline.

4. Can we trust the results?
→ Docker executes real tests.

---

## Primary USP Section

### Reproducible Evaluation

Not benchmarking APIs.

Not keyword matching.

Not subjective opinions.

OptArena evaluates complete software engineering agents using the same task, workspace, verification pipeline and scoring system.

---

## Leaderboard

Interactive leaderboard showing:

- Pass rate
- Runtime
- Cost
- Tokens
- Files changed

Filters:

- Tool
- Model
- Backend
- Language

---

## Docker Verification

Headline:

Verified. Not guessed.

Generated code is compiled, executed and tested inside Docker using hidden test suites.

---

## Regression Testing

Highlight:

```
optarena regression
```

Example output:

Accuracy  +4%
Cost      -18%
Runtime   +12%

Regressed:
- login
- oauth

---

## Drivers

Show supported categories:

- VS Code agents
- CLI agents
- SDK agents
- Raw model baselines

---

## Audience

Primary:
- AI coding tool companies
- Internal platform teams
- Enterprises evaluating coding agents

Secondary:
- Researchers

Not primarily individual developers.

---

## Future

Introduce the concept of OptArena becoming CI for AI coding:

pytest
eslint
mypy
optarena regression

Every release should prove the agent became better.

