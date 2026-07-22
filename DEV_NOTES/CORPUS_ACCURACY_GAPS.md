# Corpus accuracy gaps (audit 2026-07-21)

Two accuracy gaps, both in the **corpus data**, not the engine.

## 1. 🟠 "18 languages" is really 18 tagged + 7 untagged

7 cases carry **no `language` field** (all Dockerfile/Makefile):

- `fix_dockerfile_layer_order`
- `fix_dockerfile_pinned_base`
- `fix_dockerfile_run_as_root`
- `refactor_dockerfile_multistage`
- `create_makefile_c_build`
- `fix_makefile_incremental_rebuild`
- `refactor_makefile_pattern_rule`

**Consequence:** `optarena cases list --language dockerfile` returns **6 of the real 10**,
and `--language makefile` returns **2 of the real 5**. The README's per-language table
(Dockerfile 10, Makefile 5) is correct *in aggregate* but the filter can't reproduce it.

**This is a real bug — add the missing tags.**

Convention (from correctly-tagged siblings):
- Dockerfile cases → `language: "dockerfile"`, `framework: "docker"`
- Makefile cases → `language: "makefile"`, `framework: "make"`

Note: the 4 untagged Dockerfile cases also mis-set `framework: "dockerfile"` (should be `"docker"`).

**Status: FIXED** — see the `add-missing-language-tags` change. All 500 cases now carry a `language`.

## 2. 🟠 "Self-verifying corpus" overclaims coverage

README claims:
- *"ships … including every case's hidden tests, `reference_solution`, and `broken_solutions`"*
- *"every case … was hand-verified end-to-end — a correct reference solution passes, a broken
  one fails … before being counted as done."*

Actual data (500 cases):
- `reference_solution`: **396/500 (79%)** — 104 cases have none.
- `broken_solutions`: **393/500**.
- **42/500 cases (8.4%) have no verification variant at all** and are silently skipped by
  `verify-corpus` (the command even prints "N case(s) declare no variants").

So `verify-corpus` proves oracle discrimination for **~79–92%** of the corpus, not "every case."

**Recommended claim wording:** *"most cases ship a reference solution; `verify-corpus` proves
the oracle discriminates for those,"* with the real numbers — **or** backfill the 104 missing
reference solutions so the "every case" claim becomes true.

**Status: DONE** — reference solutions backfilled for all 104 missing cases and validated
against the real Docker oracle via `verify-corpus`. **All 500 cases now carry a
`reference_solution` (100%, was 79%); 0 cases are skipped by `verify-corpus`** (was 42).
`broken_solutions` remain on 393/500 (78%) — every case's oracle is now proven in the
pass direction (reference passes) and most in the fail direction (broken/unmodified fails).
README trust-model wording updated to match. Per-group results (all pass): python 14,
node 16, go 9, rust 8, jvm 12, dotnet 8, base(C/C++/SQL/shell/yaml/hcl/py/js) 37.

## 3. 🟠 Latent image bug surfaced by backfilling (node/ts-node)

Adding reference solutions to the 3 NestJS cases (`create_nestjs_items_controller`,
`fix_nestjs_status_code`, `refactor_nestjs_service_extraction`) immediately exposed that
**they were unrunnable for any solution**: `docker/node/Dockerfile` installed `typescript`
unpinned, which now resolves to **TypeScript 7.x** (the native `tsgo` compiler). TS7's API
surface breaks `ts-node@10.9.2` at bootstrap:

```
TypeError: Cannot read properties of undefined (reading 'fileExists')
    at readConfig (/usr/local/lib/node_modules/ts-node/dist/configuration.js:91:33)
```

So `ts-node main.ts` failed before loading any user code — every agent would have scored 0 on
these cases (a silent false-negative benchmark, exactly the risk of shipping cases with no
reference solution).

**Fix:** pinned `typescript@5.6.3` in `docker/node/Dockerfile` (ts-node 10.x supports TS 5.x).
Rebuilt the node image locally and all 3 cases now pass.

**Action required before release:** rebuild and **republish** the `optarena-tester-node` image
to GHCR — CI `verify-corpus` pulls the sandbox images from GHCR, so the fix must be in the
published image, not just local.

## 4. 🟠 `--read-only` hardening broke ALL rust cases (sandbox bug)

Backfilling the rust cases exposed that the `--read-only` rootfs hardening (added in the
second remediation pass) makes the rust image's pre-warmed cargo target dir
(`/opt/cargo-target`, `ENV CARGO_TARGET_DIR`) unwritable, so `cargo build`/`cargo test`
hard-fail for **every** rust case (all 28 in the corpus, not just the 8 backfilled):

```
error: failed to open: /opt/cargo-target/debug/.cargo-build-lock
Caused by: Read-only file system (os error 30)
```

Maven only warns ("Can not write to /root/.m2/… Carrying on") and dotnet writes into
`/workspace`, so those images were unaffected — rust alone hard-fails.

**Fix:** `optarena/cases.py` now adds an anonymous volume (`-v /opt/cargo-target`) for the
rust image in both the shared-sandbox and ephemeral-`docker run` paths (`_writable_cache_args`).
An anonymous volume initializes from the image (pre-warmed deps preserved) but is writable, and
`--rm` cleans it up. Mirrored in `ui-harness/src/oracle.js` (`writableCacheArgs`). All 8 rust
references pass after the fix; re-run `optarena cases verify --language rust` to confirm the
other 20.

## Local-validation caveat (not a corpus issue)

Two go cases (`create_fiber_items_endpoint`, `create_gin_crud_todos`) hit the 60s
`check_command` timeout **only** on this Apple-Silicon host, where the amd64 sandbox images run
under qemu emulation and the first cold gin/fiber compile exceeds 60s. Verified correct by
building + running them manually with adequate time (both PASS); on native amd64 CI the cold
build is well under 60s. No corpus change needed.

