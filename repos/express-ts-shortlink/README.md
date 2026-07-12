# express-ts-shortlink

Shared L3 (repo-scale) starter app for OptArena: a link shortener with click
tracking, in TypeScript on Express 4, tested in-process (portless: the app
listens on port 0 and tests use the global fetch).

```
src/
  config.ts             Limits and defaults (slug length, top-list bounds)
  models/types.ts       Link and stats interfaces
  store/linkStore.ts    In-memory repository (links + click timestamps)
  services/slugService.ts   Slug validation + random slug generation
  services/statsService.ts  Pure click-timestamp aggregation
  routes/               links CRUD, /r/:slug redirect, per-link stats
  app.ts                Express wiring (no listen)
  server.ts             Listen entry point
types/                  Local ambient typings (the sandbox resolves the
                        express PACKAGE via NODE_PATH at runtime, but tsc
                        does not use NODE_PATH - so minimal .d.ts files for
                        express and the node globals we touch live here)
tests/                  Plain-JS node:test suites against the compiled dist/
```

Build and test from the repo root:

```
tsc -p . && node --test
```

(`node --test` auto-discovers every `*.test.js` under the repo root.)

The `tsc` step is part of the oracle for every case on this repo - changes
must compile under `strict` before any behavior is checked.

This repo is copied verbatim into a case's workspace by OptArena's
`setup_repo` schema field (see `optarena/cases.py:copy_setup_repo`).
Individual L3 cases overlay `setup_files` and drop hidden `tests/*.test.js`
suites via `test_setup_files`.
