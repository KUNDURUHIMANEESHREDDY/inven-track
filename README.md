# InvenTrack

**Inventory & Sales OS for retail and small businesses.**

InvenTrack brings inventory management, billing, sales analytics and configurable AI assistance into one browser-based workspace. It helps business owners track stock and costs, create invoices, monitor gross profit and make better-informed day-to-day decisions without switching between disconnected tools.

[Open the hosted demo](https://inven-track-demo.onrender.com/)

## Core capabilities

| Area | Features |
| --- | --- |
| Inventory | Product catalog, stock quantities, selling prices, unit costs and low-stock alerts |
| Billing and sales | Server-calculated invoices, optional 5% GST, checkout stock validation and atomic stock deductions |
| Business insights | Sales and gross-profit tracking with immutable invoice cost snapshots |
| AI Model Studio | Configurable AI providers, model discovery, provider testing and dedicated Staff Assistant, Sales Analyst and Inventory Guide bots |
| Accounts | Per-business owner login using email or Indian mobile verification codes when delivery is configured, sessions and tenant isolation |
| Payments | Optional Razorpay checkout with signature, captured-payment and webhook verification |
| Persistence | SQLite for local development; PostgreSQL support for persistent cloud deployments |
| Security and recovery | Encrypted AI credentials, rate limiting, security headers and encrypted backup tooling |

## Technology and architecture

- **Frontend:** HTML, CSS and JavaScript; browser-based inventory and billing interface.
- **Backend:** Python 3.11, FastAPI, Uvicorn and Pydantic.
- **Data:** SQLite locally, PostgreSQL through psycopg when `DATABASE_URL` is configured; Neon is a supported hosted database option.
- **Authentication:** FastAPI business-owner sessions; separate Neon Better Auth email/password flow using the official JavaScript SDK.
- **AI:** OpenAI, Gemini, Anthropic, OpenRouter, Groq, Together and custom public HTTPS endpoints.
- **Deployment:** Docker, Caddy, Render and optional PythonAnywhere configuration.

The backend handles tenant authorization, invoice calculations, stock transactions and provider requests. Database credentials and provider encryption keys belong in server-side environment configuration, never in frontend source or Git.

The hosted link is a demo. Production persistence, payment enablement, backup restoration and managed-auth integration must be validated for the actual deployment.

## Owner login

Sign in to your existing business using your registered email address or Indian mobile number and password. Shared mobile numbers require email login. Registration still requires both contact fields. This is password authentication, not SMS or email OTP verification.

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

To rebuild the checked-in browser asset after editing `frontend/neon_auth_frontend.js`:
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

Provider credentials are encrypted using INVENTRACK_AI_KEY (Fernet) before storage. For local SQLite development, if the variable is absent, a private .ai_key file is created beside the database; securely back it up with the database. For PostgreSQL deployments, configure a persistent server-side key. A lost encryption key makes saved provider credentials unreadable. Secrets never appear in API responses or browser storage. Custom endpoints must resolve to public HTTPS addresses. Only use trusted endpoints, since each provider receives its configured key and chat queries.

Run `py -m pytest -q tests/test_ai_models.py` to verify key protection, provider formats, bot routing and tenant isolation without making paid provider requests.

## Local Qwen3.5-0.8B in AI Model Studio

In **AI Model Studio**, select **Qwen3.5-0.8B · On this device (WebGPU)** for an assistant and save its assignment. Use **Download / Test local Qwen** to fetch and initialize the model. First use downloads approximately 700–800 MB of quantized ONNX model files from Hugging Face; subsequent use can reuse the browser's cache if storage remains available. The first download may take several minutes on slower connections. The model runs in a Web Worker using WebGPU and requires an HTTPS (or localhost) origin and a capable device.

Local Qwen selection is stored only in that browser, scoped to the signed-in business and bot. The server keeps any prior cloud-model assignment; selecting a cloud model again disables the local override. If local inference fails, the application displays an error **without silently sending the user's message to a cloud provider**. Change the assistant selection to the configured cloud model to use it. The server provides authenticated, read-only sales and low-stock facts to the local model via `/api/ai/context` — no AI API credentials are returned.

**Build and verification:**
```bash
npm ci
npm run build:qwen
node --test tests/test_ai_models_frontend.cjs tests/test_qwen_frontend.cjs
python -m pytest -q tests/test_ai_models.py
# Windows + real browser and WebGPU required for this acceptance test:
python scripts/verify_qwen_webgpu.py
```

The generated `frontend/qwen_worker.bundle.js` is **not committed to Git**. Render's multi-stage Docker build uses the pinned npm lockfile to compile it and copies the bundle into the final Python image; local development requires `npm run build:qwen` before serving the page. The model weights are **not** embedded in the Docker image or hosted on Render. Actual browser inference and device memory performance must be measured; this is not guaranteed to work on mobile browsers or low-memory GPUs. The UI remains usable with an assigned cloud provider.

## Developer documentation

See [Deployment and local development](docs/DEPLOYMENT.md) for installation, testing, configuration and operational guidance, and [PostgreSQL deployment](docs/DEPLOY_POSTGRES.md) for persistent cloud storage.

## Repository structure

- [frontend/](frontend/) - browser interface, JavaScript and bundled authentication client.
- [backend/](backend/) - API, business logic, database and integrations.
- [docs/](docs/) - deployment and operational guides.
- [deploy/](deploy/) - reverse proxy configuration.
- [scripts/](scripts/) - local launcher and backup utilities.
- [tests/](tests/) - backend and frontend regression tests.

Root-level manifests and hosting entrypoints remain in place for tooling compatibility.
