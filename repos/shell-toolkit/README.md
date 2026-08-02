# shell-toolkit

A small multi-script ops toolkit: `deploy.sh` sources three helper
libraries under `lib/` (`logging.sh`, `validate.sh`, `backup.sh`) rather
than being one big monolithic script.

Cases are checked with `python3 check_X.py` scripts wrapping real
`subprocess.run(["bash", ...])` calls - no `bc` in the sandbox image, so
any arithmetic must use bash's native integer arithmetic.
