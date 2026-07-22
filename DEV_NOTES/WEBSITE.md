# WEBSITE.md - optarena.com design & content brief

Working brief for the marketing site, now built as a React/Vite app at
`website2/` (deployed as optarena.com via Cloudflare Worker; supersedes the
static `website/` v1). Written from `OptArena_README_Feedback.md`'s
positioning critique, then sharpened by a second round of feedback pushing
harder on the Arena metaphor - use this to keep future redesigns consistent
without re-deriving the reasoning each time.

## 1. Positioning (read this before writing any copy)

**Old framing:** "a testing framework for AI coding tools," then briefly
"the reproducible evaluation platform for AI coding agents" - both correct
but generic-infrastructure-sounding and, critically, neither one explains
why the product is called **Arena**. The name promises competition,
leaderboards, and a declared winner; the copy has to deliver on that
promise immediately, not as an aside.

**Current framing:** an **arena** - competition, leaderboards, proving a
winner. This is not just a tagline choice; it changes what the hero
*shows*, not just what it *says* - see section 4.1.

> OptArena runs the same task through multiple coding agents, verifies the
> result by actually compiling and running it, and tells you who won.

**Decided hero headline (as shipped):**
- H1: "The arena where **AI coding agents compete.**" (accent span on the
  second line, kept to `white-space: nowrap` so it never breaks mid-phrase)
- Subhead: "Run the same real-world coding task through multiple AI coding
  agents, verify every result by actually executing it inside Docker, and
  see which one wins - accuracy, cost, and speed, side by side."
- This exact headline is now the single canonical tagline everywhere
  (README.md, docs intro, docusaurus.config.js tagline/meta) - don't let
  another surface drift to a different phrasing again; that drift is
  exactly what prompted this rewrite (the site had shipped "the evaluation
  platform for AI coding agents" while README/docs already said "the arena
  where... compete").

Retired tagline candidates (kept for social/OG copy variety only, not the hero):
- "Stop guessing which AI coding tool is better. Run them against each other."
- "Real tools. Real tests. Real verdicts."
- "The proving ground for AI coding agents." (mixes metaphors with "arena" - avoid pairing both in the same breath)

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

### 4.1 Hero (as shipped in `website2/src/components/Hero.jsx` + `Pipeline.jsx`)
- Arena-framed headline + tightened one-line sub (see decided copy above).
- **A live animated pipeline, not a static diagram, as the first thing below
  the fold** - four equal-size stage cards (Task -> Agents -> Verify ->
  Leaderboard) that cycle through an active/running/done state on a timer,
  connected by flowing dashed arrows. Kept to exactly 4 agents in both the
  "Agents" stage and the "Leaderboard" stage (Cline, Roo Code, Claude Code,
  Aider) - a mismatched count between the two stages reads as sloppy, not
  as "there are more than shown."
- **Labeled "Inside the arena"** directly above the pipeline card (a small
  mono/uppercase eyebrow, same style as every section's eyebrow) - this is
  the single highest-leverage place to make the metaphor visible, since
  it's the first proof the visitor sees, immediately under the headline
  that just used the word.
- **Gold/silver/bronze rank badges** (`.rank-badge.rank-1/2/3/4`, a shared
  class also used on the main Leaderboard table's top 3 rows when sorted by
  pass rate) on the Leaderboard stage's four rows - not emoji medals, a
  small CSS gradient badge matching the site's premium/custom-built
  aesthetic. This is the second-highest-leverage arena cue: it appears in
  the hero AND in the full leaderboard section, so a visitor sees the same
  "ranking" visual language twice, reinforcing rather than introducing a
  new motif each time.
- CTAs: `Get started` (primary, -> docs), `GitHub` (outline).

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
