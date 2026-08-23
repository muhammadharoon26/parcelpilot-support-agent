"""Point the tests at a throwaway copy of the database so escalation writes never touch the real one."""

import shutil

import pytest

from app import db
from app.config import SQLITE_PATH


@pytest.fixture(autouse=True, scope="session")
def temp_database(tmp_path_factory):
    if not SQLITE_PATH.exists():
        pytest.skip("Run scripts/load_structured_data.py before the tests")
    copy = tmp_path_factory.mktemp("data") / "app.sqlite"
    shutil.copy(SQLITE_PATH, copy)
    db.SQLITE_PATH = copy
    yield
    db.SQLITE_PATH = SQLITE_PATH
