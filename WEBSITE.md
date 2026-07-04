# WEBSITE.md - optarena.com design & content brief

Working brief for the marketing site at `website/` (deployed as optarena.com
via Cloudflare Worker). Written from `OptArena_README_Feedback.md`'s
positioning critique - use this to redesign/regenerate `website/index.html`
without re-deriving the reasoning each time.

## 1. Positioning (read this before writing any copy)

**Old framing:** "a testing framework for AI coding tools."
**New framing:** an **arena** - competition, leaderboards, reproducibility.

> OptArena runs the same task through multiple coding agents/tools, verifies
> the result by actually compiling and running it, and tells you who won.

Tagline candidates (pick one for the hero, keep others as social/OG copy):
- "The arena where AI coding tools compete - and prove it."
- "Stop guessing which AI coding tool is better. Run them against each other."
- "Real tools. Real tests. Real verdicts."

**Category the site should claim** (broader than today's "compare coding
tools"): **evaluate software engineering agents** - UI agents (Cline, Roo,
Continue, Kilo), headless CLI agents (Claude Code, Codex, OpenCode, Goose,
Qwen Code), SDK agents (crewAI), and raw-model baselines, all judged by the
same oracle. The site should imply room to grow into browser agents / SWE
agents without re-branding later.

**Why OptArena can launch/market harder than SelfOpt:** the question "is
Tool A better than Tool B" needs zero prior belief from the visitor. Lead
with that; don't require the reader to first accept that agent evaluation
matters - show them a scoreboard and they get it in 5 seconds.

## 2. Audience

Developers evaluating AI coding tools for their team/workflow, and tool
authors (Cline, aider, etc. maintainers) who want an independent, reproducible
benchmark. Both want proof, not prose - lead with the diagram/leaderboard, not
paragraphs.

## 3. Visual identity (unchanged - reuse, don't reinvent)

- Fonts: Inter (UI/body), JetBrains Mono (code/terminal blocks).
- Dark default, light via `data-theme="light"` + localStorage, toggle in nav
- Design tokens already defined in `website/style.css` `:root` - reuse the
- No emdashes anywhere in copy - use " - " (space-hyphen-space).

## 4. Page structure (top to bottom)

Reordered from the current site specifically to put the two genuinely
differentiated capabilities (UI-native driving, real verification) ahead of
generic "comparison framework" framing, per the feedback doc.

### 4.1 Hero
- Arena-framed headline + one-line sub (see tagline candidates above).
- **Visual workflow diagram, not text, as the first thing below the fold.**
  Render as a real SVG/HTML diagram (not literal ASCII-art text) built from
  the existing `.hero-flow` pattern, extended to show fan-out + verification:

  ```
  Same task
     |
     +--> Cline   --+
     +--> Roo      --+--> Docker verification --> Leaderboard
     +--> Aider    --+
     +--> Claude Code --+
  ```

  Concretely: one task box, an arrow fanning to N tool boxes (reuse
  `.grid-cards`-style small tiles), converging into a "Docker verification"
  node, converging into a "Leaderboard" node. Keep it to 4-5 tools max in the
  hero visual (Cline, Roo, Aider, Claude Code, raw-model) - the full driver
  list stays in the Drivers section below.
- CTAs unchanged: `Get started` (primary, -> quickstart), `View source`
  (outline, -> GitHub).

### 4.2 Why? (new section, right after hero)
Four short questions, each answered in one line - this is copy-paste-able
from the feedback doc almost verbatim:

> **Which coding agent should you use?**
> **Did switching models actually help?**
> **Did your prompt optimization make things better?**
> **Did your latest update regress performance?**
>
> OptArena answers these automatically - same task, same oracle, side by side.

Render as a 2x2 or 4-in-a-row card grid (reuse `.grid-cards`), each card =
one question + a 3-5 word answer, not a paragraph.

