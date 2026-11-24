"""Contract checks for the optional Neon-managed auth UI."""
from fastapi.testclient import TestClient
from backend.app import app


def test_neon_signup_page_is_available():
    with TestClient(app) as client:
        response = client.get("/neon-signup")
    assert response.status_code == 200
    assert 'id="neon-auth-form"' in response.text
    assert 'id="check-session"' in response.text
    assert "/neon_auth_bundle.js" in response.text


def test_neon_browser_bundle_is_served():
    with TestClient(app) as client:
        response = client.get("/neon_auth_bundle.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert len(response.content) > 10000


def test_existing_login_links_to_neon_signup():
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert 'href="/neon-signup"' in response.text
