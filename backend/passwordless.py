"""Passwordless owner sign-in using provider-managed, one-use verification codes."""
import base64
import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from backend import app as server

router = APIRouter(prefix="/api/auth/otp")


def init_schema(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS verified_owner_contacts (
            owner_id INTEGER NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
            channel TEXT NOT NULL,
            destination TEXT NOT NULL,
            verified_at TEXT NOT NULL,
            PRIMARY KEY(owner_id, channel),
            UNIQUE(channel, destination)
        );
        CREATE TABLE IF NOT EXISTS owner_otp_challenges (
            id TEXT PRIMARY KEY,
            owner_id INTEGER NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
            channel TEXT NOT NULL,
            destination TEXT NOT NULL,
            verification_sid TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            consumed INTEGER NOT NULL DEFAULT 0
        );
    """)


def provider_config():
    service = os.getenv("TWILIO_VERIFY_SERVICE_SID", "")
    key = os.getenv("TWILIO_VERIFY_API_KEY", "")
    secret = os.getenv("TWILIO_VERIFY_API_SECRET", "")
    if not re.fullmatch(r"VA[0-9a-fA-F]{32}", service) or not re.fullmatch(r"SK[0-9a-fA-F]{32}", key) or not secret:
        raise HTTPException(503, "Verification delivery is not configured. Contact the administrator.")
    return service, key, secret


def provider_request(resource, fields):
    service, key, secret = provider_config()
    credentials = base64.b64encode((key + ":" + secret).encode()).decode()
    req = urllib.request.Request(
        "https://verify.twilio.com/v2/Services/" + service + "/" + resource,
        data=urllib.parse.urlencode(fields).encode(),
        headers={"Authorization": "Basic " + credentials, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read(65536))
    except urllib.error.HTTPError as exc:
        if resource == "VerificationCheck" and exc.code in (400, 404, 429):
            return {}
        raise HTTPException(503, "Verification delivery is temporarily unavailable.") from None
    except (OSError, ValueError):
        raise HTTPException(503, "Verification service is temporarily unavailable.") from None


def send_code(channel, destination):
    data = provider_request("Verifications", {"To": destination, "Channel": channel})
    sid = data.get("sid", "")
    if data.get("status") != "pending" or not re.fullmatch(r"VE[0-9a-fA-F]{32}", sid):
        raise HTTPException(503, "Verification could not be started.")
    return sid


def check_code(sid, destination, code):
    data = provider_request("VerificationCheck", {"VerificationSid": sid, "Code": code})
    return data.get("status") == "approved" and data.get("to") == destination


def normalize(identifier):
    identifier = identifier.strip()
    if "@" in identifier:
        identifier = identifier.lower()
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", identifier):
            return "email", identifier
    else:
        mobile = server.normalize_login_mobile(identifier)
        if mobile:
            return "sms", "+91" + mobile
    raise HTTPException(400, "Enter a valid email or Indian mobile number.")


def get_owner(con, channel, destination):
    if channel == "email":
        rows = con.execute("SELECT * FROM owners WHERE email=? AND is_demo=0", (destination,)).fetchall()
    else:
        rows = con.execute("SELECT * FROM owners WHERE mobile=? AND is_demo=0 LIMIT 2", (destination[3:],)).fetchall()
    return rows[0] if len(rows) == 1 else None


class Start(BaseModel):
    identifier: str = Field(min_length=1, max_length=254)
    setupPassword: str | None = Field(default=None, max_length=128)


class Confirm(BaseModel):
    challenge: str = Field(min_length=20, max_length=100)
    code: str = Field(pattern=r"^[0-9]{4,10}$")


def guard():
    if os.getenv("INVENTRACK_DEMO_ONLY") == "1":
        raise HTTPException(403, "Public demo mode: use Demo Login.")


@router.post("/start")
def start(payload: Start, request: Request):
    guard()
    channel, destination = normalize(payload.identifier)
    provider_config()
    server.enforce_limit(request, "otp-send-ip", 12, 3600)
    server.enforce_limit(request, "otp-send-destination", 4, 3600, destination, global_identity=True)
    challenge = secrets.token_urlsafe(32)
    generic = {"challenge": challenge, "message": "If this contact is eligible, a verification code has been sent. For first-time setup, use your existing account password."}
    with server.database() as con:
        owner = get_owner(con, channel, destination)
        if not owner:
            return generic
        verified = con.execute("SELECT owner_id FROM verified_owner_contacts WHERE owner_id=? AND channel=? AND destination=?",
                               (owner["id"], channel, destination)).fetchone()
        if not verified and not (payload.setupPassword and server.verify_password(payload.setupPassword, owner["password_hash"])):
            return generic
        owner_id = owner["id"]
    sid = send_code(channel, destination)
    with server.database() as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute("DELETE FROM owner_otp_challenges WHERE expires_at<=? OR (owner_id=? AND channel=?)",
                    (server.dt_iso(), owner_id, channel))
        con.execute("INSERT INTO owner_otp_challenges(id,owner_id,channel,destination,verification_sid,expires_at) VALUES(?,?,?,?,?,?)",
                    (challenge, owner_id, channel, destination, sid, server.dt_iso(server.utcnow() + timedelta(minutes=5))))
    return generic


@router.post("/verify")
def verify(payload: Confirm, request: Request, response: Response):
    guard()
    server.enforce_limit(request, "otp-check-ip", 30, 600)
    invalid = HTTPException(401, "Invalid or expired verification code. Request a new code or complete first-time setup.")
    with server.database() as con:
        con.execute("BEGIN IMMEDIATE")
        record = con.execute("SELECT * FROM owner_otp_challenges WHERE id=?", (payload.challenge,)).fetchone()
        if not record or record["consumed"] or record["attempts"] >= 5 or record["expires_at"] <= server.dt_iso():
            raise invalid
        con.execute("UPDATE owner_otp_challenges SET attempts=attempts+1 WHERE id=?", (payload.challenge,))
    if not check_code(record["verification_sid"], record["destination"], payload.code):
        raise invalid
    with server.database() as con:
        con.execute("BEGIN IMMEDIATE")
        current = con.execute("SELECT * FROM owner_otp_challenges WHERE id=? AND consumed=0 AND expires_at>?", (payload.challenge, server.dt_iso())).fetchone()
        if not current:
            raise invalid
        owner = get_owner(con, current["channel"], current["destination"])
        if not owner or owner["id"] != current["owner_id"]:
            raise invalid
        existing = con.execute("SELECT owner_id FROM verified_owner_contacts WHERE channel=? AND destination=?", (current["channel"],current["destination"])).fetchone()
        if existing and existing["owner_id"] != owner["id"]:
            raise invalid
        con.execute("INSERT INTO verified_owner_contacts(owner_id,channel,destination,verified_at) VALUES(?,?,?,?) ON CONFLICT(owner_id,channel) DO UPDATE SET destination=excluded.destination, verified_at=excluded.verified_at",
                    (owner["id"], current["channel"], current["destination"], server.dt_iso()))
        con.execute("UPDATE owner_otp_challenges SET consumed=1 WHERE id=?", (payload.challenge,))
        server.set_session(con, owner["id"], response, request)
        return {"owner": server.owner_json(owner)}