### 4.3 Leaderboard screenshot (new section, highest-priority visual asset)
A real screenshot of `optarena serve`'s dashboard after a genuine multi-tool
comparison - NOT a mockup. This is the single highest-value asset on the
whole site per the feedback doc. Requirements for the screenshot itself:
- Real run data (not placeholder/lorem numbers).
- Shows pass-rate bars/tiles for >= 2 drivers side by side.
- Dark theme (matches site default).
- Caption: one line naming the exact command that produced it, e.g.
  `optarena run --matrix-drivers cline-ui,aider,ollama-chat --matrix-models llama3.2`.
- Store at `website/screenshots/leaderboard.png` (or `.webp`), reference with
  a real `<img>`, not a background-image hack - screenshots must be visible
  with JS disabled and indexable by search/social crawlers (also serves as
  the Open Graph image).

### 4.4 How it works
Existing 6-step strip (Define / Configure / Drive / Judge / Record /
Compare) - keep as-is, move below the leaderboard screenshot (it's
explanation, not proof - proof goes first).

### 4.5 Real tools, really driven (Drivers)
Existing driver grid - keep, but re-caption to reinforce "evaluate software
engineering agents" (add one card explicitly for headless CLI agents as a
category, already partially done in the current site's "Terminal agents"
card).

### 4.6 Verified, not guessed (rename current "Features", promote Docker
verification to the top card)
Lead card must be **Docker-sandboxed real execution** framed as: "generated
code is compiled and run against real assertions in an isolated container -
not keyword matching." This is the second-strongest differentiator per the
feedback doc and currently under-emphasized. Order:
1. Docker-sandboxed real verification (new top billing)
2. UI-native testing
3. Comparison-first
4. Backend-agnostic
5. Zero infrastructure
6. Cheap extensibility
7. Private by default

### 4.7 Regression testing (new section)
Concrete, high-value use case from the feedback doc - model/tool upgrades:

```
$ optarena regression baseline-gpt5.5 baseline-gpt6

  accuracy   +4%
  cost       -18%
  runtime    +12%

  regressed cases:
    - login
    - oauth
```

Frame as "answer 'did the upgrade help?' in one command" - ties directly to
the `optarena regression` CLI command (Tier 2 implementation item).

### 4.8 Quick start
Existing section, unchanged content, just move `optarena docker build` into
the sequence (sandboxed verification needs it) and keep it short - 4-5
commands max, this is a teaser not full docs (link to docs.optarena.com for
the rest).

### 4.9 Footer
Unchanged (Trysti Labs / Trysti licensing footer, GitHub/docs links).

## 5. Content NOT to add yet (explicitly out of scope for this pass)

- Community `optarena-cases` repo promotion - don't reference it on the site
  until it exists (dead links/aspirational claims erode trust faster than
  they build it).
- Devin/OpenHands/browser-agent driver claims - only list drivers that exist
  in `optarena/drivers/__init__.py` today. The broadened "evaluate software
  engineering agents" positioning should read as true *today* (it is - CLI +
  UI + SDK + baseline already spans that), not as a roadmap promise.
- Pricing/enterprise language beyond the existing footer line - OptArena's
  site should stay developer-first and technical, matching selfoptai.com's
  tone.

## 6. Technical constraints (mirror selfopt's site exactly)

- Single static `index.html` + `style.css` + `logo.svg` (+ new
  `screenshots/` dir) - no build step, no framework, no external JS besides
  Google Fonts preconnect.
- Self-contained enough to open `index.html` directly from disk for review.
- `data-theme` + localStorage toggle logic must run before first paint
  (existing inline `<script>` in `<head>` - keep the pattern).
- Deployed via Cloudflare Worker to optarena.com; docs subdomain is
  docs.optarena.com (separate Docusaurus repo, unaffected by this brief).

## 7. Definition of done

- [ ] Hero leads with a real diagram (not a text wall) and an Arena-framed
      headline.
- [ ] "Why?" section exists, 4 questions, one line each.
- [ ] A real dashboard screenshot is embedded (not a mockup, not omitted).
- [ ] Docker-sandboxed real verification is the #1-billed feature card.
- [ ] A regression-testing section exists showing the accuracy/cost/runtime
      delta example.
- [ ] Zero emdashes, dark-default + light-toggle intact, same visual
      language as selfoptai.com.
