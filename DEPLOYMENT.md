# InvenTrack deployment and operations

## Current verified state
- FastAPI/SQLite backend, frontend integration, cost accounting, rate limiting, payment flow and backups are implemented.
- Local tests use mocked gateway calls, NOT real Razorpay transactions.
- Windows InvenTrackBackup scheduled task registered for 03:00 laptop-local time while logged in, and initial encrypted snapshot created.
- Docker Desktop was started. The application Docker image built successfully, its container passed API smoke checks, and Caddy validated its reverse proxy configuration. No public domain or HTTPS certificate has been provisioned.

## HTTPS deployment (Docker + Caddy)
1. Obtain a domain name and an authorized server; point DNS A/AAAA records to that server.
2. Make ports 80 and 443 reachable externally; do not publish port 8000.
3. Copy the project to that server and ensure Docker Engine is running.
4. Copy .env.example to .env; set DOMAIN to your real domain.
5. Generate a backup key: python -m backend.backup generate-key
6. Set INVENTRACK_BACKUP_KEY in .env to that key; save the key separately from the server.
7. Optionally configure test-mode payment and AI variables (see below).
8. Run: docker compose config
9. Run: docker compose up --build -d
10. Check: docker compose ps
11. Check: docker compose logs -f app caddy backups
12. Verify: https://YOUR_DOMAIN/api/health

Caddy automatically manages HTTPS certificates for reachable domains. Backend is internal to the private Docker network; only Caddy publishes 80/443. The backup worker runs on startup and every 24h afterward, storing up to 30 encrypted snapshots in a named volume. Export copies off-server for disaster recovery. Don't publish .env or backup key.

## Razorpay integration (optional, test mode first)
A merchant account and its verification are prerequisites. Get test-mode keys and webhook secret from your own authorized merchant dashboard. In server environment:
- RAZORPAY_MODE=test
- RAZORPAY_KEY_ID=rzp_test_...
- RAZORPAY_KEY_SECRET=...
- RAZORPAY_WEBHOOK_SECRET=...

Configure webhook URL https://YOUR_DOMAIN/api/payments/webhook for event payment.captured, and configure automatic capture in the merchant dashboard. The app uses Orders API; success requires server-side HMAC verification AND a captured payment confirmed by Razorpay. Webhooks use raw-body signature validation, event deduplication and amount/order checking. Sales > Check Status uses provider reconciliation if the browser callback or webhook was lost.

Live mode requires RAZORPAY_MODE=live, a live key, and ENABLE_LIVE_PAYMENTS=YES; do not enable until permitted merchant setup, provider test-mode E2E, webhook delivery, refund handling and compliance are finished.

IMPORTANT: One global Razorpay API key means proceeds settle to one merchant account, not individual business tenants. Multi-merchant SaaS requires a supported connected-merchant marketplace/onboarding design. Do not enable collection for unrelated tenants with a shared account.

IMPORTANT: Cash, Card and UPI / QR currently record the method only; they do not charge or verify an external payment. They have status recorded, versus paid for gateway-verified capture. Online invoices are pending until verified, and while pending their reserved stock is deducted. A pending invoice with a provider order cannot be voided without reconciliation.

## Gross profit and reporting
Product costPrice is optional. Gross profit is sum of (selling-price-before-GST minus unit cost) times quantity on the date of completed/recorded sales. Invoice rows snapshot purchase cost at the time of sale, so later edits do not change past profit. Profit is N/A if a sold item's cost is missing. Gross profit does not subtract operating expenses, payment fees, rent or salaries, and is not net profit. Existing historical invoices with unknown costs remain unknown.

## Windows backups and restore
Install schedule: powershell -ExecutionPolicy Bypass -File .\scripts\install_backup_task.ps1
Run immediately: powershell -ExecutionPolicy Bypass -File .\scripts\run_backup.ps1

Key location: %LOCALAPPDATA%\InvenTrack\backup.key
Encrypted backups: %LOCALAPPDATA%\InvenTrack\Backups
Scheduler: InvenTrackBackup at 03:00 local machine time while signed in, starting on next available opportunity if a run is missed.

Restore to a separate file (set INVENTRACK_BACKUP_KEY from your secure backup key store first):
python -m backend.backup restore PATH_TO_FILE.vfbackup NEW_PATH.sqlite3

For a real restore, stop the app first; do not overwrite an active SQLite DB. Keep offsite encrypted backups AND a secure copy of the key.

## Remaining release gates
- Public hostname and TLS certificate issuance not done
- Application Docker image built and smoke tested; actual public deployment not done
- Real Razorpay test-mode payment/webhook flow and charge/refund/settlement checks not done
- Offsite backups and failure-recovery drill not configured
- Operational observability, performance, load tests and independent public security review not done
- Merchant onboarding/KYC and production compliance not completed

## AI Model Studio deployment
The AI Model Studio provides per-owner encrypted model credentials, custom HTTPS base endpoints and separate model assignments for Staff Assistant, Sales Analyst and Inventory Guide. OpenAI-compatible, Google Gemini and Anthropic API formats are supported. The chat widget uses the selected bot's assigned model, with the existing GEMINI_API_KEY fallback for an unassigned staff bot.

Set INVENTRACK_AI_KEY to a persistent Fernet key in the server environment. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Docker Compose forwards this variable if set in .env. Without it, the backend creates .ai_key alongside the SQLite database in persistent /data storage. Back up this secret together with the database; do not commit or share either. If lost, stored provider credentials cannot be decrypted. Use the same encryption key across app replicas.

Providers receive user queries and any relevant store data included in chatbot context. Choose trustworthy providers only. Keys are never returned by the API. Model connection tests can consume credits. HTTP redirects are disabled; private network endpoints are rejected. The new unit tests mock provider calls; production vendor connectivity still requires authorized credentials and a live integration test.
