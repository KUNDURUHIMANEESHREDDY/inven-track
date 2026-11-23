"""Extended security, profit, encrypted backup and simulated gateway tests."""
import hashlib
import hmac
import json
import sqlite3

import pytest
from cryptography.fernet import Fernet, InvalidToken
from fastapi.testclient import TestClient
from backend import app as server
from backend import payments, backup


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "prod-tests.sqlite3")
    with TestClient(server.app) as c:
        yield c


def owner(client, suffix="test"):
    result = client.post("/api/auth/register", json={
        "email": suffix + "@example.com", "mobile": "1234567890",
        "password": "StrongPass123!", "businessName": "Cost Shop", "businessType": "Store"
    })
    assert result.status_code == 201, result.text


def add(client, cost=None):
    payload = {"name": "Sample", "category": "Groceries", "price": "20.00",
               "stock": 12, "minStock": 3}
    if cost is not None:
        payload["costPrice"] = cost
    resp = client.post("/api/products", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def sale(client, product, payment="Cash", qty=2):
    return client.post("/api/invoices", json={
        "customer": "Example", "payment": payment, "includeGst": True,
        "items": [{"id": product["id"], "qty": qty}]
    })


def test_gross_profit_snapshot_and_unknown_cost(client):
    owner(client)
    known = add(client, "12.50")
    assert known["costPrice"] == 12.5
    a = sale(client, known)
    assert a.status_code == 201, a.text
    assert a.json()["grossProfit"] == 15.0
    overview = client.get("/api/dashboard").json()
    assert overview["grossProfit"] == 15.0
    assert overview["grossMarginPercent"] == 37.5
    assert overview["profitAvailable"]
    changed = client.patch("/api/products/" + str(known["id"]),
                           json={"costPrice": 19.0})
    assert changed.status_code == 200
    assert client.get("/api/invoices/1").json()["grossProfit"] == 15.0
    missing = add(client)
    assert missing["costPrice"] is None
    assert sale(client, missing).status_code == 201
    assert client.get("/api/dashboard").json()["profitAvailable"] is False
    assert client.get("/api/dashboard").json()["grossProfit"] is None


def test_schema_migration_does_not_destroy_user_records(client):
    owner(client)
    saved = add(client, "4.70")
    with server.database() as con:
        server.migrate_schema(con)
        server.migrate_schema(con)
        count = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    assert count == 1
    assert client.get("/api/products").json()[0]["costPrice"] == 4.7


def test_rate_limiting_login(client):
    # All these are failed logins, but each hits the same remote-IP rate limit.
    payload = {"email": "stranger@example.com", "mobile": "1234567890",
               "password": "not-good"}
    for _ in range(12):
        assert client.post("/api/auth/login", json=payload).status_code == 401
    blocked = client.post("/api/auth/login", json=payload)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1


def test_online_payments_prove_captured_funds(client, monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_dummy_for_unit_tests")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake_test_secret")
    monkeypatch.setenv("RAZORPAY_MODE", "test")
    owner(client)
    p = add(client, "10.00")
    inv = sale(client, p, "Online (Razorpay)")
    assert inv.status_code == 201, inv.text
    assert inv.json()["paymentStatus"] == "pending"
    assert client.get("/api/dashboard").json()["todaySales"] == 0
    calls = []
    def fake_provider(method, path, payload=None):
        calls.append((method, path))
        if path == "/orders":
            assert payload["amount"] == 4200
            return {"id": "order_fake1", "amount": 4200, "currency": "INR"}
        if path == "/payments/pay_fake1":
            return {"id": "pay_fake1", "order_id": "order_fake1",
                    "amount": 4200, "currency": "INR", "status": "captured"}
        raise AssertionError(path)
    monkeypatch.setattr(payments, "provider_call", fake_provider)
    order = client.post("/api/payments/orders/1")
    assert order.status_code == 200, order.text
    assert order.json()["keyId"] == "rzp_test_dummy_for_unit_tests"
    assert client.post("/api/payments/orders/1").json()["orderId"] == "order_fake1"
    assert calls.count(("POST", "/orders")) == 1
    bad = client.post("/api/payments/verify", json={
        "invoiceNumber": 1, "razorpay_order_id": "order_fake1",
        "razorpay_payment_id": "pay_fake1", "razorpay_signature": "0" * 64
    })
    assert bad.status_code == 400
    assert client.get("/api/dashboard").json()["todaySales"] == 0
    signature = hmac.new(b"fake_test_secret", b"order_fake1|pay_fake1",
                         hashlib.sha256).hexdigest()
    payload = {"invoiceNumber": 1, "razorpay_order_id": "order_fake1",
               "razorpay_payment_id": "pay_fake1", "razorpay_signature": signature}
    verified = client.post("/api/payments/verify", json=payload)
    assert verified.status_code == 200, verified.text
    assert verified.json()["invoice"]["paymentStatus"] == "paid"
    assert client.post("/api/payments/verify", json=payload).status_code == 200
    assert client.get("/api/dashboard").json()["todaySales"] == 42.0
    assert client.post("/api/payments/orders/1").status_code == 409
    assert client.post("/api/payments/void/1").status_code == 409


def test_signed_webhook_dedupe_and_foreign_order(client, monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_dummy_for_unit_tests")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake_test_secret")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "webhook_unit_secret")
    owner(client)
    p = add(client, "10.00")
    inv = sale(client, p, "Online (Razorpay)")
    assert inv.status_code == 201
    monkeypatch.setattr(payments, "provider_call",
                        lambda method, path, payload=None: {
                            "id": "order_webhook", "amount": 4200, "currency": "INR"
                        })
    assert client.post("/api/payments/orders/1").status_code == 200
    event = {"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": "pay_webhook", "order_id": "order_webhook",
        "amount": 4200, "currency": "INR", "status": "captured"
    }}}}
    body = json.dumps(event).encode()
    good = hmac.new(b"webhook_unit_secret", body, hashlib.sha256).hexdigest()
    assert client.post("/api/payments/webhook", content=body, headers={
        "X-Razorpay-Signature": "0" * 64, "X-Razorpay-Event-Id": "evt_test"
    }).status_code == 401
    accepted = client.post("/api/payments/webhook", content=body, headers={
        "X-Razorpay-Signature": good, "X-Razorpay-Event-Id": "evt_test"
    })
    assert accepted.status_code == 200, accepted.text
    assert accepted.json().get("captured") is True
    duplicate = client.post("/api/payments/webhook", content=body, headers={
        "X-Razorpay-Signature": good, "X-Razorpay-Event-Id": "evt_test"
    })
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"]
    assert client.get("/api/invoices/1").json()["paymentStatus"] == "paid"


