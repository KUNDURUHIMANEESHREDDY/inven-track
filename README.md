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

## AI Model Studio
Log in and select **AI Model Studio** in the sidebar. Click **Connect new model**, choose OpenAI, Gemini, Anthropic, OpenRouter, Groq, Together or a custom HTTPS endpoint, enter the model ID and optional API key, then save. **Test** uses a real provider request and may consume credits. Assign a model and optional instructions separately to Staff Assistant, Sales Analyst and Inventory Guide. The chat widget lets you switch bots.

Provider credentials are encrypted in SQLite using INVENTRACK_AI_KEY (Fernet). Without this environment variable, a private .ai_key file is created beside the SQLite database; securely back it up with the database. A lost encryption key makes saved provider credentials unreadable. Secrets never appear in API responses or browser storage. Custom endpoints must resolve to public HTTPS addresses. Only use trusted endpoints, since each provider receives its configured key and chat queries.

Run `py -m pytest -q tests/test_ai_models.py` to verify key protection, provider formats, bot routing and tenant isolation without making paid provider requests.
