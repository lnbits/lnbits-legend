import asyncio
import hashlib
from datetime import datetime
from os import urandom
from typing import Dict, Optional, Set
from fastapi import WebSocket, WebSocketDisconnect
from bolt11 import Bolt11, Tags, TagChar, MilliSatoshi, encode
from loguru import logger


# LNbitsPhoenix class to handle invoice creation and payment processing
class LNbitsPhoenix:
    queue: asyncio.Queue = asyncio.Queue(0)
    payment_secrets: Dict[str, str] = {}
    paid_invoices: Set[str] = set()
    secret: str = "super_secret_key"
    privkey: str = hashlib.pbkdf2_hmac(
        "sha256",
        secret.encode(),
        b"LNbitsPhoenix",
        2048,
        32,
    ).hex()

    async def create_invoice(
        self,
        amount: int,
        memo: Optional[str] = None,
        expiry: Optional[int] = None,
    ) -> Dict:
        tags = Tags()
        tags.add(TagChar.description, memo or "")

        if expiry:
            tags.add(TagChar.expire_time, expiry)

        secret = urandom(32).hex()
        tags.add(TagChar.payment_secret, secret)
        payment_hash = hashlib.sha256(secret.encode()).hexdigest()
        tags.add(TagChar.payment_hash, payment_hash)

        self.payment_secrets[payment_hash] = secret

        bolt11 = Bolt11(
            currency="bc",
            amount_msat=MilliSatoshi(amount * 1000),
            date=int(datetime.now().timestamp()),
            tags=tags,
        )

        payment_request = encode(bolt11, self.privkey)

        return {
            "ok": True,
            "checking_id": payment_hash,
            "payment_request": payment_request,
        }

    async def get_invoice_status(self, checking_id: str):
        if checking_id in self.paid_invoices:
            return {"status": "paid"}
        elif checking_id in self.payment_secrets:
            return {"status": "pending"}
        else:
            return {"status": "failed"}

    async def mark_invoice_as_paid(self, checking_id: str):
        if checking_id in self.payment_secrets:
            self.paid_invoices.add(checking_id)
            await self.queue.put(checking_id)
            return {"status": "success"}
        return {"status": "failed"}

    async def paid_invoices_stream(self) -> asyncio.Queue:
        return self.queue


# Initialize an instance of LNbitsPhoenix
lnbits_phoenix = LNbitsPhoenix()


# WebSocket handler for invoice creation
async def handle_invoice_creation(websocket: WebSocket):
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_text()  # Receive the amount from the WebSocket client
            try:
                amount = int(data)  # Parse the amount
                invoice = await lnbits_phoenix.create_invoice(amount)
                await websocket.send_json(invoice)  # Send the invoice back to the client
            except ValueError:
                await websocket.send_text("Invalid amount. Please send a number.")
    except WebSocketDisconnect:
        logger.info("Client disconnected from invoice creation WebSocket.")


# WebSocket handler for streaming paid invoices
async def handle_paid_invoices_stream(websocket: WebSocket):
    await websocket.accept()

    try:
        while True:
            # Get a payment hash from the queue when an invoice is paid
            payment_hash = await lnbits_phoenix.queue.get()
            await websocket.send_text(f"Invoice {payment_hash} has been paid")
    except WebSocketDisconnect:
        logger.info("Client disconnected from paid invoice stream WebSocket.")

# WebSocket route for creating invoices
@app.websocket("/lnbix/ws/create_invoice")
async def websocket_create_invoice(websocket: WebSocket):
    await handle_invoice_creation(websocket)

# WebSocket route for streaming paid invoices
@app.websocket("/lnbix/ws/paid_invoices")
async def websocket_paid_invoices(websocket: WebSocket):
    await handle_paid_invoices_stream(websocket)