"""Apply version-2 profit and payment data changes to the existing app source.
Idempotent source patch; DB schema migrates non-destructively at next startup.
"""
from pathlib import Path
p = Path(__file__).with_name("app.py")
s = p.read_text(encoding="utf-8")

def change(old, new):
    global s
    assert old in s or new in s, "Source fragment not found: " + old[:100]
    if old in s:
        s = s.replace(old, new, 1)

change('PAYMENTS = {"Cash", "Card", "UPI / QR"}',
       'PAYMENTS = {"Cash", "Card", "UPI / QR", "Online (Razorpay)"}')

change('''        """)\n\n\n@asynccontextmanager''',
'''        """)\n        migrate_schema(con)\n\n\ndef migrate_schema(con):\n    """Keep existing user records and invoices while introducing nullable costs."""\n    for table, column, declaration in (\n        ("products", "cost_paise", "INTEGER CHECK(cost_paise >= 0)"),\n        ("invoice_items", "cost_paise", "INTEGER CHECK(cost_paise >= 0)"),\n        ("invoices", "payment_status", "TEXT NOT NULL DEFAULT 'recorded'"),\n    ):\n        existing = {row["name"] for row in con.execute("PRAGMA table_info(" + table + ")")}\n        if column not in existing:\n            con.execute("ALTER TABLE " + table + " ADD COLUMN " + column + " " + declaration)\n    con.executescript("""\n        CREATE TABLE IF NOT EXISTS rate_limits (\n            bucket TEXT PRIMARY KEY, window_start INTEGER NOT NULL, hits INTEGER NOT NULL\n        );\n        CREATE TABLE IF NOT EXISTS payment_attempts (\n            invoice_id INTEGER PRIMARY KEY REFERENCES invoices(id) ON DELETE CASCADE,\n            gateway_order_id TEXT UNIQUE,\n            gateway_payment_id TEXT UNIQUE,\n            amount_paise INTEGER NOT NULL,\n            status TEXT NOT NULL,\n            updated_at TEXT NOT NULL\n        );\n        CREATE TABLE IF NOT EXISTS payment_webhook_events (\n            event_id TEXT PRIMARY KEY, received_at TEXT NOT NULL\n        );\n    """)\n\n\n@asynccontextmanager''')

change('''    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"\n    return response''',
'''    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"\n    response.headers["X-Frame-Options"] = "DENY"\n    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"\n    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"\n    if request.url.scheme == "https":\n        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"\n    if request.url.path.startswith("/api/"):\n        response.headers["Cache-Control"] = "no-store"\n    return response''')

change('''class Register(BaseModel):''', '''def enforce_limit(request: Request, scope: str, max_hits: int, interval: int, identity: str = ""):\n    """SQLite-backed, multi-process fixed-window throttling; no proxy-spoofed client IP."""\n    address = request.client.host if request.client else "unknown"\n    bucket = hashlib.sha256((scope + "|" + address + "|" + identity.lower()).encode()).hexdigest()\n    now = int(utcnow().timestamp())\n    window_start = now - (now % interval)\n    with database() as con:\n        con.execute("BEGIN IMMEDIATE")\n        row = con.execute("SELECT window_start,hits FROM rate_limits WHERE bucket=?",\n                          (bucket,)).fetchone()\n        if not row or row["window_start"] != window_start:\n            con.execute("""INSERT INTO rate_limits(bucket,window_start,hits) VALUES(?,?,1)\n                        ON CONFLICT(bucket) DO UPDATE SET window_start=excluded.window_start,hits=1""",\n                        (bucket, window_start))\n        elif row["hits"] >= max_hits:\n            delay = interval - now % interval\n            raise HTTPException(429, "Too many requests; try again later",\n                                headers={"Retry-After": str(delay)})\n        else:\n            con.execute("UPDATE rate_limits SET hits=hits+1 WHERE bucket=?", (bucket,))\n\n\nclass Register(BaseModel):''')

