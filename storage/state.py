import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel

DB_PATH = os.getenv("STATE_DB_PATH", os.path.join(os.path.dirname(__file__), "trading_state.db"))


class Position(BaseModel):
    id: int
    symbol: str
    side: Literal["long", "short"]
    quantity: float
    entry_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    entry_order_id: Optional[str] = None
    status: Literal["open", "closed"]
    opened_at: str
    closed_at: Optional[str] = None
    exit_price: Optional[float] = None
    realized_pnl: Optional[float] = None


class Order(BaseModel):
    id: int
    position_id: Optional[int] = None
    symbol: str
    side: str
    order_type: str
    purpose: str
    quantity: Optional[float] = None
    price: Optional[float] = None
    status: Optional[str] = None
    exchange_order_id: Optional[str] = None
    created_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL CHECK (side IN ('long', 'short')),
                quantity REAL NOT NULL,
                entry_price REAL NOT NULL,
                stop_loss REAL,
                take_profit REAL,
                entry_order_id TEXT,
                status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                exit_price REAL,
                realized_pnl REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER REFERENCES positions(id),
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                purpose TEXT NOT NULL,
                quantity REAL,
                price REAL,
                status TEXT,
                exchange_order_id TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol)")


def open_position(
    symbol: str,
    side: Literal["long", "short"],
    quantity: float,
    entry_price: float,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    entry_order_id: Optional[str] = None,
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO positions
                (symbol, side, quantity, entry_price, stop_loss, take_profit,
                 entry_order_id, status, opened_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            (symbol, side, quantity, entry_price, stop_loss, take_profit, entry_order_id, _now()),
        )
        return cursor.lastrowid


def close_position(position_id: int, exit_price: float, realized_pnl: float) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE positions
            SET status = 'closed', closed_at = ?, exit_price = ?, realized_pnl = ?
            WHERE id = ?
            """,
            (_now(), exit_price, realized_pnl, position_id),
        )


def get_open_positions(symbol: Optional[str] = None) -> List[Position]:
    with get_connection() as conn:
        if symbol:
            rows = conn.execute(
                "SELECT * FROM positions WHERE status = 'open' AND symbol = ?", (symbol,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM positions WHERE status = 'open'").fetchall()
        return [Position(**dict(row)) for row in rows]


def get_open_position_count() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM positions WHERE status = 'open'").fetchone()
        return row["n"]


def has_open_position(symbol: str) -> bool:
    return len(get_open_positions(symbol=symbol)) > 0


def record_order(
    symbol: str,
    side: str,
    order_type: str,
    purpose: str,
    position_id: Optional[int] = None,
    quantity: Optional[float] = None,
    price: Optional[float] = None,
    status: Optional[str] = None,
    exchange_order_id: Optional[str] = None,
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO orders
                (position_id, symbol, side, order_type, purpose, quantity, price,
                 status, exchange_order_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (position_id, symbol, side, order_type, purpose, quantity, price, status, exchange_order_id, _now()),
        )
        return cursor.lastrowid


def get_daily_realized_pnl(date: Optional[str] = None) -> float:
    """Sum of realized_pnl for positions closed on `date` (UTC, YYYY-MM-DD). Defaults to today."""
    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(realized_pnl), 0) AS pnl
            FROM positions
            WHERE status = 'closed' AND substr(closed_at, 1, 10) = ?
            """,
            (date,),
        ).fetchone()
        return row["pnl"]


init_db()
