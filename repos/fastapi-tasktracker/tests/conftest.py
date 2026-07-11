import os
import tempfile
from pathlib import Path

import pytest

# Must be set before `app.db.session` (and therefore `app.core.config`) is
# imported anywhere, so the app talks to a throwaway file DB for this test
# session instead of the dev default (./tasktracker.db).
_tmp_dir = tempfile.mkdtemp(prefix="tasktracker_test_")
os.environ["TASKTRACKER_DATABASE_URL"] = f"sqlite:///{Path(_tmp_dir, 'test.db').as_posix()}"

from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_db():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def client():
    return TestClient(app)
