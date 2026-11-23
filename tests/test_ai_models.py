"""AI Model Studio: security, bot routing, provider compatibility and tenant isolation."""
import sqlite3

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import app as server
from backend import ai_models


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "ai.sqlite3")
    monkeypatch.setenv("INVENTRACK_AI_KEY", Fernet.generate_key().decode())
    with TestClient(server.app) as test_client:
        yield test_client


def create_owner(client, label="alpha", mobile="9876543210"):
    response = client.post("/api/auth/register", json={
        "email": label + "@example.com", "mobile": mobile,
        "password": "ValidPass123!", "businessName": "Shop " + label,
        "businessType": "Supermart"
    })
    assert response.status_code == 201, response.text


def model_input(**overrides):
    default = {
        "name": "AI Fast", "provider": "openai",
        "protocol": "openai", "endpoint": "https://api.openai.com/v1",
        "modelId": "gpt-4.1-mini", "apiKey": "sk-test-sensitive-secret"
    }
    default.update(overrides)
    return default


def test_config_requires_session_and_keeps_key_encrypted(client):
    assert client.get("/api/ai/config").status_code == 401
    create_owner(client)
    response = client.post("/api/ai/models", json=model_input())
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["hasKey"] is True
    assert "apiKey" not in record and "encrypted_key" not in record
    assert "sk-test-sensitive-secret" not in response.text
    with sqlite3.connect(server.DB_PATH) as db:
        encrypted = db.execute("SELECT encrypted_key FROM ai_model_configs").fetchone()[0]
    assert "sk-test-sensitive-secret" not in encrypted
    assert ai_models.decrypt_key(encrypted) == "sk-test-sensitive-secret"
    listing = client.get("/api/ai/config")
    assert "sk-test-sensitive-secret" not in listing.text
    assert len(listing.json()["models"]) == 1
    assert len(listing.json()["bots"]) == 3


def test_update_keep_replace_and_clear_key(client):
    create_owner(client)
    model_id = client.post("/api/ai/models", json=model_input()).json()["id"]
    update = client.put("/api/ai/models/" + str(model_id),
                        json=model_input(name="Renamed", apiKey=""))
    assert update.status_code == 200
    with sqlite3.connect(server.DB_PATH) as db:
        encrypted = db.execute("SELECT encrypted_key FROM ai_model_configs").fetchone()[0]
    assert ai_models.decrypt_key(encrypted) == "sk-test-sensitive-secret"
    assert client.put("/api/ai/models/" + str(model_id),
                      json=model_input(apiKey="", clearKey=True)).json()["hasKey"] is False
    assert client.put("/api/ai/models/" + str(model_id),
                      json=model_input(apiKey="new-secret")).json()["hasKey"] is True


def test_assign_bot_routes_chat_and_delete_unassigns(client, monkeypatch):
    create_owner(client)
    saved = client.post("/api/ai/models", json=model_input()).json()
    selection = client.put("/api/ai/bots/sales",
                           json={"modelId": saved["id"], "extraInstruction": "Prefer tables"})
    assert selection.status_code == 200, selection.text
    seen = {}

    def mock_provider(model, key, instruction, message):
        seen.update(key=key, model=model["model_id"],
                    instruction=instruction, message=message)
        return "Verified mocked response"

    monkeypatch.setattr(ai_models, "provider_call", mock_provider)
    result = client.post("/api/ai/chat", json={"bot": "sales", "message": "Revenue today?"})
    assert result.status_code == 200, result.text
    assert result.json()["reply"] == "Verified mocked response"
    assert seen["key"] == "sk-test-sensitive-secret"
    assert seen["model"] == "gpt-4.1-mini"
    assert "Prefer tables" in seen["instruction"]
    assert "Shop alpha" in seen["instruction"]
    assert client.delete("/api/ai/models/" + str(saved["id"])).status_code == 204
    config = client.get("/api/ai/config").json()
    assert next(b for b in config["bots"] if b["slug"] == "sales")["modelId"] is None
    assert client.post("/api/ai/chat", json={"bot": "sales", "message": "hello"}).status_code == 503


def test_model_isolation_and_validation(client):
    create_owner(client)
    one = client.post("/api/ai/models", json=model_input()).json()["id"]
    client.post("/api/auth/logout")
    create_owner(client, "beta", "8888888888")
    assert client.get("/api/ai/config").json()["models"] == []
    assert client.put("/api/ai/models/" + str(one),
                      json=model_input()).status_code == 404
    assert client.delete("/api/ai/models/" + str(one)).status_code == 404
    assert client.post("/api/ai/models/" + str(one) + "/test").status_code == 404
    assert client.put("/api/ai/bots/staff", json={"modelId": one}).status_code == 404
    assert client.put("/api/ai/bots/undefined", json={"modelId": None}).status_code == 404
    assert client.post("/api/ai/chat", json={"bot": "undefined", "message": "hi"}).status_code == 404
    assert client.post("/api/ai/models", json=model_input(endpoint="http://127.0.0.1:8000")).status_code == 422
    assert client.post("/api/ai/models", json=model_input(endpoint="https://example.com/path?x=1")).status_code == 422


