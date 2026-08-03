# Governance

OptArena is solo-maintained today. This document says so plainly rather than
implying a team or a board that doesn't exist - and states the concrete
policies that *are* real: who decides what, how a security report gets
handled, which branches are supported, and what changes require a version
bump versus what's safe to ship silently.

## Maintainers

- **Arun** ([arun@trysti.com](mailto:arun@trysti.com)) - sole maintainer:
  reviews and merges every pull request, triages every issue, and makes final
  calls on scope, design, and releases.

There is no maintainer team, steering committee, or voting process. If that
changes (a second maintainer is added, a team forms), this file is where
that gets recorded - not assumed elsewhere.

## Decision process

- **Day-to-day changes** (bug fixes, new cases, new drivers, docs): a pull
  request, reviewed and merged by the maintainer. No formal proposal process
  - open the PR, explain the "why" in the description, and the [pull request
    template](.github/PULL_REQUEST_TEMPLATE.md)/[CONTRIBUTING.md](./CONTRIBUTING.md)
  cover the mechanics.
- **Larger or breaking changes** (a case-schema field removed, a driver's
  contract changed, an oracle-semantics change): open an issue first
  ([feature request template](.github/ISSUE_TEMPLATE/feature_request.md))
  describing the change and its impact before sending a PR - this is a
  solo-maintainer project, so "discuss first" mostly exists to avoid wasted
  work on a PR that gets redesigned in review, not to route around a single
  decision-maker.
- Disagreements are the maintainer's call. There is no appeals process today
  - a contributor who disagrees is always free to fork (Apache-2.0).

## Security response ownership

Handled entirely by the maintainer. See [SECURITY.md](./SECURITY.md) for how
to report a vulnerability, the acknowledgment timeline, and the project's
actual trust-boundary model - not duplicated here to avoid the two files
drifting out of sync.

## Support channels

- **Bugs and feature requests**: GitHub Issues, via the
  [bug report](.github/ISSUE_TEMPLATE/bug_report.md) /
  [feature request](.github/ISSUE_TEMPLATE/feature_request.md) templates.
- **Security reports**: email, per [SECURITY.md](./SECURITY.md) - not a
  public issue.
- **Nothing else exists today** - no Discord/Slack, no mailing list, no paid
  support tier. If one of these gets stood up later, this section is where
  it gets added; until then, don't assume a channel this file doesn't list.

## Supported release branches

- **`main`** is the primary development branch and the one new
  contributions should target. It was rebuilt for the `v0.1.0` tag from
  `v0.1`'s history (146 commits compressed into a clean, reviewable ~27,
  re-authored under one consistent identity - the working history behind
  that compression, including the account it was previously authored
  under and the external-review commits it absorbed, isn't preserved
  as-is; the code and this document are the record going forward).
- **`v0.1`** was the working branch for everything up to the `v0.1.0`
  release and is no longer the one to target - `main` supersedes it as of
  this tag.
- OptArena is pre-1.0 (`Development Status :: 4 - Beta`). There is no
  guarantee of behavioral compatibility between `0.x` releases yet - see
  "Versioning and compatibility" below for what *is* guaranteed even at this
  stage (the oracle/manifest/result version fields), and what isn't (CLI
  flags, case JSON shape, driver registry keys - all can still change with a
  `CHANGELOG.md` entry rather than a deprecation cycle before `1.0`).
- Once `1.0` ships, this section will define real supported-branch windows
  (e.g., "the current and previous minor release get security fixes") -
  not written yet because there is only one release line to support today,
  and a policy for branches that don't exist yet would be speculative.

## Versioning and compatibility

Four independent things can each change version, on their own schedule -
conflating them (as "the project version") would hide exactly the kind of
silent-incompatibility problem this policy exists to prevent:

