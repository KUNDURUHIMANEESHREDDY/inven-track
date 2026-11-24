"""API integration tests: real SQLite, cookies, stock transactions and tenant isolation."""
import pytest
from fastapi.testclient import TestClient
from backend import app as server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "test.sqlite3")
    with TestClient(server.app) as c:
        yield c


def account(client, email="owner@example.com", mobile="9876543210"):
    response = client.post("/api/auth/register", json={
        "email": email, "mobile": mobile, "password": "StrongPass123!",
        "businessName": "Example Market", "businessType": "Supermart"
    })
    assert response.status_code == 201, response.text
    return response.json()


def product(client, name="Tomato"):
    response = client.post("/api/products", json={
        "name": name, "category": "Groceries", "price": 12.5,
        "stock": 5, "minStock": 2
    })
    assert response.status_code == 201, response.text
    return response.json()


def checkout(client, product_id, qty=2, gst=True):
    return client.post("/api/invoices", json={
        "items": [{"id": product_id, "qty": qty}],
        "customer": "Test Customer", "payment": "Cash", "includeGst": gst
    })


def test_auth_persistence_and_logout(client):
    assert client.get("/api/products").status_code == 401
    account(client)
    saved = product(client)
    assert client.get("/api/products").json()[0]["id"] == saved["id"]
    assert client.get("/api/session").json()["owner"]["businessName"] == "Example Market"
    client.post("/api/auth/logout")
    assert client.get("/api/session").status_code == 401
    wrong = client.post("/api/auth/login", json={
        "email": "owner@example.com", "mobile": "9876543210", "password": "wrong"
    })
    assert wrong.status_code == 401
    good = client.post("/api/auth/login", json={
        "email": "owner@example.com", "mobile": "9876543210",
        "password": "StrongPass123!"
    })
    assert good.status_code == 200
    assert client.get("/api/products").json()[0]["id"] == saved["id"]


def test_checkout_recomputes_prices_and_prevents_oversell(client):
    account(client)
    saved = product(client)
    response = checkout(client, saved["id"], 2)
    assert response.status_code == 201, response.text
    bill = response.json()
    assert bill["subtotal"] == 25.0
    assert bill["gst"] == 1.25
    assert bill["total"] == 26.25
    assert client.get("/api/products").json()[0]["stock"] == 3
    assert client.get("/api/products").json()[0]["soldCount"] == 2
    second = checkout(client, saved["id"], 4)
    assert second.status_code == 409
    assert len(client.get("/api/invoices").json()) == 1
    assert client.get("/api/products").json()[0]["stock"] == 3
    assert client.get("/api/dashboard").json()["todaySales"] == 26.25
    assert client.get("/api/dashboard").json()["todayBills"] == 1


def test_deleted_products_do_not_destroy_old_receipts(client):
    account(client)
    saved = product(client)
    bill = checkout(client, saved["id"], 1, gst=False).json()
    assert client.delete("/api/products/" + str(saved["id"])).status_code == 200
    receipt = client.get("/api/invoices/1").json()
    assert receipt["id"] == bill["id"]
    assert receipt["items"][0]["name"] == "Tomato"
    assert receipt["subtotal"] == 12.5


def test_tenant_isolation(client):
    account(client, "one@example.com")
    first = product(client, "Secret Product")
    client.post("/api/auth/logout")
    account(client, "two@example.com", "8888888888")
    assert client.get("/api/products").json() == []
    assert client.post("/api/products/" + str(first["id"]) + "/restock",
                       json={"qty": 1}).status_code == 404
    assert client.delete("/api/products/" + str(first["id"])).status_code == 404
    assert checkout(client, first["id"]).status_code == 404
    assert client.get("/api/invoices/1").status_code == 404


def test_validation_and_duplicate_items(client):
    account(client)
    saved = product(client)
    assert client.post("/api/invoices", json={
        "items": [{"id": saved["id"], "qty": 1}, {"id": saved["id"], "qty": 1}]
    }).status_code == 422
    assert client.post("/api/products/" + str(saved["id"]) + "/restock",
                       json={"qty": -1}).status_code == 422
    assert client.post("/api/products", json={
        "name": "Bad", "category": "Other", "price": -9, "stock": 1, "minStock": 1
    }).status_code == 422
    assert client.get("/api/invoices").json() == []


def test_demo_store_is_preloaded_and_isolated(client):
    demo = client.post("/api/auth/demo")
    assert demo.status_code == 200
    assert demo.json()["owner"]["demo"] is True
    data = client.get("/api/bootstrap").json()
    assert len(data["inventory"]) == 9
    assert len(data["bills"]) == 4
    assert data["invoiceCounter"] == 1005
    assert data["dashboard"]["lowStock"] > 0
    client.post("/api/auth/logout")
    other = client.post("/api/auth/demo")
    assert other.status_code == 200
    assert other.json()["owner"]["email"] != demo.json()["owner"]["email"]


def test_frontend_and_health(client):
    assert client.get("/").status_code == 200
    assert 'api.js' in client.get("/").text
    assert client.get("/api.js").status_code == 200
    assert client.get("/api/health").json()["status"] == "ok"


def test_csrf_origin_rejected(client):
    response = client.post("/api/auth/demo", headers={"Origin": "https://outside.example"})
    assert response.status_code == 403


@pytest.mark.parametrize("identifier", ["OWNER@example.com", "9876543210", "+91 98765-43210"])
def test_login_with_either_identifier(client, identifier):
    owner = account(client)["owner"]
    saved = product(client)
    client.post("/api/auth/logout")
    response = client.post("/api/auth/login", json={"identifier": identifier, "password": "StrongPass123!"})
    assert response.status_code == 200, response.text
    assert response.json()["owner"]["id"] == owner["id"]
    assert client.get("/api/products").json()[0]["id"] == saved["id"]


def test_mobile_login_ambiguous_and_bad_password(client):
    account(client)
    client.post("/api/auth/logout")
    account(client, "other@example.com")
    client.post("/api/auth/logout")
    for identifier, password in [("9876543210", "StrongPass123!"), ("owner@example.com", "wrong"), ("+1 9876543210", "StrongPass123!")]:
        response = client.post("/api/auth/login", json={"identifier": identifier, "password": password})
        assert response.status_code == 401
        assert client.get("/api/session").status_code == 401
    response = client.post("/api/auth/login", json={"identifier": "owner@example.com", "password": "StrongPass123!"})
    assert response.status_code == 200
    assert response.json()["owner"]["email"] == "owner@example.com"
