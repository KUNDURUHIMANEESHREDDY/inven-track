"""Encrypted SQLite online backups, restore verification and periodic backup worker.

Usage:
  python -m backend.backup generate-key
  python -m backend.backup create
  python -m backend.backup schedule --interval-seconds 86400
  python -m backend.backup restore /backups/file.vfbackup /new/path.sqlite3
"""
from __future__ import annotations

import argparse
from contextlib import closing
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from backend.app import DB_PATH, init_db

DEFAULT_BACKUPS = Path(os.getenv("INVENTRACK_BACKUP_DIR",
                                   str(DB_PATH.parent.parent / "backups")))


def cipher():
    key = os.getenv("INVENTRACK_BACKUP_KEY", "")
    if not key:
        raise RuntimeError("INVENTRACK_BACKUP_KEY is required; backups must be encrypted")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise RuntimeError("Invalid INVENTRACK_BACKUP_KEY; generate one with generate-key")


def backup_database(db_path: Path = DB_PATH, directory: Path = DEFAULT_BACKUPS,
                    keep: int = 14) -> Path:
    """Online-consistent SQLite snapshot via backup API; encrypt and rotate."""
    if keep < 1:
        raise ValueError("keep must be positive")
    encryption = cipher()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError("Database is missing: " + str(path))
    fd, snapshot_name = tempfile.mkstemp(prefix=".snapshot-", suffix=".sqlite", dir=directory)
    os.close(fd)
    snapshot = Path(snapshot_name)
    output = directory / (
        "inven-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".vfbackup"
    )
    temporary = output.with_suffix(".tmp")
    try:
        with closing(sqlite3.connect(str(path), timeout=30)) as source:
            with closing(sqlite3.connect(str(snapshot))) as target:
                source.backup(target)
                integrity = target.execute("PRAGMA quick_check").fetchone()[0]
                if integrity != "ok":
                    raise RuntimeError("Snapshot integrity check failed: " + str(integrity))
        encrypted = encryption.encrypt(snapshot.read_bytes())
        with open(temporary, "wb") as out:
            out.write(encrypted)
            out.flush()
            os.fsync(out.fileno())
        temporary.replace(output)
        existing = sorted(directory.glob("inven-*.vfbackup"), reverse=True)
        for old in existing[keep:]:
            old.unlink()
        return output
    finally:
        snapshot.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)


def restore_database(backup: Path, destination: Path, overwrite: bool = False) -> Path:
    """Decrypt into a NEW DB path; quick_check before committing destination."""
    source = Path(backup)
    target = Path(destination)
    if target.exists() and not overwrite:
        raise FileExistsError("Destination exists; specify --overwrite explicitly")
    if target.resolve() == DB_PATH.resolve():
        raise ValueError("Do not restore over an active application DB; stop the app first")
    decrypted = cipher().decrypt(source.read_bytes())
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".restoring-", suffix=".sqlite", dir=target.parent)
    os.close(fd)
    temp = Path(name)
    try:
        temp.write_bytes(decrypted)
        with closing(sqlite3.connect(str(temp))) as con:
            assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok", "Corrupt backup"
        if target.exists():
            target.unlink()
        temp.replace(target)
        return target
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Encrypted, consistent InvenTrack backups")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("generate-key", help="Print new Fernet key; store outside your repository")
    create = sub.add_parser("create")
    create.add_argument("--keep", type=int, default=14)
    schedule = sub.add_parser("schedule")
    schedule.add_argument("--interval-seconds", type=int, default=86400)
    schedule.add_argument("--keep", type=int, default=14)
    restore = sub.add_parser("restore")
    restore.add_argument("backup")
    restore.add_argument("destination")
    restore.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.command == "generate-key":
        print(Fernet.generate_key().decode())
    elif args.command == "create":
        print("Created encrypted backup:", backup_database(keep=args.keep))
    elif args.command == "restore":
        print("Restored and verified:", restore_database(
            Path(args.backup), Path(args.destination), args.overwrite))
    else:
        if args.interval_seconds < 60:
            parser.error("Backup interval must be at least 60 seconds")
        print("Encrypted backup scheduler started", flush=True)
        while True:
            try:
                location = backup_database(keep=args.keep)
                print(datetime.now(timezone.utc).isoformat(), "Backup:", location, flush=True)
            except Exception as error:
                print("Backup failed:", type(error).__name__, str(error), file=sys.stderr,
                      flush=True)
            time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
