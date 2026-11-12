import os
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from fastapi.testclient import TestClient


def _test_database_url():
    source = os.environ["DATABASE_URL"]
    parsed = urlsplit(source)
    test_db = "checkstock_test"
    admin = urlunsplit(parsed._replace(path="/postgres"))
    with psycopg.connect(admin, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (test_db,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{test_db}"')
    return urlunsplit(parsed._replace(path="/" + test_db))


os.environ["DATABASE_URL"] = _test_database_url()


@pytest.fixture()
def client():
    from app import db
    from app.main import app

    with db.connect() as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")

    with TestClient(app) as test_client:
        yield test_client
