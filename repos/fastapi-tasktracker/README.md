# fastapi-tasktracker

Shared L3 (repo-scale) starter app for OptArena: a small multi-user task
tracker on FastAPI + SQLAlchemy 2.0 + Alembic, tested with an in-process
`TestClient` against a file-based SQLite database.

```
app/
  core/config.py     Settings (DB URL from env)
  db/session.py       Base, engine, SessionLocal, get_db dependency
  models/             User, Project, Task (SQLAlchemy ORM)
  schemas/            Pydantic request/response models
  crud/               DB-access functions per entity
  services/           Pure domain helpers (no DB access)
  routers/            FastAPI routers per entity
  main.py             App wiring
alembic/               Migration matching the current models (0001_initial)
tests/                 conftest.py (per-test DB reset + TestClient) + suite
```

Run the suite from the repo root: `python -m pytest tests/ -q`.

This repo is copied verbatim into a case's workspace by OptArena's
`setup_repo` schema field (see `optarena/cases.py:copy_setup_repo`) - it is
not embedded in any case JSON. Individual L3 cases layer a small
`setup_files` diff on top (e.g. introducing the gap the task asks the model
to close) and a hidden `test_setup_files` test dropped into `tests/` that
`check_command` runs alongside this suite.
