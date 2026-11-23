"""A small DB-API compatibility layer for the existing SQLite-first application.

Select PostgreSQL only when DATABASE_URL is provided. All SQL is application-owned:
values always use bound placeholders. SQLite remains the default for local development.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager

from psycopg import connect, IntegrityError as PostgresIntegrityError
from psycopg.rows import dict_row

# All existing writes protected by BEGIN IMMEDIATE in the SQLite implementation
# must be serialized for equivalent read/modify/write semantics in PostgreSQL.
_LOCK_ID = 71482620
_SERIAL_TABLES = {"owners", "products", "invoices", "invoice_items", "ai_model_configs"}
_SCHEMA_COLUMN = re.compile(r"^PRAGMA\s+table_info\(([a-z_]+)\)$", re.I)


class Result:
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid
        self.rowcount = cursor.rowcount

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)


class PostgresConnection:
    def __init__(self, raw):
        self.raw = raw

    def execute(self, statement, args=()):
        query = statement.strip()
        if query.upper() == "BEGIN IMMEDIATE":
            cursor = self.raw.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_ID,))
            return Result(cursor)
        match = _SCHEMA_COLUMN.match(query)
        if match:
            cursor = self.raw.execute(
                """SELECT column_name AS name FROM information_schema.columns
                   WHERE table_schema=current_schema() AND table_name=%s""",
                (match.group(1),))
            return Result(cursor)
        query = query.replace("?", "%s")
        query = re.sub(r"\bMAX\s*\(\s*0\s*,", "GREATEST(0,", query, flags=re.I)
        is_insert = re.match(r"^INSERT\s+INTO\s+([a-z_]+)\b", query, re.I)
        auto_id = is_insert and is_insert.group(1).lower() in _SERIAL_TABLES
        if auto_id and not re.search(r"\bRETURNING\b", query, re.I):
            query = query.rstrip(";") + " RETURNING id"
        try:
            cursor = self.raw.execute(query, args)
            if auto_id:
                record = cursor.fetchone()
                return Result(cursor, record["id"])
            return Result(cursor)
        except PostgresIntegrityError as exc:
            # Preserve existing FastAPI handlers that catch sqlite3.IntegrityError.
            raise sqlite3.IntegrityError(str(exc)) from exc

    def executescript(self, script):
        # DDL scripts are project-owned and contain no dollar-quoted function bodies.
        for chunk in script.split(";"):
            sql = chunk.strip()
            sql = re.sub(r"^PRAGMA\s+journal_mode\s*=\s*WAL\s*$", "", sql, flags=re.I)
            if not sql:
                continue
            # An ID primary key auto-increments in SQLite, but needs IDENTITY in PG.
            sql = re.sub(r"\bid\s+INTEGER\s+PRIMARY\s+KEY\b",
                         "id BIGSERIAL PRIMARY KEY", sql, flags=re.I)
            self.execute(sql)


@contextmanager
def postgres_database(url):
    raw = connect(url, row_factory=dict_row, connect_timeout=15)
    try:
        yield PostgresConnection(raw)
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()
