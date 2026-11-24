"""InvenTrack inventory and billing API (Python 3.11+, FastAPI + SQLite)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import urllib.error
import urllib.request
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("INVENTRACK_DB", str(ROOT / "backend" / "inventory.sqlite3")))
TIMEZONE = ZoneInfo(os.getenv("INVENTRACK_TIMEZONE", "Asia/Kolkata"))
COOKIE = "inven_session"
SESSION_AGE = 7 * 24 * 60 * 60
PAYMENTS = {"Cash", "Card", "UPI / QR", "Online (Razorpay)"}


@contextmanager
def database():
    if os.getenv("DATABASE_URL"):
        from backend.postgres import postgres_database
        with postgres_database(os.environ["DATABASE_URL"]) as con:
            yield con
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 15000")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    with database() as con:
        con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS owners (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            mobile TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            business_name TEXT NOT NULL,
            business_type TEXT NOT NULL,
            is_demo INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            owner_id INTEGER NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY,
            owner_id INTEGER NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price_paise INTEGER NOT NULL CHECK(price_paise > 0),
            stock INTEGER NOT NULL CHECK(stock >= 0),
            min_stock INTEGER NOT NULL CHECK(min_stock >= 0),
            sold_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_products_owner ON products(owner_id);
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY,
            owner_id INTEGER NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
            invoice_number INTEGER NOT NULL,
            customer TEXT NOT NULL,
            subtotal_paise INTEGER NOT NULL,
            gst_paise INTEGER NOT NULL,
            total_paise INTEGER NOT NULL,
            payment TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(owner_id, invoice_number)
        );
        CREATE INDEX IF NOT EXISTS idx_invoices_owner_date ON invoices(owner_id, created_at);
        CREATE TABLE IF NOT EXISTS invoice_items (
            id INTEGER PRIMARY KEY,
            invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
            product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
            name TEXT NOT NULL,
            price_paise INTEGER NOT NULL,
            qty INTEGER NOT NULL
        );
        """)
        migrate_schema(con)
        from backend.passwordless import init_schema
        init_schema(con)


