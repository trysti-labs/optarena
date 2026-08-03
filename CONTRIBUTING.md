# Contributing to OptArena

## Setup

```bash
git clone https://github.com/trysti-labs/optarena.git
cd optarena
pip install -e .
```

The core is stdlib-only - nothing else to install for the CLI, baseline
drivers, or case-content oracle logic. If you're working on a specific SDK
driver, install its extra too (`pip install -e ".[crewai]"`, etc. - see
`pyproject.toml` for the full list).

Docker or Podman is needed for anything touching real `check_command`
verification (most of the corpus) - auto-detected, or force one with
`OPTARENA_CONTAINER_ENGINE=docker`/`podman`. `optarena sandbox pull --all`
fetches the published images (minutes); `optarena sandbox build --all`
builds them locally (~30 min cold, mostly cached after).

## Running the checks CI runs

```bash
ruff check optarena tests                              # lint gate
OPTARENA_DISABLE_SANDBOX=1 python -m unittest discover tests -v   # unit tests
python -m optarena cases verify                         # corpus self-verification
```

The unit suite runs with the container sandbox deliberately disabled
(`OPTARENA_DISABLE_SANDBOX=1`) so it doesn't depend on a local Docker/Podman
daemon; corpus verification does need a container engine, since it replays
every case's `reference_solution` and `broken_solutions` through the real
sandboxed oracle.

## Adding a driver

One file in `optarena/drivers/` implementing
`run_case(case, scenario, workspace) -> CaseResult`, plus one entry in the
registry (`optarena/drivers/__init__.py`). See
[Drivers](https://docs.optarena.com/concepts/drivers) for the SDK-driver
pattern (no file tools, single-code-block extraction) if you're adding an
agent-framework driver rather than a CLI one.

## Adding a case

Add one JSON file to `optarena/cases/`. A prompt without a real test is a
weak case - write a `check_command` (with `test_setup_files` for a custom
test script) alongside every prompt, not just `content_patterns`. See
[Cases and the oracle](https://docs.optarena.com/concepts/cases-and-oracle)
for the full schema, and `optarena cases verify` to prove your case's
`reference_solution` passes and any `broken_solutions` fail before opening a
PR.

## Architecture

[ARCH.md](./ARCH.md) is the reference for how the system fits together - the
domain model, every component, the contracts between them, and the
cross-platform gotchas already found the hard way (worth a skim before
touching `subprocess_env()`, the container sandbox, or anything Windows-specific).

## Security

Found something that looks like a real vulnerability rather than a bug?
Please don't open a public issue for it - see [SECURITY.md](./SECURITY.md)
for how to report it and what OptArena's actual trust model is.

## Governance

See [GOVERNANCE.md](./GOVERNANCE.md) for who maintains this project, how
decisions get made, and the deprecation/compatibility policy for the case
schema, drivers, results, and oracle versions - worth a read before proposing
a breaking change.

## Pull requests

- Keep the change scoped - a bug fix doesn't need a drive-by refactor.
- Run the three checks above before opening the PR; CI runs the same ones.
- If you're touching a case, `optarena cases verify` output showing your new
  case's variants passing/failing as expected is the fastest way to confirm
  it's correct.