def test_provider_protocols_and_private_endpoint_blocking(monkeypatch):
    requests = []

    def mocked_http(url, payload, headers):
        requests.append((url, payload, headers))
        if ":generateContent" in url:
            return {"candidates": [{"content": {"parts": [{"text": "Gemini OK"}]}}]}
        if url.endswith("/messages"):
            return {"content": [{"type": "text", "text": "Anthropic OK"}]}
        return {"choices": [{"message": {"content": "OpenAI OK"}}]}

    monkeypatch.setattr(ai_models, "http_json", mocked_http)
    monkeypatch.setattr(ai_models, "public_destination", lambda url: None)
    for protocol, expected in (("openai", "OpenAI OK"),
                               ("gemini", "Gemini OK"),
                               ("anthropic", "Anthropic OK")):
        model = {"endpoint": "https://model.example.com/v1",
                 "protocol": protocol, "model_id": "test-model"}
        assert ai_models.provider_call(model, "key-value", "system instruction", "hi") == expected
    assert requests[0][2]["Authorization"] == "Bearer key-value"
    assert requests[1][2]["x-goog-api-key"] == "key-value"
    assert requests[2][2]["x-api-key"] == "key-value"
    monkeypatch.undo()
    with pytest.raises(HTTPException) as error:
        ai_models.public_destination("https://127.0.0.1/latest/metadata")
    assert error.value.status_code == 422

def test_9router_preset_end_to_end_with_mock_gateway(client, monkeypatch):
    """Verify the full form -> encrypted key -> test -> bot -> chat path without provider charges."""
    create_owner(client)
    config = client.get("/api/ai/config").json()
    preset = config["providers"]["9router"]
    assert preset["endpoint"] == "https://9router.com/v1"
    assert preset["protocol"] == "openai"
    assert preset["model"].startswith("if/")
    payload = model_input(
        provider="9router", protocol=preset["protocol"], endpoint=preset["endpoint"],
        modelId=preset["model"], name="9Router Test", apiKey="9r_FAKE_TEST_ONLY")
    created = client.post("/api/ai/models", json=payload)
    assert created.status_code == 201, created.text
    mid = created.json()["id"]
    assert "9r_FAKE_TEST_ONLY" not in created.text
    assert created.json()["hasKey"] is True
    request_capture = []

    def fake_http(url, body, headers):
        request_capture.append((url, body, headers))
        return {"choices": [{"message": {"content": "9Router mocked reply"}}]}

    monkeypatch.setattr(ai_models, "public_destination", lambda _: None)
    monkeypatch.setattr(ai_models, "http_json", fake_http)
    tested = client.post(f"/api/ai/models/{mid}/test")
    assert tested.status_code == 200, tested.text
    assert tested.json()["connected"] is True
    assert request_capture[0][0] == "https://9router.com/v1/chat/completions"
    assert request_capture[0][1]["model"] == preset["model"]
    assert request_capture[0][2]["Authorization"] == "Bearer 9r_FAKE_TEST_ONLY"
    assert client.put("/api/ai/bots/staff", json={"modelId": mid, "extraInstruction": ""}).status_code == 200
    answer = client.post("/api/ai/chat", json={"bot": "staff", "message": "Hello"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["reply"] == "9Router mocked reply"
    assert "9r_FAKE_TEST_ONLY" not in client.get("/api/ai/config").text

@pytest.mark.parametrize("endpoint, expected", [
    ("https://9router.com/v1", "https://9router.com/v1"),
    ("https://9router.com/v1/", "https://9router.com/v1"),
    ("https://9router.com/v1/chat/completions", "https://9router.com/v1"),
    ("https://9router.com/v1/chat/completions/", "https://9router.com/v1"),
    ("https://api.openai.com/v1", "https://api.openai.com/v1"),
])
def test_endpoint_normalizes_common_api_urls(endpoint, expected):
    assert ai_models.checked_endpoint(endpoint) == expected


@pytest.mark.parametrize("endpoint, expected", [
    ("http://9router.com/v1", "https://"),
    ("https://9router.com/v1?key=secret", "?query"),
    ("https://9router.com/v1#fragment", "?query"),
    ("https://user:pass@9router.com/v1", "credentials"),
    ("https://", "complete HTTPS"),
])
def test_endpoint_reports_actionable_error_without_echoing_secrets(endpoint, expected):
    with pytest.raises(HTTPException) as exc:
        ai_models.checked_endpoint(endpoint)
    assert exc.value.status_code == 422
    assert expected in str(exc.value.detail)
    assert "secret" not in str(exc.value.detail)
    assert "user:pass" not in str(exc.value.detail)