change('''    minStock: int = Field(ge=0, le=100000000)\n\n\nclass ProductUpdate''',
'''    minStock: int = Field(ge=0, le=100000000)\n    costPrice: Decimal | None = Field(default=None, ge=0, le=100000000)\n\n\nclass ProductUpdate''')
change('''    minStock: int | None = Field(default=None, ge=0, le=100000000)\n\n\nclass Restock''',
'''    minStock: int | None = Field(default=None, ge=0, le=100000000)\n    costPrice: Decimal | None = Field(default=None, ge=0, le=100000000)\n\n\nclass Restock''')
change('''        "minStock": row["min_stock"], "soldCount": row["sold_count"]''',
'''        "minStock": row["min_stock"], "soldCount": row["sold_count"],\n        "costPrice": rupees(row["cost_paise"]) if row["cost_paise"] is not None else None''')

change('''"SELECT name, qty, price_paise, product_id FROM invoice_items WHERE invoice_id=? ORDER BY id"''',
'''"SELECT name, qty, price_paise, cost_paise, product_id FROM invoice_items WHERE invoice_id=? ORDER BY id"''')
change('''"price": rupees(it["price_paise"]), "qty": it["qty"]} for it in items],''',
'''"price": rupees(it["price_paise"]), "qty": it["qty"]} for it in items],''')
change('''        "createdAt": row["created_at"],''',
'''        "createdAt": row["created_at"],\n        "paymentStatus": row["payment_status"],\n        "grossProfit": (rupees(sum((it["price_paise"] - it["cost_paise"]) * it["qty"]\n                            for it in items)) if all(it["cost_paise"] is not None for it in items)\n                        else None),''')

change('''    rows = con.execute(\n        "SELECT total_paise, created_at FROM invoices WHERE owner_id=? AND created_at>=?",\n        (owner_id, since)).fetchall()''',
'''    rows = con.execute("""\n        SELECT i.subtotal_paise, i.total_paise, i.created_at,\n               (SELECT SUM(t.cost_paise * t.qty) FROM invoice_items t\n                WHERE t.invoice_id=i.id) AS cogs,\n               (SELECT COUNT(*) FROM invoice_items t\n                WHERE t.invoice_id=i.id AND t.cost_paise IS NULL) AS missing_cost\n        FROM invoices i\n        WHERE i.owner_id=? AND i.created_at>=?\n          AND i.payment_status IN ('recorded','paid')\n    """, (owner_id, since)).fetchall()''')
change('''    counts = {today: 0}\n    for row in rows:''',
'''    counts = {today: 0}\n    gross_profit_paise = 0\n    sale_subtotals_paise = 0\n    missing_costs = False\n    for row in rows:''')
change('''            if day == today:\n                counts[today] += 1''',
'''            if day == today:\n                counts[today] += 1\n                sale_subtotals_paise += row["subtotal_paise"]\n                if row["missing_cost"]:\n                    missing_costs = True\n                else:\n                    gross_profit_paise += row["subtotal_paise"] - (row["cogs"] or 0)''')
change('''        "profitAvailable": False,''',
'''        "profitAvailable": not missing_costs,\n        "grossProfit": rupees(gross_profit_paise) if not missing_costs else None,\n        "grossMarginPercent": (round(100 * gross_profit_paise / sale_subtotals_paise, 2)\n                               if not missing_costs and sale_subtotals_paise else None),''')

change('''        mapping[name] = (cursor.lastrowid, price)''',
'''        sample_cost = int(price * 0.70)\n        con.execute("UPDATE products SET cost_paise=? WHERE id=?", (sample_cost, cursor.lastrowid))\n        mapping[name] = (cursor.lastrowid, price, sample_cost)''')
change('''        subtotal = sum(mapping[name][1] * qty for name, qty in entries)''',
'''        subtotal = sum(mapping[name][1] * qty for name, qty in entries)''')
change('''            product_id, price = mapping[name]\n            con.execute("""INSERT INTO invoice_items(invoice_id,product_id,name,price_paise,qty)\n                           VALUES(?,?,?,?,?)""", (cur.lastrowid, product_id, name, price, qty))''',
'''            product_id, price, cost = mapping[name]\n            con.execute("""INSERT INTO invoice_items(invoice_id,product_id,name,price_paise,cost_paise,qty)\n                           VALUES(?,?,?,?,?,?)""", (cur.lastrowid, product_id, name, price, cost, qty))''')

