"""
optarena/_cases/
─────────────────
P3-01: internal implementation of the case engine, split by concern out of
what used to be one 2000+ line `optarena/cases.py`. Not part of the public
import surface - every name here is re-exported through `optarena/cases.py`,
which is what every other module (drivers, cli, runner, verify, tests)
imports from and keeps doing so unchanged.

Layout, in dependency order (each only imports from ones above it):
  _constants.py        shared paths/registries with no other dependencies
  _corpus.py            case-file loading/filtering/validation
  _snapshot.py           workspace content hashing + the assertion oracle
  _sandbox.py            container engine, DockerSandbox, check_command exec
  _workspace_setup.py    setup_files/setup_repo/git_init/disruptions
  _evaluate.py            ties check_expected + run_check_command together

Named with a leading underscore - not `cases/` - specifically because
`optarena/cases/` already exists on disk as the JSON case-corpus data
directory (`CASES_DIR`); a package here would collide with it.
"""
