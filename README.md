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

## Neon-managed email authentication

This project now includes a separate Neon Better Auth sign-up/sign-in screen at
`/neon-signup`, reachable from the main login form. It uses the official
`@neon/auth` client SDK, creates email/password accounts, reads the active
managed session via `getSession()`, and supports sign-out. The dedicated screen
is intentionally separate from the older FastAPI owner login: it does **not**
grant access to inventory, invoices or the original owner session cookie.
Existing business accounts and login continue working unchanged.

The Neon CLI links to project `wild-snow-59431122`, branch `main` and
`neon.ts` declares `auth: true`. The public Auth URL was validated from
Neon's branch configuration; do not place secret tokens or database passwords
in browser assets. The CLI wrote branch Auth env values to ignored `.env.local`.

To rebuild the checked-in browser asset after editing `neon_auth_frontend.js`:
```bash
npm install
npm run build:auth
py -m pytest -q tests/test_neon_signup.py
```

To try it locally, run the existing FastAPI server and open
`http://localhost:8000/neon-signup`. Neon Auth currently allows localhost.
Before production use, register the **verified** deployed app origin in Neon
Auth trusted domains, enable email verification with deliverable email, validate
full sign-up/sign-in/session/sign-out in-browser, and integrate a server-side
verified Neon identity into the inventory owner's authorization model. A Neon
Auth session alone is not an Inventrack owner session.

## AI Model Studio
Log in and select **AI Model Studio** in the sidebar. Click **Connect new model**, choose OpenAI, Gemini, Anthropic, OpenRouter, Groq, Together or a custom HTTPS endpoint, enter the model ID and optional API key, then save. **Test** uses a real provider request and may consume credits. Assign a model and optional instructions separately to Staff Assistant, Sales Analyst and Inventory Guide. The chat widget lets you switch bots.

Provider credentials are encrypted in SQLite using INVENTRACK_AI_KEY (Fernet). Without this environment variable, a private .ai_key file is created beside the SQLite database; securely back it up with the database. A lost encryption key makes saved provider credentials unreadable. Secrets never appear in API responses or browser storage. Custom endpoints must resolve to public HTTPS addresses. Only use trusted endpoints, since each provider receives its configured key and chat queries.

Run `py -m pytest -q tests/test_ai_models.py` to verify key protection, provider formats, bot routing and tenant isolation without making paid provider requests.