change('''def register(payload: Register, request: Request, response: Response):\n    with database()''',
'''def register(payload: Register, request: Request, response: Response):\n    enforce_limit(request, "register", 8, 3600)\n    with database()''')
change('''def login(payload: Login, request: Request, response: Response):\n    with database()''',
'''def login(payload: Login, request: Request, response: Response):\n    enforce_limit(request, "login-ip", 12, 600)\n    enforce_limit(request, "login-email", 16, 3600, payload.email.strip()[:254])\n    with database()''')
change('''def demo_login(request: Request, response: Response):\n    with database()''',
'''def demo_login(request: Request, response: Response):\n    enforce_limit(request, "demo", 8, 3600)\n    with database()''')

change('''            INSERT INTO products(owner_id,name,category,price_paise,stock,min_stock,created_at)\n            VALUES(?,?,?,?,?,?,?)\n        """, (owner["id"], payload.name.strip(), payload.category.strip(),\n              money_to_paise(payload.price), payload.stock, payload.minStock, dt_iso()))''',
'''            INSERT INTO products(owner_id,name,category,price_paise,cost_paise,stock,min_stock,created_at)\n            VALUES(?,?,?,?,?,?,?,?)\n        """, (owner["id"], payload.name.strip(), payload.category.strip(),\n              money_to_paise(payload.price),\n              money_to_paise(payload.costPrice) if payload.costPrice is not None else None,\n              payload.stock, payload.minStock, dt_iso()))''')
change('''             "stock": "stock", "minStock": "min_stock"}''',
'''             "stock": "stock", "minStock": "min_stock", "costPrice": "cost_paise"}''')
change('''updates = {names[key]: money_to_paise(value) if key == "price" else''',
'''updates = {names[key]: money_to_paise(value) if key in ("price", "costPrice") else''')
change('''    if payload.payment not in PAYMENTS:\n        raise HTTPException(422, "Unsupported payment method")''',
'''    if payload.payment not in PAYMENTS:\n        raise HTTPException(422, "Unsupported payment method")\n    if payload.payment == "Online (Razorpay)" and not (\n        os.getenv("RAZORPAY_KEY_ID") and os.getenv("RAZORPAY_KEY_SECRET")\n    ):\n        raise HTTPException(503, "Online payments are not configured")''')
change('''def checkout(payload: Checkout, owner=Depends(current_owner)):''',
'''def checkout(payload: Checkout, request: Request, owner=Depends(current_owner)):\n    enforce_limit(request, "checkout", 90, 3600)''')
change('''INSERT INTO invoices(owner_id,invoice_number,customer,subtotal_paise,gst_paise,\n                                 total_paise,payment,created_at) VALUES(?,?,?,?,?,?,?,?)\n        """, (owner["id"], next_invoice, payload.customer.strip() or "Walk-in Customer",\n              subtotal, gst, subtotal + gst, payload.payment, dt_iso()))''',
'''INSERT INTO invoices(owner_id,invoice_number,customer,subtotal_paise,gst_paise,\n                                 total_paise,payment,created_at,payment_status) VALUES(?,?,?,?,?,?,?,?,?)\n        """, (owner["id"], next_invoice, payload.customer.strip() or "Walk-in Customer",\n              subtotal, gst, subtotal + gst, payload.payment, dt_iso(),\n              "pending" if payload.payment == "Online (Razorpay)" else "recorded"))''')
change('''                INSERT INTO invoice_items(invoice_id,product_id,name,price_paise,qty)\n                VALUES(?,?,?,?,?)\n            """, (cur.lastrowid, item.id, product["name"], product["price_paise"], item.qty))''',
'''                INSERT INTO invoice_items(invoice_id,product_id,name,price_paise,cost_paise,qty)\n                VALUES(?,?,?,?,?,?)\n            """, (cur.lastrowid, item.id, product["name"], product["price_paise"],\n                  product["cost_paise"], item.qty))''')
change('''        "dashboard": dashboard_for(con, owner["id"])}''',
'''        "dashboard": dashboard_for(con, owner["id"]),\n        "gatewayEnabled": bool(os.getenv("RAZORPAY_KEY_ID") and os.getenv("RAZORPAY_KEY_SECRET"))}''')
s += '\n\nfrom backend.payments import router as payment_router\napp.include_router(payment_router)\n' if 'app.include_router(payment_router)' not in s else ''
p.write_text(s, encoding="utf-8")
print("V2 source changes applied")