def test_encrypted_backup_restore(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTRACK_BACKUP_KEY", Fernet.generate_key().decode())
    source = tmp_path / "data.sqlite3"
    with sqlite3.connect(source) as con:
        con.execute("CREATE TABLE business (name TEXT)")
        con.execute("INSERT INTO business(name) VALUES ('test-business')")
    file_path = backup.backup_database(source, tmp_path / "encrypted", keep=2)
    encrypted = file_path.read_bytes()
    assert b"test-business" not in encrypted
    restored = backup.restore_database(file_path, tmp_path / "restored.sqlite3")
    with sqlite3.connect(restored) as con:
        assert con.execute("SELECT name FROM business").fetchone()[0] == "test-business"
    with pytest.raises(FileExistsError):
        backup.restore_database(file_path, restored)
    monkeypatch.setenv("INVENTRACK_BACKUP_KEY", Fernet.generate_key().decode())
    with pytest.raises(InvalidToken):
        backup.restore_database(file_path, tmp_path / "wrong.sqlite3")



def test_reconcile_unconfirmed_payment(client, monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_for_reconcile")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "test_secret")
    owner(client)
    p = add(client, "11.00")
    assert sale(client, p, "Online (Razorpay)").status_code == 201

    def fake_provider(method, path, payload=None):
        if path == "/orders":
            return {"id": "order_recovery1", "amount": 4200, "currency": "INR"}
        if path == "/orders/order_recovery1/payments":
            return {"items": [{"id": "pay_recovery1", "status": "captured",
                               "amount": 4200, "currency": "INR",
                               "order_id": "order_recovery1"}]}
        raise AssertionError(path)

    monkeypatch.setattr(payments, "provider_call", fake_provider)
    assert client.post("/api/payments/orders/1").status_code == 200
    reconciled = client.post("/api/payments/reconcile/1")
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["verified"]
    assert reconciled.json()["invoice"]["paymentStatus"] == "paid"
    assert client.get("/api/dashboard").json()["todaySales"] == 42.0
    assert client.post("/api/payments/reconcile/1").json()["verified"]


def test_live_payment_credential_guard(client, monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_example")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")
    monkeypatch.delenv("ENABLE_LIVE_PAYMENTS", raising=False)
    monkeypatch.setenv("RAZORPAY_MODE", "test")
    owner(client)
    assert not client.get("/api/bootstrap").json()["gatewayEnabled"]
    p = add(client, "4.00")
    assert sale(client, p, "Online (Razorpay)").status_code == 503
    assert client.get("/api/invoices").json() == []


def test_payment_cross_tenant_blocked(client, monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_cross_tenant")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")
    owner(client, "firstowner")
    first = add(client, "4.00")
    assert sale(client, first, "Online (Razorpay)").status_code == 201
    client.post("/api/auth/logout")
    owner(client, "otherowner")
    assert client.post("/api/payments/orders/1").status_code == 404
    assert client.post("/api/payments/reconcile/1").status_code == 404
    assert client.get("/api/payments/status/1").status_code == 404