def migrate_schema(con):
    """Keep existing user records and invoices while introducing nullable costs."""
    for table, column, declaration in (
        ("products", "cost_paise", "INTEGER CHECK(cost_paise >= 0)"),
        ("invoice_items", "cost_paise", "INTEGER CHECK(cost_paise >= 0)"),
        ("invoices", "payment_status", "TEXT NOT NULL DEFAULT 'recorded'"),
    ):
        existing = {row["name"] for row in con.execute("PRAGMA table_info(" + table + ")")}
        if column not in existing:
            con.execute("ALTER TABLE " + table + " ADD COLUMN " + column + " " + declaration)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS rate_limits (
            bucket TEXT PRIMARY KEY, window_start INTEGER NOT NULL, hits INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payment_attempts (
            invoice_id INTEGER PRIMARY KEY REFERENCES invoices(id) ON DELETE CASCADE,
            gateway_order_id TEXT UNIQUE,
            gateway_payment_id TEXT UNIQUE,
            amount_paise INTEGER NOT NULL,
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payment_webhook_events (
            event_id TEXT PRIMARY KEY, received_at TEXT NOT NULL
        );
    """)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if os.getenv("RENDER_SERVICE_ID") and not os.getenv("DATABASE_URL") and os.getenv("INVENTRACK_DEMO_ONLY") != "1":
        raise RuntimeError("Refusing to accept real data on an ephemeral Render filesystem; configure DATABASE_URL")
    if os.getenv("DATABASE_URL") and not os.getenv("INVENTRACK_AI_KEY"):
        raise RuntimeError("Persistent DATABASE_URL requires a persistent INVENTRACK_AI_KEY for encrypted AI model credentials")
    init_db()
    from backend.ai_models import init_ai_db
    init_ai_db()
    yield


app = FastAPI(title="InvenTrack API", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin:
            from urllib.parse import urlsplit
            if urlsplit(origin).netloc != request.headers.get("host"):
                return Response("Cross-origin request denied", status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def dt_iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat(timespec="microseconds")


def money_to_paise(number: Decimal | str | int | float) -> int:
    try:
        value = Decimal(str(number))
        if not value.is_finite() or value < 0 or value > Decimal("100000000"):
            raise ValueError
        return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except Exception:
        raise HTTPException(422, "Invalid monetary amount")


def rupees(paise: int) -> float:
    return paise / 100


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260_000)
    return salt.hex() + ":" + digest.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split(":", 1)
        return hmac.compare_digest(hash_password(password, bytes.fromhex(salt)), stored)
    except (ValueError, TypeError):
        return False


def owner_json(row):
    return {
        "id": row["id"],
        "businessName": row["business_name"],
        "businessType": row["business_type"],
        "email": row["email"],
        "mobile": "+91 " + row["mobile"],
        "demo": bool(row["is_demo"]),
    }


def set_session(con, owner_id: int, response: Response, request: Request):
    token = secrets.token_urlsafe(40)
    expires = dt_iso(utcnow() + timedelta(seconds=SESSION_AGE))
    con.execute("INSERT INTO sessions (token_hash, owner_id, expires_at) VALUES (?, ?, ?)",
                (hashlib.sha256(token.encode()).hexdigest(), owner_id, expires))
    response.set_cookie(COOKIE, token, max_age=SESSION_AGE, httponly=True,
                        secure=request.url.scheme == "https", samesite="lax", path="/")


def current_owner(request: Request):
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Please log in")
    digest = hashlib.sha256(token.encode()).hexdigest()
    with database() as con:
        owner = con.execute("""
            SELECT o.* FROM owners o JOIN sessions s ON s.owner_id=o.id
            WHERE s.token_hash=? AND s.expires_at > ?
        """, (digest, dt_iso())).fetchone()
    if not owner:
        raise HTTPException(401, "Session expired; please log in")
    return owner


def enforce_limit(request: Request, scope: str, max_hits: int, interval: int, identity: str = "", *, global_identity: bool = False):
    """SQLite-backed, multi-process fixed-window throttling; no proxy-spoofed client IP."""
    address = request.client.host if request.client else "unknown"
    bucket = hashlib.sha256((scope + "|" + ("global" if global_identity else address) + "|" + identity.lower()).encode()).hexdigest()
    now = int(utcnow().timestamp())
    window_start = now - (now % interval)
    with database() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT window_start,hits FROM rate_limits WHERE bucket=?",
                          (bucket,)).fetchone()
        if not row or row["window_start"] != window_start:
            con.execute("""INSERT INTO rate_limits(bucket,window_start,hits) VALUES(?,?,1)
                        ON CONFLICT(bucket) DO UPDATE SET window_start=excluded.window_start,hits=1""",
                        (bucket, window_start))
        elif row["hits"] >= max_hits:
            delay = interval - now % interval
            raise HTTPException(429, "Too many requests; try again later",
                                headers={"Retry-After": str(delay)})
        else:
            con.execute("UPDATE rate_limits SET hits=hits+1 WHERE bucket=?", (bucket,))


class Register(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    mobile: str = Field(pattern=r"^[0-9]{10}$")
    password: str = Field(min_length=8, max_length=128)
    businessName: str = Field(min_length=2, max_length=100)
    businessType: str = Field(default="Supermart", min_length=2, max_length=50)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Enter a valid email address")
        return value


class Login(BaseModel):
    identifier: str | None = Field(default=None, max_length=254)
    email: str | None = Field(default=None, max_length=254)
    mobile: str | None = Field(default=None, max_length=32)
    password: str = Field(min_length=1, max_length=128)


def normalize_login_mobile(value: str) -> str:
    value = re.sub(r"[\s-]", "", value)
    if value.startswith("+91"):
        value = value[3:]
    return value if re.fullmatch(r"[0-9]{10}", value) else ""


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=50)
    price: Decimal = Field(gt=0, le=100000000)
    stock: int = Field(ge=0, le=100000000)
    minStock: int = Field(ge=0, le=100000000)
    costPrice: Decimal | None = Field(default=None, ge=0, le=100000000)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, min_length=1, max_length=50)
    price: Decimal | None = Field(default=None, gt=0, le=100000000)
    stock: int | None = Field(default=None, ge=0, le=100000000)
    minStock: int | None = Field(default=None, ge=0, le=100000000)
    costPrice: Decimal | None = Field(default=None, ge=0, le=100000000)


class Restock(BaseModel):
    qty: int = Field(gt=0, le=1000000)


class CartItem(BaseModel):
    id: int
    qty: int = Field(gt=0, le=100000)


class Checkout(BaseModel):
    items: list[CartItem] = Field(min_length=1, max_length=200)
    customer: str = Field(default="Walk-in Customer", max_length=120)
    payment: str = "UPI / QR"
    includeGst: bool = True


class AssistantPrompt(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


def product_json(row):
    return {
        "id": row["id"], "name": row["name"], "category": row["category"],
        "price": rupees(row["price_paise"]), "stock": row["stock"],
        "minStock": row["min_stock"], "soldCount": row["sold_count"],
        "costPrice": rupees(row["cost_paise"]) if row["cost_paise"] is not None else None
    }


def invoice_json(con, row):
    items = con.execute(
        "SELECT name, qty, price_paise, cost_paise, product_id FROM invoice_items WHERE invoice_id=? ORDER BY id",
        (row["id"],)
    ).fetchall()
    local_time = datetime.fromisoformat(row["created_at"]).astimezone(TIMEZONE)
    today = local_time.date() == utcnow().astimezone(TIMEZONE).date()
    return {
        "id": "INV-" + str(1000 + row["invoice_number"]),
        "time": ("Today, " if today else local_time.strftime("%d %b %Y, "))
                + local_time.strftime("%I:%M %p"),
        "customer": row["customer"],
        "items": [{"id": it["product_id"], "name": it["name"],
                   "price": rupees(it["price_paise"]), "qty": it["qty"]} for it in items],
        "subtotal": rupees(row["subtotal_paise"]), "gst": rupees(row["gst_paise"]),
        "total": rupees(row["total_paise"]), "payment": row["payment"],
        "createdAt": row["created_at"],
        "paymentStatus": row["payment_status"],
        "grossProfit": (rupees(sum((it["price_paise"] - it["cost_paise"]) * it["qty"]
                            for it in items)) if all(it["cost_paise"] is not None for it in items)
                        else None),
    }


def invoices_for(con, owner_id: int, limit: int = 500, offset: int = 0):
    rows = con.execute(
        "SELECT * FROM invoices WHERE owner_id=? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        (owner_id, limit, offset)).fetchall()
    return [invoice_json(con, r) for r in rows]


def dashboard_for(con, owner_id: int):
    today = utcnow().astimezone(TIMEZONE).date()
    start = today - timedelta(days=6)
    since = dt_iso(datetime.combine(start, time.min, TIMEZONE).astimezone(timezone.utc))
    rows = con.execute("""
        SELECT i.subtotal_paise, i.total_paise, i.created_at,
               (SELECT SUM(t.cost_paise * t.qty) FROM invoice_items t
                WHERE t.invoice_id=i.id) AS cogs,
               (SELECT COUNT(*) FROM invoice_items t
                WHERE t.invoice_id=i.id AND t.cost_paise IS NULL) AS missing_cost
        FROM invoices i
        WHERE i.owner_id=? AND i.created_at>=?
          AND i.payment_status IN ('recorded','paid')
    """, (owner_id, since)).fetchall()
    daily = {start + timedelta(days=i): 0 for i in range(7)}
    counts = {today: 0}
    gross_profit_paise = 0
    sale_subtotals_paise = 0
    missing_costs = False
    for row in rows:
        day = datetime.fromisoformat(row["created_at"]).astimezone(TIMEZONE).date()
        if day in daily:
            daily[day] += row["total_paise"]
            if day == today:
                counts[today] += 1
                sale_subtotals_paise += row["subtotal_paise"]
                if row["missing_cost"]:
                    missing_costs = True
                else:
                    gross_profit_paise += row["subtotal_paise"] - (row["cogs"] or 0)
    day_sales = daily[today]
    count = counts[today]
    low = con.execute("SELECT COUNT(*) AS n FROM products WHERE owner_id=? AND stock<=min_stock",
                      (owner_id,)).fetchone()["n"]
    products = con.execute("SELECT COUNT(*) AS n FROM products WHERE owner_id=?",
                           (owner_id,)).fetchone()["n"]
    return {
        "todaySales": rupees(day_sales), "todayBills": count,
        "averageBill": rupees(round(day_sales / count)) if count else 0,
        "lowStock": low, "totalProducts": products,
        "weeklyDays": [(start + timedelta(days=i)).strftime("%a") if i < 6 else "Today" for i in range(7)],
        "weeklyRevenue": [rupees(v) for v in daily.values()],
        "weeklyTotal": rupees(sum(daily.values())),
        "profitAvailable": not missing_costs,
        "grossProfit": rupees(gross_profit_paise) if not missing_costs else None,
        "grossMarginPercent": (round(100 * gross_profit_paise / sale_subtotals_paise, 2)
                               if not missing_costs and sale_subtotals_paise else None),
    }


DEMO_PRODUCTS = [
    ("India Gate Basmati Rice (5kg)", "Groceries", 54000, 18, 8, 42),
    ("Freedom Sunflower Oil (1L)", "Groceries", 13500, 5, 10, 68),
    ("Toor Dal Premium (1kg)", "Groceries", 16500, 22, 8, 35),
    ("Special Masala Dosa", "Hotel Menu", 8500, 40, 10, 95),
    ("South Indian Filter Coffee", "Beverages", 3500, 6, 15, 120),
    ("Amul Taaza Milk (500ml)", "Dairy & Bakery", 2800, 4, 12, 84),
    ("Parle-G Gold Biscuits Pack", "Snacks", 2000, 35, 10, 60),
    ("Veg Dum Biryani (Full)", "Hotel Menu", 18000, 14, 6, 51),
    ("Tata Salt Iodized (1kg)", "Groceries", 2800, 3, 10, 49),
]


def seed_demo(con, owner_id):
    now = dt_iso()
    mapping = {}
    for name, category, price, stock, minimum, sold in DEMO_PRODUCTS:
        cursor = con.execute("""
            INSERT INTO products(owner_id,name,category,price_paise,stock,min_stock,sold_count,created_at)
            VALUES(?,?,?,?,?,?,?,?)
        """, (owner_id, name, category, price, stock, minimum, sold, now))
        sample_cost = int(price * 0.70)
        con.execute("UPDATE products SET cost_paise=? WHERE id=?", (sample_cost, cursor.lastrowid))
        mapping[name] = (cursor.lastrowid, price, sample_cost)
    samples = [
        ("Ananya Verma", "UPI / QR", [("Veg Dum Biryani (Full)", 3)], 13, 20),
        ("Walk-in Customer", "Cash",
         [("Freedom Sunflower Oil (1L)", 2), ("Parle-G Gold Biscuits Pack", 3)], 16, 30),
        ("Ramesh Sharma", "Card",
         [("India Gate Basmati Rice (5kg)", 1), ("Toor Dal Premium (1kg)", 2)], 18, 15),
        ("Table #4 (Hotel)", "UPI / QR",
         [("Special Masala Dosa", 2), ("South Indian Filter Coffee", 2)], 19, 45),
    ]
    local_today = utcnow().astimezone(TIMEZONE).date()
    for number, (customer, payment, entries, hour, minute) in enumerate(samples, start=1):
        subtotal = sum(mapping[name][1] * qty for name, qty in entries)
        gst = (subtotal * 5 + 50) // 100
        created = datetime.combine(local_today, time(hour, minute), TIMEZONE)
        created_at = dt_iso(created.astimezone(timezone.utc))
        cur = con.execute("""
            INSERT INTO invoices(owner_id,invoice_number,customer,subtotal_paise,gst_paise,
                                 total_paise,payment,created_at) VALUES(?,?,?,?,?,?,?,?)
        """, (owner_id, number, customer, subtotal, gst, subtotal + gst, payment, created_at))
        for name, qty in entries:
            product_id, price, cost = mapping[name]
            con.execute("""INSERT INTO invoice_items(invoice_id,product_id,name,price_paise,cost_paise,qty)
                           VALUES(?,?,?,?,?,?)""", (cur.lastrowid, product_id, name, price, cost, qty))


@app.get("/")
def frontend():
    return FileResponse(ROOT / "frontend" / "digital_inventory_and_sales_management_system.html")


@app.get("/api.js")
def frontend_api():
    return FileResponse(ROOT / "frontend" / "api.js", media_type="application/javascript")


@app.get("/ai_models.js")
def ai_frontend_script():
    return FileResponse(ROOT / "frontend" / "ai_models.js", media_type="application/javascript")


@app.get("/neon-signup")
def neon_signup_page():
    return FileResponse(ROOT / "frontend" / "neon_signup.html")


@app.get("/neon_auth_bundle.js")
def neon_signup_bundle():
    return FileResponse(ROOT / "frontend" / "neon_auth_bundle.js", media_type="application/javascript")


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "InvenTrack", "demoOnly": os.getenv("INVENTRACK_DEMO_ONLY") == "1", "database": "postgresql" if os.getenv("DATABASE_URL") else "sqlite-ephemeral"}


@app.post("/api/auth/register", status_code=201)
def register(payload: Register, request: Request, response: Response):
    if os.getenv("INVENTRACK_DEMO_ONLY") == "1":
        raise HTTPException(403, "Public demo mode: registration is disabled")
    enforce_limit(request, "register", 8, 3600)
    with database() as con:
        try:
            cur = con.execute("""
                INSERT INTO owners(email,mobile,password_hash,business_name,business_type,created_at)
                VALUES(?,?,?,?,?,?)
            """, (payload.email, payload.mobile, hash_password(payload.password),
                  payload.businessName.strip(), payload.businessType.strip(), dt_iso()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "An account with this email already exists")
        set_session(con, cur.lastrowid, response, request)
        row = con.execute("SELECT * FROM owners WHERE id=?", (cur.lastrowid,)).fetchone()
        return {"owner": owner_json(row)}


@app.post("/api/auth/login")
def login(payload: Login, request: Request, response: Response):
    if os.getenv("INVENTRACK_DEMO_ONLY") == "1":
        raise HTTPException(403, "Public demo mode: sign in using Demo Login")
    enforce_limit(request, "login-ip", 12, 600)
    email = (payload.email or "").strip().lower()
    mobile = normalize_login_mobile(payload.mobile or "")
    identifier = (payload.identifier or "").strip()
    if identifier:
        if email or payload.mobile:
            raise HTTPException(400, "Use one login method")
        if "@" in identifier:
            email = identifier.lower()
        else:
            mobile = normalize_login_mobile(identifier)
    if (payload.mobile and not mobile) or (email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)) or not (email or mobile):
        raise HTTPException(401, "Invalid login details")
    enforce_limit(request, "login-identity", 16, 3600, email or mobile)
    with database() as con:
        if email and mobile:
            rows = con.execute("SELECT * FROM owners WHERE email=? AND mobile=? AND is_demo=0",
                               (email, mobile)).fetchall()
        elif email:
            rows = con.execute("SELECT * FROM owners WHERE email=? AND is_demo=0", (email,)).fetchall()
        else:
            # Legacy businesses can share numbers; never guess which account to open.
            rows = con.execute("SELECT * FROM owners WHERE mobile=? AND is_demo=0 LIMIT 2", (mobile,)).fetchall()
        row = rows[0] if len(rows) == 1 else None
        if not row or not verify_password(payload.password, row["password_hash"]):
            raise HTTPException(401, "Invalid login details. If you share a mobile number, use your email.")
        set_session(con, row["id"], response, request)
        return {"owner": owner_json(row)}


@app.post("/api/auth/demo")
def demo_login(request: Request, response: Response):
    enforce_limit(request, "demo", 8, 3600)
    with database() as con:
        # Expire old demo shops to avoid filling free PostgreSQL storage.
        con.execute("DELETE FROM owners WHERE is_demo=1 AND created_at<?",
                    (dt_iso(utcnow() - timedelta(days=1)),))
        identity = secrets.token_hex(12)
        cur = con.execute("""
            INSERT INTO owners(email,mobile,password_hash,business_name,business_type,is_demo,created_at)
            VALUES(?,?,?,?,?,1,?)
        """, ("demo-" + identity + "@example.invalid", "9876543210", "",
              "Sri Lakshmi Supermart & Tiffins", "Supermart & Hotel", dt_iso()))
        seed_demo(con, cur.lastrowid)
        set_session(con, cur.lastrowid, response, request)
        row = con.execute("SELECT * FROM owners WHERE id=?", (cur.lastrowid,)).fetchone()
        return {"owner": owner_json(row)}


@app.get("/api/session")
def session(owner=Depends(current_owner)):
    return {"owner": owner_json(owner)}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with database() as con:
            con.execute("DELETE FROM sessions WHERE token_hash=?",
                        (hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/products")
def list_products(owner=Depends(current_owner)):
    with database() as con:
        return [product_json(x) for x in con.execute(
            "SELECT * FROM products WHERE owner_id=? ORDER BY id DESC", (owner["id"],)).fetchall()]


@app.post("/api/products", status_code=201)
def create_product(payload: ProductIn, owner=Depends(current_owner)):
    with database() as con:
        cur = con.execute("""
            INSERT INTO products(owner_id,name,category,price_paise,cost_paise,stock,min_stock,created_at)
            VALUES(?,?,?,?,?,?,?,?)
        """, (owner["id"], payload.name.strip(), payload.category.strip(),
              money_to_paise(payload.price),
              money_to_paise(payload.costPrice) if payload.costPrice is not None else None,
              payload.stock, payload.minStock, dt_iso()))
        row = con.execute("SELECT * FROM products WHERE id=?", (cur.lastrowid,)).fetchone()
        return product_json(row)


@app.patch("/api/products/{product_id}")
def edit_product(product_id: int, payload: ProductUpdate, owner=Depends(current_owner)):
    changes = payload.model_dump(exclude_none=True)
    names = {"name": "name", "category": "category", "price": "price_paise",
             "stock": "stock", "minStock": "min_stock", "costPrice": "cost_paise"}
    with database() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM products WHERE id=? AND owner_id=?",
                          (product_id, owner["id"])).fetchone()
        if not row:
            raise HTTPException(404, "Product not found")
        if changes:
            updates = {names[key]: money_to_paise(value) if key in ("price", "costPrice") else
                       value.strip() if isinstance(value, str) else value
                       for key, value in changes.items()}
            sets = ", ".join(column + "=?" for column in updates)
            con.execute("UPDATE products SET " + sets + " WHERE id=? AND owner_id=?",
                        (*updates.values(), product_id, owner["id"]))
        return product_json(con.execute("SELECT * FROM products WHERE id=?",
                                        (product_id,)).fetchone())


@app.post("/api/products/{product_id}/restock")
def restock(product_id: int, payload: Restock, owner=Depends(current_owner)):
    with database() as con:
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute("""
            UPDATE products SET stock=stock+? WHERE id=? AND owner_id=?
        """, (payload.qty, product_id, owner["id"]))
        if cur.rowcount == 0:
            raise HTTPException(404, "Product not found")
        return product_json(con.execute("SELECT * FROM products WHERE id=?",
                                        (product_id,)).fetchone())


@app.delete("/api/products/{product_id}")
def delete_product(product_id: int, owner=Depends(current_owner)):
    with database() as con:
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute("DELETE FROM products WHERE id=? AND owner_id=?",
                          (product_id, owner["id"]))
        if cur.rowcount == 0:
            raise HTTPException(404, "Product not found")
        return {"ok": True}


@app.post("/api/invoices", status_code=201)
def checkout(payload: Checkout, request: Request, owner=Depends(current_owner)):
    enforce_limit(request, "checkout", 90, 3600)
    if payload.payment not in PAYMENTS:
        raise HTTPException(422, "Unsupported payment method")
    if payload.payment == "Online (Razorpay)" and not gateway_enabled():
        raise HTTPException(503, "Online payments are not configured")
    ids = [item.id for item in payload.items]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "Duplicate cart item")
    with database() as con:
        con.execute("BEGIN IMMEDIATE")
        products = {}
        subtotal = 0
        for item in payload.items:
            product = con.execute("SELECT * FROM products WHERE id=? AND owner_id=?",
                                  (item.id, owner["id"])).fetchone()
            if not product:
                raise HTTPException(404, "Product unavailable")
            if product["stock"] < item.qty:
                raise HTTPException(409, "Not enough stock for " + product["name"])
            products[item.id] = product
            subtotal += product["price_paise"] * item.qty
        gst = (subtotal * 5 + 50) // 100 if payload.includeGst else 0
        next_invoice = con.execute(
            "SELECT COALESCE(MAX(invoice_number),0)+1 AS num FROM invoices WHERE owner_id=?",
            (owner["id"],)).fetchone()["num"]
        cur = con.execute("""
            INSERT INTO invoices(owner_id,invoice_number,customer,subtotal_paise,gst_paise,
                                 total_paise,payment,created_at,payment_status) VALUES(?,?,?,?,?,?,?,?,?)
        """, (owner["id"], next_invoice, payload.customer.strip() or "Walk-in Customer",
              subtotal, gst, subtotal + gst, payload.payment, dt_iso(),
              "pending" if payload.payment == "Online (Razorpay)" else "recorded"))
        for item in payload.items:
            product = products[item.id]
            con.execute("UPDATE products SET stock=stock-?, sold_count=sold_count+? WHERE id=?",
                        (item.qty, item.qty, item.id))
            con.execute("""
                INSERT INTO invoice_items(invoice_id,product_id,name,price_paise,cost_paise,qty)
                VALUES(?,?,?,?,?,?)
            """, (cur.lastrowid, item.id, product["name"], product["price_paise"],
                  product["cost_paise"], item.qty))
        row = con.execute("SELECT * FROM invoices WHERE id=?", (cur.lastrowid,)).fetchone()
        return invoice_json(con, row)


@app.get("/api/invoices")
def list_invoices(limit: int = 100, offset: int = 0, owner=Depends(current_owner)):
    if limit < 1 or limit > 500 or offset < 0:
        raise HTTPException(422, "Invalid pagination")
    with database() as con:
        return invoices_for(con, owner["id"], limit, offset)


@app.get("/api/invoices/{invoice_number}")
def invoice_detail(invoice_number: int, owner=Depends(current_owner)):
    with database() as con:
        row = con.execute("SELECT * FROM invoices WHERE owner_id=? AND invoice_number=?",
                          (owner["id"], invoice_number)).fetchone()
        if not row:
            raise HTTPException(404, "Invoice not found")
        return invoice_json(con, row)


@app.get("/api/dashboard")
def dashboard(owner=Depends(current_owner)):
    with database() as con:
        return dashboard_for(con, owner["id"])


@app.get("/api/bootstrap")
def bootstrap(owner=Depends(current_owner)):
    with database() as con:
        products = [product_json(x) for x in con.execute(
            "SELECT * FROM products WHERE owner_id=? ORDER BY id DESC",
            (owner["id"],)).fetchall()]
        number = con.execute(
            "SELECT COALESCE(MAX(invoice_number),0)+1 AS num FROM invoices WHERE owner_id=?",
            (owner["id"],)).fetchone()["num"]
        return {"owner": owner_json(owner), "inventory": products,
                "bills": invoices_for(con, owner["id"]), "invoiceCounter": 1000 + number,
                "dashboard": dashboard_for(con, owner["id"]),
        "gatewayEnabled": gateway_enabled()}


@app.post("/api/assistant")
def assistant(prompt: AssistantPrompt, request: Request, owner=Depends(current_owner)):
    """Backwards-compatible endpoint, using the configured staff-assistant model."""
    from backend.ai_models import answer_assistant
    enforce_limit(request, "ai-chat", 40, 3600, str(owner["id"]))
    return {"reply": answer_assistant(prompt.message, owner, "staff")}


from backend.payments import gateway_enabled, router as payment_router
app.include_router(payment_router)

from backend.ai_models import router as ai_router
app.include_router(ai_router)

from backend.passwordless import router as otp_router
app.include_router(otp_router)
