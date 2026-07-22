# OptArena Website Design Specification (React + Vite)

## Philosophy

Not a documentation site.

Not a GitHub README.

It should feel like visiting **Vercel**, **Linear**, or **Playwright**.

Visitors should understand the product in **under 10 seconds**.

The homepage should answer:

-   What is it?
-   Why should I care?
-   Why is it different?

without scrolling much.

------------------------------------------------------------------------

# Tech Stack

``` text
React
Vite
TypeScript
Tailwind CSS

Framer Motion

shadcn/ui

Lucide

Recharts

React Router

React Syntax Highlighter
```

------------------------------------------------------------------------

# Color Palette

Dark-first.

**Background**

``` text
#09090B
```

**Panels**

``` text
#111114
```

**Borders**

``` text
#27272A
```

**Primary**

``` text
#4F8CFF
```

**Success**

``` text
#22C55E
```

**Failure**

``` text
#EF4444
```

**Warning**

``` text
#F59E0B
```

**Text**

``` text
#FAFAFA
```

**Secondary**

``` text
#A1A1AA
```

------------------------------------------------------------------------

# Typography

**Heading**

``` text
Inter Tight
```

**Body**

``` text
Inter
```

**Terminal**

``` text
JetBrains Mono
```

------------------------------------------------------------------------

# Navigation

``` text
Logo

Features

Drivers

Docs

GitHub

Get Started
```

Sticky, blurred background, shrinks on scroll.

------------------------------------------------------------------------

# Hero

## Left side

``` text
The arena where AI coding tools compete.

Run the same task through Cline, Roo,
Claude Code, Aider and raw models.

Compile it.

Test it.

Rank it.

Automatically.

[ Get Started ]

[ GitHub ]
```

## Right side

Animated Arena.

``` text
          Same Task

              │

    ┌─────────┼──────────┐

 Cline      Roo       Claude

    │         │          │

    └─────────┼──────────┘

      Docker Verification

              │

      Live Leaderboard
```

Everything animates.

------------------------------------------------------------------------

# Terminal

Animated typing.

``` bash
$ optarena run benchmark.yaml

Running...

✓ Cline

PASS

91%

✓ Claude Code

PASS

95%

✓ Roo

PASS

88%

Generating report...

Done.
```

------------------------------------------------------------------------

# Trusted By

Eventually

``` text
Cline

Aider

Continue

Claude Code

OpenCode

Goose
```

Initially show:

``` text
Works with
```

------------------------------------------------------------------------

# Why OptArena?

Four large cards.

``` text
Which coding tool should I use?

↓

Run the same benchmark everywhere.
```

``` text
Did GPT-6 actually help?

↓

Measure it.
```

``` text
Did this release regress?

↓

Compare every run.
```

``` text
Can I trust the result?

↓

Docker verifies it.
```

------------------------------------------------------------------------

# Live Leaderboard

Interactive and sortable.

Columns:

-   Rank
-   Driver
-   Pass %
-   Runtime
-   Cost
-   Tokens

Bars animate and hover expands details.

------------------------------------------------------------------------

# Arena Workflow

Horizontal animated timeline.

``` text
Task

↓

Workspace

↓

Agent

↓

Filesystem

↓

Docker

↓

Tests

↓

Results
```

Every node is clickable.

------------------------------------------------------------------------

# Docker Verification

## Left

Animation:

``` text
Generated Code

↓

Docker

↓

pytest

↓

PASS
```

## Right

``` text
Real execution.

Not keyword matching.

Generated code is compiled,
executed and verified inside
an isolated container.
```

------------------------------------------------------------------------

# Drivers

Cards grouped by category.

## VS Code

``` text
Cline

Roo

Continue
```

## CLI

``` text
Claude Code

Aider

Codex

Goose
```

## SDK

``` text
crewAI
```

Hover reveals capabilities.

------------------------------------------------------------------------

# Feature Grid

``` text
UI Native

Real VS Code
```

``` text
Docker

Sandboxed execution
```

``` text
Comparison

Tool vs Tool
```

``` text
Regression

Version history
```

``` text
Backend Agnostic

OpenAI

Ollama

LM Studio

SelfOpt
```

``` text
Zero Infrastructure

JSON

Static HTML

Local First
```

------------------------------------------------------------------------

# Regression

GitHub-style section.

``` text
optarena regression

Accuracy

+4%

Cost

-18%

Runtime

+12%

Broken

oauth

login
```

Animated counters.

------------------------------------------------------------------------

# Code Example

``` bash
optarena run \
  --matrix-drivers cline-ui,aider \
  --matrix-models gpt-5.5,gemma4
```

Output:

``` text
PASS

PASS

FAIL
```

------------------------------------------------------------------------

# Dashboard Preview

Eventually replace screenshots with an embedded interactive report
supporting:

-   Search
-   Sort
-   Dark mode
-   Resizing

------------------------------------------------------------------------

# Open Source

``` text
MIT

GitHub

Python

TypeScript

Docker

VS Code
```

------------------------------------------------------------------------

# Final CTA

``` text
Stop guessing.

Start benchmarking.

[ Get Started ]

[ GitHub ]
```

------------------------------------------------------------------------

# Footer

``` text
OptArena

Docs

GitHub

Discord

Trysti Labs
```

------------------------------------------------------------------------

# Animations

Use Framer Motion.

-   Fade up on scroll
-   Animated counters
-   Leaderboard bar animations
-   Card hover lift
-   Terminal typing animation
-   Workflow pulse
-   Docker activity animation
-   Smooth leaderboard reordering

------------------------------------------------------------------------

# Mobile

Instead of shrinking the desktop layout:

-   Arena becomes a vertical pipeline.
-   Leaderboard becomes swipeable cards.
-   Terminal fills the width.
-   Drivers become a two-column grid.
-   Timeline becomes a vertical stepper.

------------------------------------------------------------------------

# Future Pages

``` text
/

Features

Drivers

Docs

Battle

Leaderboard

Blog
```

------------------------------------------------------------------------

# Highest Priority Feature After Launch

Build **/battle**.

Instead of static marketing, showcase real OptArena runs.

Allow visitors to choose:

-   Prompt
-   Tool
-   Model

Then display:

-   Generated diff
-   Test results
-   Runtime
-   Cost
-   Docker logs
-   Pass/fail
-   Leaderboard ranking

The website itself becomes a live demonstration of OptArena rather than
just a landing page.
