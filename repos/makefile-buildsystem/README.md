# makefile-buildsystem

A small multi-module C project built via a single top-level Makefile:
`src/core` (calculator functions), `src/utils` (string helpers), and
`src/cli` (the entry point), each picked up via `$(wildcard ...)` rather
than a hardcoded file list, plus a `test` target that compiles and runs
`tests/test_calc.c` directly against `core`/`utils` sources.

Cases are checked with `python3 test_X.py` scripts that run REAL `make`
subprocess calls (`make all`, `make test`, etc.) - no mocking.
