# Persistent PostgreSQL deployment (Neon + Render Free)

Status: **The code supports PostgreSQL and has been locally tested**. A live Neon
project must be connected before the public service can be switched. Keep the
existing Render app in demo-only mode until this checklist passes.

## Architecture

Render runs FastAPI and the static frontend. Neon stores accounts, sessions,
inventory, invoices, payment attempts, rate limits and AI model settings.
Local development continues to use SQLite when `DATABASE_URL` is absent.
A required, persistent `INVENTRACK_AI_KEY` decrypts saved AI provider credentials.

Neon's Free plan is usage-limited, currently 1 GB of PostgreSQL storage per
project and a short (6-hour) instant-restore window. The standalone offsite
backups below are independent of that limited recovery window. Do not expect
a free tier to provide a service-level availability guarantee.

## Deploy in stages

1. Connect Neon through your own account and create a **Free** PostgreSQL
   project. Prefer the region closest to the Render service (Singapore when
   available). Confirm free pricing before creating resources.
2. In Render, set the service's environment variable `DATABASE_URL` to the
   Neon connection string. Never paste the URL into source files, GitHub issues,
   documentation, console logs, or a public chat. Use an SSL-enabled connection
   string (for example, including `sslmode=require`).
3. Also set `INVENTRACK_AI_KEY` to a securely generated Fernet key. To create a
   new key when there are **no existing saved provider credentials**:

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Store the key in a secure password manager, not Git. If migrating existing
   AI credentials, **reuse the old key** (from `backend/.ai_key` or the original
   `INVENTRACK_AI_KEY` environment setting) or those credentials cannot be
   decrypted. Never overwrite a working key while retaining encrypted records.
4. Leave `INVENTRACK_DEMO_ONLY=1` for the initial rollout. Let Render redeploy.
   Check `/api/health` returns `{"status":"ok",...,"database":"postgresql"}`.
   Verify the demo can log in, create a product, generate an invoice, and see
   those same records after a fresh deploy/restart.
5. Set up and verify the offsite encrypted backups described below; download
   one artifact and restore it to a *separate empty scratch database*.
6. Only after the checks pass, change `INVENTRACK_DEMO_ONLY` to `0` in Render.
   Recheck `/api/health`, owner registration, login, invoice and persistence.
   Keep the payment gateway disabled until separately certified/tested.

**Safety:** On Render, startup refuses to accept real business data when
`INVENTRACK_DEMO_ONLY` is not `1` and `DATABASE_URL` is not set. A free Render
web service has no persistent disk and must never store production SQLite data.

## Copy an existing SQLite database (optional)

Run this in a controlled environment with backups, using the same app version.
Stop writes during migration. The destination PostgreSQL database **must be
empty** (all tables may already exist, with no rows).

```bash
# Set DATABASE_URL securely in the shell; do not commit it.
python -m backend.migrate_to_postgres --sqlite path/to/inventory.sqlite3
# Review counts, then:
python -m backend.migrate_to_postgres --sqlite path/to/inventory.sqlite3 --apply
```

The migration copies owners, products, invoices, payment states, rate limits,
sessions, and AI configuration/binding tables, preserving IDs and invoice numbers.
It refuses to overwrite a database containing data. The original SQLite file is
unchanged. You must retain or migrate the original AI encryption key separately.
The public hosted demo currently has *ephemeral* records; do not treat those as
reliable business records.

## Encrypted, offsite backups (GitHub Actions)

The repository contains `.github/workflows/backup-postgres.yml`, which prepares
a native custom-format `pg_dump`, checks it with `pg_restore --list`, encrypts it
with AES-256-GCM, and uploads **only the encrypted file** as an artifact kept for
7 days. The schedule is 02:27 UTC, subject to GitHub scheduler delays. It is
**disabled until explicitly enabled and configured**.

In the repository's **GitHub Settings → Secrets and variables → Actions** set:

- Secret `NEON_DATABASE_URL`: connection string for the Neon database (read-only
  dump role recommended).
- Secret `BACKUP_ENCRYPTION_KEY`: generate using
  `python -m backend.postgres_backup generate-key`; store a second copy securely
  *outside GitHub* for disaster recovery.
- Variable `ENABLE_PG_BACKUPS`: `true` only after both secrets are configured.

Run the backup workflow manually once and confirm a downloadable `.vfpga`
artifact appears. Download it and restore it to a **different, empty PostgreSQL
database** before relying on backups. Keep your own independent offsite copy
with a longer retention period if the records matter. A 7-day retention
window by itself is not sufficient for long-term business archives.

Manual backup using installed PostgreSQL client binaries:

```bash
python -m backend.postgres_backup create backups --retain 14
```

Manual restore to an **empty scratch DB only**:

```bash
# Provide DATABASE_URL, RESTORE_DATABASE_URL and BACKUP_ENCRYPTION_KEY in the environment.
# Never point RESTORE_DATABASE_URL to the live database.
CONFIRM_EMPTY_RESTORE=YES python -m backend.postgres_backup restore backups/your-file.vfpga
```

Do not commit database passwords, encrypted key files or backup dumps. Both
database credentials and encryption key should be rotated if leaked.

## Recovery gates

- At least one real PostgreSQL smoke test, including persistence across restart
- Offsite encrypted artifact downloaded and independently restored
- Database and encryption-key credentials retained by the owner
- Alerts for backup failures and free-tier exhaustion reviewed
- Restore drill repeated periodically

The included tests can exercise a temporary local PostgreSQL Docker database:
`TEST_POSTGRES_URL=postgresql://...@127.0.0.1:55432/inven_test pytest tests/test_postgres.py`.
**Warning:** that test suite drops the `public` schema and refuses non-local
database URLs. Do not run it against Neon/production.
