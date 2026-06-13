"""Shared sync DB helper used by seed scripts and partition maintenance.

Reads POSTGRES_* env vars and passes them to psycopg.connect() as keyword
arguments. This avoids URL-encoding pitfalls when the password contains
characters like %, &, @, or # that have special meaning in a libpq URI.
"""
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


@contextmanager
def conn():
    user = os.environ.get("POSTGRES_USER")
    password = os.environ.get("POSTGRES_PASSWORD")
    dbname = os.environ.get("POSTGRES_DB")
    if not (user and password and dbname):
        raise RuntimeError(
            "POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB must be set in .env"
        )

    # Scripts run on the Windows host; the DB container publishes 5432 on
    # localhost. POSTGRES_HOST/PORT overrides are accepted for flexibility.
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))

    with psycopg.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=dbname,
        autocommit=True,
    ) as c:
        yield c
