"""Integration tests for owner passwordless sign-in, with a simulated delivery service."""
import pytest
from fastapi.testclient import TestClient
from backend import app as server
from backend import passwordless as otp


@pytest.mark.parametrize("channel,identifier", [("sms", "+91 98765-43210"), ("email", "owner@example.test")])
def test_verified_contact_login(tmp_path, monkeypatch, channel, identifier):
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "otp.sqlite3")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(otp, "provider_config", lambda: ("test-service", "test-key", "test-secret"))
    sent = []
    def send(kind, address):
        sent.append((kind, address))
        return "VE" + str(len(sent)).zfill(32)
    monkeypatch.setattr(otp, "send_code", send)
    monkeypatch.setattr(otp, "check_code", lambda sid, address, code: code == "654321")
    initial_password = "Test-only-owner-password"
    with TestClient(server.app) as client:
        response = client.post("/api/auth/register", json={"email": "owner@example.test", "mobile": "9876543210", "password": initial_password, "businessName": "Test Store"})
        assert response.status_code == 201
        owner_id = response.json()["owner"]["id"]
        client.post("/api/auth/logout")
        def start(**kwargs):
            response = client.post("/api/auth/otp/start", json={"identifier": identifier, **kwargs})
            assert response.status_code == 200
            return response.json()["challenge"]
        def confirm(challenge, code):
            return client.post("/api/auth/otp/verify", json={"challenge": challenge, "code": code})
        unverified = start()
        assert not sent
        assert confirm(unverified, "654321").status_code == 401
        challenge = start(setupPassword=initial_password)
        assert sent[-1][0] == channel
        assert confirm(challenge, "000000").status_code == 401
        assert confirm(challenge, "654321").json()["owner"]["id"] == owner_id
        assert client.get("/api/bootstrap").status_code == 200
        client.post("/api/auth/logout")
        assert confirm(challenge, "654321").status_code == 401
        assert confirm(start(), "654321").json()["owner"]["id"] == owner_id