- **`optarena_version`** (`pyproject.toml`'s `version`, `optarena
  --version`) - the tool's own release version. Bumped on every release;
  follows no strict semver contract yet (pre-1.0). Recorded in every run
  manifest for evidence, but deliberately does **not** gate run comparability
  - a patch release that doesn't touch the oracle must not invalidate
  existing comparisons.
- **`ORACLE_VERSION`** (`optarena/runner/_manifest.py`) - what a pass/fail
  *means*. This is the one field `compare.manifest_compatibility` hard-gates
  on: two runs with different `oracle_version` are flagged non-comparable
  unless the caller explicitly forces the comparison. `_manifest.py`'s own
  docstring is the authoritative, current decision procedure for when this
  bumps (scoring-logic changes, sandbox-contract changes that can flip a
  verdict) versus when it doesn't (schema hardening that only rejects
  already-invalid cases, logging/redaction changes, additive fields) - read
  it there rather than here, so there is exactly one place this policy can
  drift out of date.
- **`manifest_version`** (`optarena/runner/_manifest.py`) - the *shape* of
  the manifest dict itself (which keys exist). Bump when a key is removed or
  its meaning changes incompatibly; adding a new key is not a breaking
  change and does not require a bump (every manifest consumer in this
  codebase already treats missing keys as "not recorded," not an error).
- **Case schema** (`optarena/schema.py`'s `validate_case`/`validate_scenario`)
  has no separate version number today - there has been no breaking case- or
  scenario-schema change yet, so none was needed. The real, current
  enforcement mechanism is `validate_case`/`validate_scenario` rejecting
  unknown keys outright, which makes a case authored against a newer schema
  fail loudly and immediately on an older `optarena`, rather than silently
  misbehaving. If a genuinely breaking case-schema change ships (a required
  field renamed, a field's accepted values narrowed in an incompatible way),
  it must add an explicit `case_schema_version`-style field at that point,
  not retroactively - this paragraph is the commitment to do that when the
  day comes, not a claim it already exists.

## Driver deprecation and compatibility

- Every driver's support tier is `DRIVERS[...]["status"]`
  (`optarena/drivers/__init__.py`): `stable` (exercised by the project's own
  regular use and covered by scheduled CI - see `integration-smoke.yml`),
  `experimental` (best-effort, may lag upstream), or `optional` (an SDK
  driver behind its own pip extra). `optarena drivers list` and `optarena
  doctor` both surface this, so a driver's support level is visible before
  you depend on it, not discovered after.
- `DRIVERS[...]["tested_with"]` records the last version each driver was
  confirmed working against via a real invocation; `doctor` flags drift
  between that and the installed version (advisory only for local/
  interactive use), and `integration-smoke.yml`'s scheduled CI hard-fails
  on that same drift signal (via `doctor --json`) for the drivers it
  covers - a local run staying advisory while unattended CI gates on it is
  deliberate, not an inconsistency.
- Removing a driver entirely: announce it in `CHANGELOG.md` under
  `Deprecated` for at least one release before deletion, unless the removal
  is forced by an upstream tool disappearing entirely (nothing to keep
  supporting in that case).

## Result-format compatibility

- A saved run record's on-disk JSON shape can gain new keys freely (every
  reader in this codebase treats an absent key as "not recorded," the same
  convention as `manifest_version` above) but should not remove or repurpose
  an existing key without a `CHANGELOG.md` entry - `optarena runs
  rebuild-index`/`store.py`'s loaders are the two places that would need to
  handle an old-shape record if one ever needs an actual migration, which
  hasn't happened yet.

## Deprecation policy summary

Until `1.0`: a breaking change to the CLI, case schema, driver registry
keys, or result format ships with a `CHANGELOG.md` entry (not a formal
multi-release deprecation cycle - pre-1.0 software moving fast is the
expected trade-off, matching the `Development Status :: 4 - Beta`
classifier). `ORACLE_VERSION` is the one exception: it is versioned and
gated *today*, pre-1.0, because an oracle-semantics change silently
corrupting a comparison is a correctness bug, not a compatibility
inconvenience - the two are not treated the same way on purpose.
