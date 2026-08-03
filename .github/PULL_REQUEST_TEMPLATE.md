## What & why

<!-- What does this change, and why? Link an issue if there is one. -->

## Checks run locally

- [ ] `ruff check optarena tests`
- [ ] `OPTARENA_DISABLE_SANDBOX=1 python -m unittest discover tests -v`
- [ ] `python -m optarena cases verify` (only if you touched a case, the
      oracle, or a Dockerfile)

## Notes for the reviewer

<!-- Anything non-obvious: a tradeoff you made, an alternative you
considered and rejected, a follow-up you're deliberately leaving out of
scope. -->
