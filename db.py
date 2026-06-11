"""Postgres connection helper. Azure-ready: connection comes from settings/env.

Defaults match the dev VM's system Postgres (localhost:5432, db/role `plm`).
Override with `PG_CONN` (or PG_HOST/PG_PORT/PG_DB/PG_USER/PG_PASSWORD), e.g. to
point at the Azure Postgres.
"""
from __future__ import annotations

import os

import psycopg


def pg_conninfo() -> str:
    if os.environ.get("PG_CONN"):
        return os.environ["PG_CONN"]
    host = os.environ.get("PG_HOST", "localhost")
    port = os.environ.get("PG_PORT", "5432")
    db = os.environ.get("PG_DB", "plm")
    user = os.environ.get("PG_USER", "plm")
    pw = os.environ.get("PG_PASSWORD", "plm_local_pw")
    return f"host={host} port={port} dbname={db} user={user} password={pw}"


def connect():
    return psycopg.connect(pg_conninfo())
