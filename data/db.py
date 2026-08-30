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


def fetch_ohlcv(symbol: str, exchange: str, hours: int) -> list[dict]:
    """Closed 1m candles for `symbol`/`exchange` from the last `hours` hours,
    oldest first."""
    query = """
        SELECT ts, open, high, low, close, volume
        FROM raw_ohlcv
        WHERE symbol = %s AND exchange = %s AND ts >= now() - (%s || ' hours')::interval
        ORDER BY ts ASC
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, (symbol, exchange, hours))
        rows = cur.fetchall()
    conn.close()
    return [
        {
            "ts": ts.isoformat(),
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": float(v),
        }
        for ts, o, h, l, c, v in rows
    ]


def fetch_gex_profile_history(symbol: str, exchange: str, hours: int) -> list[dict]:
    """Full-chain GEX profile snapshots (feature_gex_profile_snapshot, 24h
    retention — NOT feature_gex_snapshot's top-10) from the last `hours`
    hours, oldest first. Each row already carries its strikes as a JSONB
    list (see deribit.fetch_gex_profile_snapshot_row), so this just reshapes
    it to the {ts, spot_price, strikes} shape the GEX Interval Map expects."""
    query = """
        SELECT ts, spot_price, profile
        FROM feature_gex_profile_snapshot
        WHERE symbol = %s AND exchange = %s AND ts >= now() - (%s || ' hours')::interval
        ORDER BY ts ASC
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, (symbol, exchange, hours))
        rows = cur.fetchall()
    conn.close()
    return [
        {"ts": ts.isoformat(), "spot_price": float(spot), "strikes": profile}
        for ts, spot, profile in rows
    ]


def prune_gex_profile_history(older_than_hours: int = 24) -> None:
    """Manual stand-in for add_retention_policy(), which needs the
    (non-Apache) Timescale license managed instances like Aiven's don't
    ship — see the comment on feature_gex_profile_snapshot in schema.sql.
    drop_chunks() removes whole chunks (metadata-only) rather than deleting
    rows one at a time, so this stays cheap even called hourly."""
    query = "SELECT drop_chunks('feature_gex_profile_snapshot', older_than => (%s || ' hours')::interval)"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, (older_than_hours,))
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
