"""Real PostgreSQL integration checks. Set TEST_POSTGRES_URL to an isolated TEST database.

These tests DROP THE PUBLIC SCHEMA. NEVER set TEST_POSTGRES_URL to a production DB.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from psycopg import connect

from backend import app as server
from backend import ai_models
from backend.migrate_to_postgres import copy_source

TEST_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="TEST_POSTGRES_URL is not configured")


@pytest.fixture
def client(monkeypatch):
    # Explicit guard: destructive tests may ONLY target a local test server.
    from urllib.parse import urlparse
    url = urlparse(TEST_URL)
    if url.hostname not in ("127.0.0.1", "localhost") or url.path != "/inven_test":
        pytest.fail("PostgreSQL tests are restricted to local inven_test")
    with connect(TEST_URL) as conn:
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.commit()
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.delenv("INVENTRACK_DEMO_ONLY", raising=False)
    monkeypatch.setenv("INVENTRACK_AI_KEY", Fernet.generate_key().decode("ascii"))
    with TestClient(server.app) as c:
        yield c


def test_accounts_products_invoice_ai_and_restart(client):
    assert client.get("/api/health").json()["database"] == "postgresql"
    reg = client.post("/api/auth/register", json={
        "email": "pg-test@example.com", "mobile": "9123456789",
        "password": "StrongPass123!", "businessName": "PG Store", "businessType": "Supermart"
    })
    assert reg.status_code == 201, reg.text
    assert client.post("/api/auth/register", json={
        "email": "pg-test@example.com", "mobile": "9123456789",
        "password": "StrongPass123!", "businessName": "PG Store", "businessType": "Supermart"
    }).status_code == 409
    product = client.post("/api/products", json={
        "name": "Chai", "category": "Beverages", "price": 20,
        "costPrice": 8, "stock": 3, "minStock": 1
    })
    assert product.status_code == 201, product.text
    pid = product.json()["id"]
    invoice = client.post("/api/invoices", json={
        "items": [{"id": pid, "qty": 2}], "customer": "PG Test",
        "payment": "Cash", "includeGst": False
    })
    assert invoice.status_code == 201, invoice.text
    assert invoice.json()["grossProfit"] == 24.0
    model = client.post("/api/ai/models", json={
        "name": "Test Model", "provider": "openai", "protocol": "openai",
        "endpoint": "https://api.openai.com/v1",
        "modelId": "test-model", "apiKey": "secret-encrypted-in-postgres"
    })
    assert model.status_code == 201, model.text
    with server.database() as db:
        key = db.execute("SELECT encrypted_key FROM ai_model_configs").fetchone()["encrypted_key"]
    assert "secret-encrypted-in-postgres" not in key
    assert ai_models.decrypt_key(key) == "secret-encrypted-in-postgres"
    with TestClient(server.app) as second:
        assert second.post("/api/auth/login", json={
            "email": "pg-test@example.com", "mobile": "9123456789",
            "password": "StrongPass123!"
        }).status_code == 200
        assert second.get("/api/products").json()[0]["stock"] == 1
        assert len(second.get("/api/invoices").json()) == 1
        assert len(second.get("/api/ai/config").json()["models"]) == 1
    assert client.get("/api/dashboard").json()["grossProfit"] == 24.0


def test_demo_tenant_and_stock_isolation(client):
    a = client.post("/api/auth/demo")
    assert a.status_code == 200, a.text
    data = client.get("/api/bootstrap").json()
    assert len(data["inventory"]) >= 5
    with TestClient(server.app) as other:
        assert other.get("/api/products").status_code == 401
        assert other.post("/api/auth/demo").status_code == 200
        assert other.get("/api/ai/config").json()["models"] == []
        assert other.get("/api/products").json()[0]["id"] != data["inventory"][0]["id"]
    assert len(client.get("/api/products").json()) == len(data["inventory"])


def test_backup_encryption_authentication(client, tmp_path, monkeypatch):
    from backend.postgres_backup import encrypt, decrypt
    key = Fernet.generate_key()
    import base64
    raw = base64.urlsafe_b64decode(key)
    src = tmp_path / "source.dump"
    src.write_bytes(os.urandom(256_000))
    crypt = tmp_path / "source.vfpga"
    encrypt(src, crypt, raw)
    output = tmp_path / "restored.dump"
    decrypt(crypt, output, raw)
    assert output.read_bytes() == src.read_bytes()
    tampered = bytearray(crypt.read_bytes())
    tampered[-40] ^= 1
    crypt.write_bytes(tampered)
    with pytest.raises(Exception):
        decrypt(crypt, output, raw)


def test_sqlite_to_postgres_copy_preserves_ids_and_refuses_overwrite(client, tmp_path, monkeypatch):
    from backend import app as app_module
    sqlite_path = tmp_path / "original.sqlite3"
    monkeypatch.setattr(app_module, "DB_PATH", sqlite_path)
    monkeypatch.delenv("DATABASE_URL")
    with TestClient(server.app) as source:
        registered = source.post("/api/auth/register", json={
            "email": "migration@example.com", "mobile": "9123456789",
            "password": "StrongPass123!", "businessName": "Original Shop",
            "businessType": "Grocery"
        })
        assert registered.status_code == 201, registered.text
        saved = source.post("/api/products", json={
            "name": "Fresh Milk", "category": "Dairy", "price": 22,
            "costPrice": 17, "stock": 4, "minStock": 2
        }).json()
        bill = source.post("/api/invoices", json={
            "items": [{"id": saved["id"], "qty": 2}], "customer": "Customer",
            "payment": "Cash", "includeGst": False
        })
        assert bill.status_code == 201, bill.text
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    preview = copy_source(sqlite_path, TEST_URL, commit=False)
    assert preview["dryRun"] and preview["counts"]["invoices"] == 1
    imported = copy_source(sqlite_path, TEST_URL, commit=True)
    assert imported["counts"]["owners"] == 1
    with TestClient(server.app) as dest:
        login = dest.post("/api/auth/login", json={
            "email": "migration@example.com", "mobile": "9123456789",
            "password": "StrongPass123!"
        })
        assert login.status_code == 200, login.text
        product = dest.get("/api/products").json()[0]
        assert product["id"] == saved["id"]
        assert product["stock"] == 2
        assert len(dest.get("/api/invoices").json()) == 1
        assert dest.post("/api/products", json={
            "name": "Another Product", "category": "Dairy", "price": 10,
            "stock": 1, "minStock": 1
        }).json()["id"] > saved["id"]
    with pytest.raises(RuntimeError, match="not empty"):
        copy_source(sqlite_path, TEST_URL, commit=True)
    assert sqlite_path.exists()
