"""Encrypted PostgreSQL backups using native pg_dump/pg_restore and AES-256-GCM.

Requires PostgreSQL client binaries on PATH. Encryption key is 32 random bytes
encoded with urlsafe base64 (Fernet.generate_key() produces this format).
Backups are not stored in the Git repository. Never commit a key or DATABASE_URL.
"""
from __future__ import annotations

import argparse
import base64
import os
import secrets
import subprocess
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from psycopg import connect

MAGIC = b"VFPGBK01"
BUFFER = 1024 * 1024


def secret_key():
    value = os.getenv("BACKUP_ENCRYPTION_KEY", "")
    try:
        key = base64.urlsafe_b64decode(value.encode("ascii"))
        if len(key) != 32:
            raise ValueError("bad size")
        return key
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError("Set BACKUP_ENCRYPTION_KEY to 32 random bytes, URL-safe base64") from exc


def encrypt(src: Path, out: Path, key: bytes):
    nonce = secrets.token_bytes(12)
    enc = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    temp = out.with_suffix(out.suffix + ".part")
    try:
        with src.open("rb") as reader, temp.open("wb") as writer:
            writer.write(MAGIC + nonce)
            while chunk := reader.read(BUFFER):
                writer.write(enc.update(chunk))
            writer.write(enc.finalize())
            writer.write(enc.tag)
        os.replace(temp, out)
    finally:
        temp.unlink(missing_ok=True)


def decrypt(src: Path, out: Path, key: bytes):
    with src.open("rb") as reader:
        if reader.read(len(MAGIC)) != MAGIC:
            raise RuntimeError("Invalid encrypted backup header")
        nonce = reader.read(12)
        if len(nonce) != 12:
            raise RuntimeError("Backup truncated")
        size = src.stat().st_size
        end = size - 16
        if end < len(MAGIC) + 12:
            raise RuntimeError("Backup truncated")
        reader.seek(end)
        tag = reader.read(16)
        dec = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        reader.seek(len(MAGIC) + 12)
        with out.open("wb") as writer:
            pos = reader.tell()
            while pos < end:
                chunk = reader.read(min(BUFFER, end - pos))
                if not chunk:
                    raise RuntimeError("Backup truncated")
                writer.write(dec.update(chunk))
                pos += len(chunk)
            # GCM finalize raises InvalidTag if the key or data is wrong.
            writer.write(dec.finalize())


def client(tool, url, *options):
    # Pass DATABASE_URL through process environment, not command arguments/logs.
    env = {**os.environ, "PGDATABASE": url}
    subprocess.run([tool, *options], check=True, env=env, timeout=900,
                   stdout=subprocess.DEVNULL)


def create(output_dir, *, retain=14):
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL must point to PostgreSQL")
    key = secret_key()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    filename = "inven-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with tempfile.TemporaryDirectory() as tmp:
        sql = Path(tmp) / "database.dump"
        client("pg_dump", url, "--format=custom", "--no-owner", "--no-privileges",
               "--file", str(sql))
        result = output_dir / (filename + ".vfpga")
        encrypt(sql, result, key)
        verify = Path(tmp) / "check.dump"
        decrypt(result, verify, key)
        client("pg_restore", url, "--list", str(verify))
    backups = sorted(output_dir.glob("inven-*.vfpga"))
    for expired in backups[:-max(1, retain)]:
        expired.unlink()
    return result


def seal_existing_dump(source, output):
    """Encrypt a pg_dump custom-format file prepared by an external client."""
    source, output = Path(source), Path(output)
    with source.open("rb") as reader:
        if reader.read(5) != b"PGDMP":
            raise RuntimeError("Not a PostgreSQL custom-format dump")
    encrypt(source, output, secret_key())
    with tempfile.TemporaryDirectory() as temp:
        verified = Path(temp) / "verified.dump"
        decrypt(output, verified, secret_key())
        if verified.stat().st_size != source.stat().st_size:
            raise RuntimeError("Encrypted backup verification failed")
    return output


def restore(file):
    url = os.environ.get("RESTORE_DATABASE_URL")
    if not url or not os.environ.get("DATABASE_URL"):
        raise RuntimeError("Set DATABASE_URL (production source) and RESTORE_DATABASE_URL (empty target)")
    if url == os.environ["DATABASE_URL"]:
        raise RuntimeError("Refusing to restore into the source database")
    key = secret_key()
    # Prevent accidental restores over a live application; only empty public schemas.
    with connect(url, connect_timeout=15) as conn:
        count = conn.execute("""SELECT COUNT(*) FROM information_schema.tables
                                WHERE table_schema='public' AND table_type='BASE TABLE'""").fetchone()[0]
        if count:
            raise RuntimeError("Restore destination must be an empty database")
    with tempfile.TemporaryDirectory() as tmp:
        sql = Path(tmp) / "verified.dump"
        decrypt(Path(file), sql, key)
        client("pg_restore", url, "--no-owner", "--no-privileges",
               "--exit-on-error", "--dbname=", str(sql))
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("create", "restore", "generate-key", "seal"))
    parser.add_argument("target", nargs="?", help="Backup directory or encrypted backup file")
    parser.add_argument("--retain", type=int, default=14)
    parser.add_argument("--output", type=Path, help="Encrypted output path for seal")
    args = parser.parse_args()
    if args.operation == "generate-key":
        print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"))
    elif args.operation == "create":
        print("Encrypted and verified:", create(args.target or "backups", retain=args.retain))
    elif args.operation == "seal":
        if not args.target or not args.output:
            raise SystemExit("seal requires source dump and --output")
        print("Encrypted and verified:", seal_existing_dump(args.target, args.output))
    else:
        if not args.target or os.getenv("CONFIRM_EMPTY_RESTORE") != "YES":
            raise SystemExit("Restore needs a file and CONFIRM_EMPTY_RESTORE=YES")
        restore(args.target)
        print("Restore succeeded into empty destination")
