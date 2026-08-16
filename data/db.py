"""Connection + batch-insert helpers for the raw ingest tables."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Sequence

import psycopg2
from psycopg2.extras import execute_values

DATABASE_URL = os.environ["DATABASE_URL"]

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db() -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(_SCHEMA_PATH.read_text())
    conn.close()


def insert_rows(
    table: str,
    columns: Sequence[str],
    rows: Iterable[Sequence],
    on_conflict: bool = True,
) -> int:
    """Bulk insert. Rows must match `columns` order. No-op on empty input.

    `on_conflict` skips duplicates on the table's primary key — set to False
    for tables with no PK (e.g. raw_liquidations, which is event-only).
    """
    rows = list(rows)
    if not rows:
        return 0
    cols_sql = ", ".join(columns)
    query = f"INSERT INTO {table} ({cols_sql}) VALUES %s"
    if on_conflict:
        query += " ON CONFLICT DO NOTHING"
    with get_conn() as conn, conn.cursor() as cur:
        execute_values(cur, query, rows)
    conn.close()
    return len(rows)
