"""One-time guarded migration from an existing local SQLite DB to PostgreSQL.

Destination must be empty. This tool does not change or delete the SQLite source.
Run after PostgreSQL has been provisioned and an encrypted copy of source data exists.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

from psycopg import connect
from psycopg.rows import dict_row

TABLES = [
    "owners", "sessions", "products", "invoices", "invoice_items",
    "rate_limits", "payment_attempts", "payment_webhook_events",
    "ai_model_configs", "ai_bot_bindings",
]
AUTO_IDS = ("owners", "products", "invoices", "invoice_items", "ai_model_configs")


def copy_source(path: Path, destination: str, *, commit: bool = False):
    if not path.is_file():
        raise RuntimeError("Source SQLite database not found: " + str(path))
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as src:
        src.row_factory = sqlite3.Row
        integrity = src.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError("SQLite source integrity check failed")
        available = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "owners" not in available:
            raise RuntimeError("SQLite source lacks owners table")
        table_rows = {table: [dict(r) for r in src.execute('SELECT * FROM "' + table + '"')]
                      for table in TABLES if table in available}
    sizes = {t: len(r) for t, r in table_rows.items()}
    if not commit:
        return {"dryRun": True, "counts": sizes}
    with connect(destination, row_factory=dict_row, connect_timeout=15) as pg:
        # Hold one DB-wide advisory lock while checking and importing.
        pg.execute("SELECT pg_advisory_xact_lock(%s)", (71482620,))
        for table in TABLES:
            if pg.execute(f'SELECT EXISTS(SELECT 1 FROM "{table}" LIMIT 1) AS present').fetchone()["present"]:
                raise RuntimeError("Destination is not empty. Refusing to overwrite: " + table)
        for table, rows in table_rows.items():
            if not rows:
                continue
            columns = list(rows[0])
            names = ", ".join('"' + c + '"' for c in columns)
            placeholders = ", ".join(["%s"] * len(columns))
            for row in rows:
                pg.execute(f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})',
                           tuple(row[c] for c in columns))
        for table in AUTO_IDS:
            pg.execute(
                "SELECT setval(pg_get_serial_sequence(%s,'id'),"
                "COALESCE((SELECT MAX(id) FROM \"" + table + "\"),1),"
                "(SELECT COUNT(*)>0 FROM \"" + table + "\"))",
                (table,),
            )
        pg.commit()
    return {"dryRun": False, "counts": sizes}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Commit import to an EMPTY destination")
    args = parser.parse_args()
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL must be set to the PostgreSQL destination")
    # The application creates its schema first; avoid connecting to an unknown server silently.
    from backend.app import init_db
    from backend.ai_models import init_ai_db
    init_db()
    init_ai_db()
    print(copy_source(args.sqlite, url, commit=args.apply))
