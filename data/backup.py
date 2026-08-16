"""Weekly backup: pg_dump the raw_* tables to Google Drive, then truncate
them to stay under the Aiven free-tier storage cap.

Order matters: dump -> upload -> verify upload succeeded -> only then
truncate. If any step before the truncate fails, nothing is deleted — the
raw tables are left as-is and the job exits non-zero.

Run: python -m data.backup
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from data.db import get_conn  # noqa: E402
from data.drive import get_file_size, upload_file  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

RAW_TABLES = ["raw_ohlcv", "raw_futures_snapshot", "raw_options_chain", "raw_liquidations"]
DUMP_DIR = Path(os.getenv("BACKUP_DUMP_DIR", "/tmp"))


def _dump_database(dump_path: Path) -> None:
    database_url = os.environ["DATABASE_URL"]
    cmd = [
        "pg_dump", database_url,
        "-Fc",  # custom format: compressed, restorable with pg_restore
        "-f", str(dump_path),
        *[arg for t in RAW_TABLES for arg in ("-t", t)],
    ]
    logger.info("running pg_dump -> %s", dump_path)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {result.stderr}")


def _truncate_raw_tables() -> None:
    tables_sql = ", ".join(RAW_TABLES)
    conn = get_conn()
    with conn, conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {tables_sql}")
    conn.close()
    logger.info("truncated: %s", tables_sql)


def run_backup() -> None:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    dump_path = DUMP_DIR / f"crypto_raw_backup_{stamp}.dump"

    try:
        _dump_database(dump_path)
        local_size = dump_path.stat().st_size
        if local_size == 0:
            raise RuntimeError("pg_dump produced an empty file")
        logger.info("dump size: %d bytes", local_size)

        file_id = upload_file(dump_path, drive_filename=dump_path.name)
        remote_size = get_file_size(file_id)
        if remote_size != local_size:
            raise RuntimeError(f"upload verification failed: local={local_size} remote={remote_size}")
        logger.info("verified upload (file_id=%s, size=%d bytes)", file_id, remote_size)

        _truncate_raw_tables()
        logger.info("backup + truncate complete")
    finally:
        if dump_path.exists():
            dump_path.unlink()


if __name__ == "__main__":
    try:
        run_backup()
    except Exception:
        logger.exception("backup failed — raw tables were NOT truncated")
        sys.exit(1)
