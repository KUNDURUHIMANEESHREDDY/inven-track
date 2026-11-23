# VyaparFlow / Inven Track

Python 3.11 FastAPI + SQLite backend for the existing HTML inventory and billing frontend.

## Start on Windows
Double-click run_backend.bat, or run:
1. py -m pip install -r requirements.txt
2. py -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
3. Open http://127.0.0.1:8000

API docs: http://127.0.0.1:8000/docs
Tests: py -m pytest -q tests

## Includes
- Per-business login, sessions and tenant isolation
- Durable products, stock, prices, unit costs and low-stock alerts
- Server-calculated invoices, optional 5% GST, immutable cost snapshots and gross profit
- Checkout stock checks and atomic deductions
- SQLite rate limiting and anti-framing/security headers
- Optional Razorpay checkout with signature, captured-payment verification and webhooks
- Encrypted SQLite backups and scheduled Windows backup task
- Caddy + Docker Compose deployment configuration
- Optional Gemini API backend with local frontend help fallback

See DEPLOYMENT.md for setup and limitations. Do not place API keys into the frontend or commit backup keys, .env or databases.
