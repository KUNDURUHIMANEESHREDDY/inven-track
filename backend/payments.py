"""Optional Razorpay checkout: test credentials by default, verified capture, signed webhooks."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.app import current_owner, database, dt_iso, enforce_limit, invoice_json

router = APIRouter(prefix="/api/payments", tags=["Payments"])
PAYMENT_API = "https://api.razorpay.com/v1"


def gateway_enabled() -> bool:
    if os.getenv("INVENTRACK_DEMO_ONLY") == "1":
        return False
    key = os.getenv("RAZORPAY_KEY_ID", "")
    secret = os.getenv("RAZORPAY_KEY_SECRET", "")
    if not key or not secret:
        return False
    if os.getenv("RAZORPAY_MODE", "test") == "test":
        return key.startswith("rzp_test_")
    return (os.getenv("RAZORPAY_MODE") == "live"
            and os.getenv("ENABLE_LIVE_PAYMENTS") == "YES"
            and key.startswith("rzp_live_"))


def require_gateway():
    if not gateway_enabled():
        raise HTTPException(503, "Razorpay is not enabled (test credentials required)")


def provider_call(method: str, path: str, payload: dict | None = None) -> dict:
    """Only call fixed API host with server-owned credentials, never a client URL."""
    require_gateway()
    auth = base64.b64encode(
        (os.environ["RAZORPAY_KEY_ID"] + ":" + os.environ["RAZORPAY_KEY_SECRET"]).encode()
    ).decode()
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(PAYMENT_API + path, data=data, method=method, headers={
        "Authorization": "Basic " + auth, "Content-Type": "application/json",
        "Accept": "application/json"
    })
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            result = json.loads(r.read(262144))
        return result
    except (urllib.error.URLError, ValueError, TimeoutError):
        raise HTTPException(502, "Payment provider did not confirm the request")


def invoice_row(con, owner_id: int, invoice_number: int):
    row = con.execute("SELECT * FROM invoices WHERE owner_id=? AND invoice_number=?",
                      (owner_id, invoice_number)).fetchone()
    if not row:
        raise HTTPException(404, "Invoice not found")
    return row


class VerifyPayment(BaseModel):
    invoiceNumber: int = Field(ge=1)
    razorpay_order_id: str = Field(pattern=r"^order_[A-Za-z0-9]+$", max_length=100)
    razorpay_payment_id: str = Field(pattern=r"^pay_[A-Za-z0-9]+$", max_length=100)
    razorpay_signature: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


def signed(signature: str, message: bytes, secret: str) -> bool:
    generated = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(generated, signature)


@router.post("/orders/{invoice_number}")
def create_payment_order(invoice_number: int, request: Request, owner=Depends(current_owner)):
    require_gateway()
    enforce_limit(request, "payment-create", 30, 3600)
    with database() as con:
        con.execute("BEGIN IMMEDIATE")
        invoice = invoice_row(con, owner["id"], invoice_number)
        if invoice["payment"] != "Online (Razorpay)":
            raise HTTPException(409, "Only online invoices can be paid through the gateway")
        if invoice["payment_status"] == "paid":
            raise HTTPException(409, "Invoice is already paid")
        if invoice["payment_status"] != "pending":
            raise HTTPException(409, "Invoice is no longer payable")
        attempt = con.execute("SELECT * FROM payment_attempts WHERE invoice_id=?",
                              (invoice["id"],)).fetchone()
        if attempt and attempt["status"] in ("ready", "captured") and attempt["gateway_order_id"]:
            return {"keyId": os.environ["RAZORPAY_KEY_ID"],
                    "orderId": attempt["gateway_order_id"],
                    "amount": attempt["amount_paise"], "currency": "INR",
                    "invoiceNumber": invoice_number, "businessName": owner["business_name"]}
        if attempt and attempt["status"] == "creating":
            # Prevent duplicate orders while an external request is in flight.
            age = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(attempt["updated_at"])).total_seconds()
            if age < 60:
                raise HTTPException(409, "Payment initialization is in progress; retry shortly")
        con.execute("""INSERT INTO payment_attempts(invoice_id,amount_paise,status,updated_at)
                       VALUES(?,?,?,?)
                       ON CONFLICT(invoice_id) DO UPDATE SET
                         amount_paise=excluded.amount_paise,
                         gateway_order_id=NULL,gateway_payment_id=NULL,
                         status='creating',updated_at=excluded.updated_at
                    """, (invoice["id"], invoice["total_paise"], "creating", dt_iso()))
        invoice_id, amount = invoice["id"], invoice["total_paise"]
    try:
        result = provider_call("POST", "/orders",
                               {"amount": amount, "currency": "INR",
                                "receipt": "vf-" + str(invoice_id),
                                "notes": {"invoice": "INV-" + str(1000 + invoice_number)}})
        if (not isinstance(result.get("id"), str) or
                result.get("amount") != amount or result.get("currency") != "INR"):
            raise HTTPException(502, "Invalid payment provider order response")
    except Exception:
        with database() as con:
            con.execute("UPDATE payment_attempts SET status='failed', updated_at=? "
                        "WHERE invoice_id=? AND status='creating'", (dt_iso(), invoice_id))
        raise
    with database() as con:
        con.execute("BEGIN IMMEDIATE")
        changed = con.execute(
            """UPDATE payment_attempts SET gateway_order_id=?,status='ready',updated_at=?
               WHERE invoice_id=? AND status='creating'""",
            (result["id"], dt_iso(), invoice_id))
        if changed.rowcount != 1:
            raise HTTPException(409, "Payment state changed; contact support")
    return {"keyId": os.environ["RAZORPAY_KEY_ID"], "orderId": result["id"],
            "amount": amount, "currency": "INR", "invoiceNumber": invoice_number,
            "businessName": owner["business_name"]}


@router.post("/verify")
def verify_payment(payload: VerifyPayment, request: Request, owner=Depends(current_owner)):
    require_gateway()
    enforce_limit(request, "payment-verify", 50, 3600)
    with database() as con:
        invoice = invoice_row(con, owner["id"], payload.invoiceNumber)
        attempt = con.execute("SELECT * FROM payment_attempts WHERE invoice_id=?",
                              (invoice["id"],)).fetchone()
        if not attempt or attempt["gateway_order_id"] != payload.razorpay_order_id:
            raise HTTPException(409, "Unknown order for this invoice")
        if not signed(payload.razorpay_signature,
                      (attempt["gateway_order_id"] + "|" + payload.razorpay_payment_id).encode(),
                      os.environ["RAZORPAY_KEY_SECRET"]):
            raise HTTPException(400, "Invalid payment signature")
        if invoice["payment_status"] == "paid" and attempt["gateway_payment_id"] == payload.razorpay_payment_id:
            return {"invoice": invoice_json(con, invoice), "verified": True}
        if invoice["payment_status"] != "pending":
            raise HTTPException(409, "Invoice is not pending")
        invoice_id, amount = invoice["id"], invoice["total_paise"]
    # A valid browser callback signature alone is not evidence of captured funds.
    verified = provider_call("GET", "/payments/" + payload.razorpay_payment_id)
    if (verified.get("id") != payload.razorpay_payment_id
            or verified.get("order_id") != payload.razorpay_order_id
            or verified.get("amount") != amount
            or verified.get("currency") != "INR"
            or verified.get("status") != "captured"):
        raise HTTPException(409, "Payment is not captured; invoice remains pending")
    with database() as con:
        con.execute("BEGIN IMMEDIATE")
        invoice = invoice_row(con, owner["id"], payload.invoiceNumber)
        if invoice["payment_status"] not in ("pending", "paid"):
            raise HTTPException(409, "Invoice is no longer payable")
        existing = con.execute("SELECT * FROM payment_attempts WHERE invoice_id=?",
                               (invoice_id,)).fetchone()
        if existing["gateway_order_id"] != payload.razorpay_order_id:
            raise HTTPException(409, "Payment order changed")
        if invoice["payment_status"] == "paid" and existing["gateway_payment_id"] != payload.razorpay_payment_id:
            raise HTTPException(409, "Invoice was already paid differently")
        con.execute("""UPDATE payment_attempts
                       SET status='captured',gateway_payment_id=?,updated_at=?
                       WHERE invoice_id=?""", (payload.razorpay_payment_id, dt_iso(), invoice_id))
        con.execute("UPDATE invoices SET payment_status='paid' WHERE id=?",
                    (invoice_id,))
        invoice = invoice_row(con, owner["id"], payload.invoiceNumber)
        return {"invoice": invoice_json(con, invoice), "verified": True}


@router.get("/status/{invoice_number}")
def payment_status(invoice_number: int, owner=Depends(current_owner)):
    with database() as con:
        invoice = invoice_row(con, owner["id"], invoice_number)
        return {"invoice": invoice_json(con, invoice)}


@router.post("/webhook")
async def payment_webhook(request: Request):
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(503, "Webhook secret is not configured")
    body = await request.body()
    if len(body) > 262144:
        raise HTTPException(413, "Webhook payload too large")
    signature = request.headers.get("x-razorpay-signature", "")
    if not signed(signature, body, secret):
        raise HTTPException(401, "Invalid webhook signature")
    event_id = request.headers.get("x-razorpay-event-id", "")
    if not event_id or len(event_id) > 150:
        raise HTTPException(400, "Missing event identifier")
    try:
        event = json.loads(body)
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Invalid webhook JSON")
    with database() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute("INSERT INTO payment_webhook_events(event_id,received_at) VALUES(?,?)",
                        (event_id, dt_iso()))
        except sqlite3.IntegrityError:
            return {"ok": True, "duplicate": True}
        if event.get("event") != "payment.captured":
            return {"ok": True}
        info = event.get("payload", {}).get("payment", {}).get("entity", {})
        if not isinstance(info, dict):
            return {"ok": True}
        order_id = info.get("order_id")
        payment_id = info.get("id")
        if not isinstance(order_id, str) or not isinstance(payment_id, str):
            return {"ok": True}
        attempt = con.execute("""
            SELECT a.*,i.total_paise,i.payment_status,i.payment
            FROM payment_attempts a JOIN invoices i ON i.id=a.invoice_id
            WHERE a.gateway_order_id=?
        """, (order_id,)).fetchone()
        if not attempt or attempt["payment"] != "Online (Razorpay)":
            # Unknown order: acknowledge safely and let gateway reconciliation handle it.
            return {"ok": True, "unmatched": True}
        if (info.get("amount") != attempt["amount_paise"]
                or info.get("currency") != "INR"
                or info.get("status") != "captured"):
            return {"ok": True, "mismatch": True}
        if attempt["payment_status"] == "paid":
            return {"ok": True, "duplicate": True}
        if attempt["payment_status"] != "pending":
            return {"ok": True, "mismatch": True}
        con.execute("""UPDATE payment_attempts SET status='captured',
                       gateway_payment_id=?,updated_at=? WHERE invoice_id=?""",
                    (payment_id, dt_iso(), attempt["invoice_id"]))
        con.execute("UPDATE invoices SET payment_status='paid' WHERE id=?",
                    (attempt["invoice_id"],))
    return {"ok": True, "captured": True}


@router.post("/void/{invoice_number}")
def void_unpaid_invoice(invoice_number: int, request: Request, owner=Depends(current_owner)):
    enforce_limit(request, "payment-void", 25, 3600)
    with database() as con:
        con.execute("BEGIN IMMEDIATE")
        row = invoice_row(con, owner["id"], invoice_number)
        if row["payment"] != "Online (Razorpay)" or row["payment_status"] != "pending":
            raise HTTPException(409, "Only unpaid online invoices can be voided")
        attempt = con.execute("SELECT * FROM payment_attempts WHERE invoice_id=?",
                              (row["id"],)).fetchone()
        # Once a provider order exists, money could have been paid while the callback
        # is delayed. Never release stock until provider reconciliation is definitive.
        if attempt and attempt["status"] not in ("failed",):
            raise HTTPException(409, "Payment order exists; reconcile it before voiding")
        items = con.execute("SELECT product_id,qty FROM invoice_items WHERE invoice_id=?",
                            (row["id"],)).fetchall()
        for item in items:
            if item["product_id"] is not None:
                con.execute("""UPDATE products SET stock=stock+?,
                               sold_count=MAX(0,sold_count-?) WHERE id=? AND owner_id=?""",
                            (item["qty"], item["qty"], item["product_id"], owner["id"]))
        con.execute("UPDATE invoices SET payment_status='void' WHERE id=?", (row["id"],))
        return {"ok": True, "invoice": invoice_json(con, invoice_row(con, owner["id"], invoice_number))}


@router.post("/reconcile/{invoice_number}")
def reconcile_payment(invoice_number: int, request: Request, owner=Depends(current_owner)):
    """Find captured payments directly at provider even if client/webhook was lost."""
    require_gateway()
    enforce_limit(request, "payment-reconcile", 24, 3600)
    with database() as con:
        invoice = invoice_row(con, owner["id"], invoice_number)
        if invoice["payment"] != "Online (Razorpay)":
            raise HTTPException(409, "This is not an online payment invoice")
        if invoice["payment_status"] == "paid":
            return {"invoice": invoice_json(con, invoice), "verified": True}
        if invoice["payment_status"] != "pending":
            raise HTTPException(409, "Invoice is not pending")
        attempt = con.execute("SELECT * FROM payment_attempts WHERE invoice_id=?",
                              (invoice["id"],)).fetchone()
        if not attempt or attempt["status"] != "ready" or not attempt["gateway_order_id"]:
            return {"invoice": invoice_json(con, invoice), "verified": False}
        order_id, amount = attempt["gateway_order_id"], attempt["amount_paise"]
    if not re.fullmatch(r"order_[A-Za-z0-9]+", order_id):
        raise HTTPException(502, "Invalid stored payment order identifier")
    response = provider_call("GET", "/orders/" + order_id + "/payments")
    payments = response.get("items", [])
    if not isinstance(payments, list):
        raise HTTPException(502, "Unexpected payment provider response")
    valid = next((payment for payment in payments
                  if isinstance(payment, dict)
                  and payment.get("status") == "captured"
                  and payment.get("amount") == amount
                  and payment.get("currency") == "INR"
                  and payment.get("order_id") == order_id
                  and isinstance(payment.get("id"), str)
                  and re.fullmatch(r"pay_[A-Za-z0-9]+", payment["id"])), None)
    if not valid:
        with database() as con:
            return {"invoice": invoice_json(con, invoice_row(con, owner["id"], invoice_number)),
                    "verified": False}
    with database() as con:
        con.execute("BEGIN IMMEDIATE")
        current = invoice_row(con, owner["id"], invoice_number)
        if current["payment_status"] == "pending":
            con.execute("""UPDATE payment_attempts SET status='captured',
                           gateway_payment_id=?,updated_at=? WHERE invoice_id=?""",
                        (valid["id"], dt_iso(), current["id"]))
            con.execute("UPDATE invoices SET payment_status='paid' WHERE id=?", (current["id"],))
        return {"invoice": invoice_json(con, invoice_row(con, owner["id"], invoice_number)),
                "verified": True}
