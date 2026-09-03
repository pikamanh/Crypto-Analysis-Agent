"""Connection + batch-insert helpers for the raw ingest tables."""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

import psycopg2
from psycopg2 import pool as _pg_pool
from psycopg2.extras import execute_values

DATABASE_URL = os.environ["DATABASE_URL"]

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Aiven requires sslmode=require, so every fresh psycopg2.connect() pays a
# real TCP+TLS handshake — noticeable both on user-facing dashboard requests
# and on the ~60s ingest ticks that used to open-and-close a connection each
# time. A small pool keeps a handful of connections warm instead, which is
# also lighter on RAM than repeatedly spinning up new connection objects.
# ThreadedConnectionPool because ingest ticks run via asyncio.to_thread, so
# multiple threads can be checking connections in/out concurrently.
_POOL: _pg_pool.ThreadedConnectionPool | None = None
_POOL_MIN = 1
_POOL_MAX = 5


def _get_pool() -> _pg_pool.ThreadedConnectionPool:
    global _POOL
    if _POOL is None:
        _POOL = _pg_pool.ThreadedConnectionPool(_POOL_MIN, _POOL_MAX, DATABASE_URL)
    return _POOL


@contextmanager
def get_conn():
    """Checks out a pooled connection rather than opening a new one.
    Mirrors the commit-on-success/rollback-on-exception behavior callers
    used to get from `with psycopg2.connect(...) as conn:`, then always
    returns the connection to the pool (never closes it) on exit."""
    conn = _get_pool().getconn()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        _get_pool().putconn(conn)


def init_db() -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(_SCHEMA_PATH.read_text())


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
    return [
        {"ts": ts.isoformat(), "spot_price": float(spot), "strikes": profile}
        for ts, spot, profile in rows
    ]


def fetch_iv_history(symbol: str, exchange: str, hours: int) -> list[dict]:
    """30d implied-vol history for `symbol`/`exchange` from the last `hours`
    hours, oldest first. Pulled from feature_gex_snapshot (one row per ~60s
    ingest tick, kept indefinitely — unlike feature_gex_profile_snapshot,
    this table has no retention/pruning), so unlike the GEX Interval Map
    this doesn't need a client-side accumulator to cover ongoing history —
    the DB already has it all."""
    query = """
        SELECT ts, iv
        FROM feature_gex_snapshot
        WHERE symbol = %s AND exchange = %s AND ts >= now() - (%s || ' hours')::interval
        ORDER BY ts ASC
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, (symbol, exchange, hours))
        rows = cur.fetchall()
    return [{"ts": ts.isoformat(), "iv": float(iv) if iv is not None else None} for ts, iv in rows]


def fetch_latest_ingest_ts(symbol: str, exchange: str) -> datetime | None:
    """Latest feature_gex_snapshot row for `symbol`/`exchange`. That table
    gets a write every ingest tick unconditionally (unlike raw_ohlcv, which
    only grows when the upstream actually has a new candle to offer), so
    its age is the most reliable canary for "is the poll loop actually
    still running" — see /api/ingest/freshness in api/app.py. Returns None
    if the stream has never ingested a row."""
    query = "SELECT max(ts) FROM feature_gex_snapshot WHERE symbol = %s AND exchange = %s"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, (symbol, exchange))
        (ts,) = cur.fetchone()
    return ts


def prune_gex_profile_history(older_than_hours: int = 24) -> None:
    """Manual stand-in for add_retention_policy(), which needs the
    (non-Apache) Timescale license managed instances like Aiven's don't
    ship — see the comment on feature_gex_profile_snapshot in schema.sql.
    drop_chunks() removes whole chunks (metadata-only) rather than deleting
    rows one at a time, so this stays cheap even called hourly."""
    query = "SELECT drop_chunks('feature_gex_profile_snapshot', older_than => (%s || ' hours')::interval)"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, (older_than_hours,))


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
    return len(rows)
