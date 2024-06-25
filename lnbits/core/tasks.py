import asyncio
from typing import Dict

import httpx
from fastapi.routing import APIRouter
from loguru import logger

from lnbits.core.crud import (
    get_wallet,
    get_webpush_subscriptions_for_user,
    mark_webhook_sent,
)
from lnbits.core.models import Payment, Wallet
from lnbits.core.services import (
    get_balance_delta,
    send_payment_notification,
    switch_to_voidwallet,
)
from lnbits.settings import get_funding_source, settings
from lnbits.tasks import create_unique_task, send_push_notification
from lnbits.utils.gateway import WebSocketReverseWallet

api_invoice_listeners: Dict[str, asyncio.Queue] = {}
reverse_funding_wallets_ids: asyncio.Queue[Wallet] = asyncio.Queue()
reverse_funding_wallets: Dict[str, WebSocketReverseWallet] = {}


async def killswitch_task():
    """
    killswitch will check lnbits-status repository for a signal from
    LNbits and will switch to VoidWallet if the killswitch is triggered.
    """
    while settings.lnbits_running:
        funding_source = get_funding_source()
        if (
            settings.lnbits_killswitch
            and funding_source.__class__.__name__ != "VoidWallet"
        ):
            with httpx.Client() as client:
                try:
                    r = client.get(settings.lnbits_status_manifest, timeout=4)
                    r.raise_for_status()
                    if r.status_code == 200:
                        ks = r.json().get("killswitch")
                        if ks and ks == 1:
                            logger.error(
                                "Switching to VoidWallet. Killswitch triggered."
                            )
                            await switch_to_voidwallet()
                except (httpx.RequestError, httpx.HTTPStatusError):
                    logger.error(
                        "Cannot fetch lnbits status manifest."
                        f" {settings.lnbits_status_manifest}"
                    )
        await asyncio.sleep(settings.lnbits_killswitch_interval * 60)


async def watchdog_task():
    """
    Registers a watchdog which will check lnbits balance and nodebalance
    and will switch to VoidWallet if the watchdog delta is reached.
    """
    while settings.lnbits_running:
        funding_source = get_funding_source()
        if (
            settings.lnbits_watchdog
            and funding_source.__class__.__name__ != "VoidWallet"
        ):
            try:
                balance = await get_balance_delta()
                delta = balance.delta_msats
                logger.debug(f"Running watchdog task. current delta: {delta}")
                if delta + settings.lnbits_watchdog_delta <= 0:
                    logger.error(f"Switching to VoidWallet. current delta: {delta}")
                    await switch_to_voidwallet()
            except Exception as e:
                logger.error("Error in watchdog task", e)
        await asyncio.sleep(settings.lnbits_watchdog_interval * 60)


async def wait_for_paid_invoices(invoice_paid_queue: asyncio.Queue):
    """
    This worker dispatches events to all extensions and dispatches webhooks.
    """
    while settings.lnbits_running:
        payment = await invoice_paid_queue.get()
        logger.trace("received invoice paid event")
        # dispatch api_invoice_listeners
        await dispatch_api_invoice_listeners(payment)
        # payment notification
        wallet = await get_wallet(payment.wallet_id)
        if wallet:
            await send_payment_notification(wallet, payment)
        # dispatch webhook
        if payment.webhook and not payment.webhook_status:
            await dispatch_webhook(payment)
        # dispatch push notification
        await send_payment_push_notification(payment)


async def dispatch_api_invoice_listeners(payment: Payment):
    """
    Emits events to invoice listener subscribed from the API.
    """
    for chan_name, send_channel in api_invoice_listeners.items():
        try:
            logger.debug(f"api invoice listener: sending paid event to {chan_name}")
            send_channel.put_nowait(payment)
        except asyncio.QueueFull:
            logger.error(
                f"api invoice listener: QueueFull, removing {send_channel}:{chan_name}"
            )
            api_invoice_listeners.pop(chan_name)


async def dispatch_webhook(payment: Payment):
    """
    Dispatches the webhook to the webhook url.
    """
    logger.debug("sending webhook", payment.webhook)

    if not payment.webhook:
        return await mark_webhook_sent(payment.payment_hash, -1)

    headers = {"User-Agent": settings.user_agent}
    async with httpx.AsyncClient(headers=headers) as client:
        data = payment.dict()
        try:
            r = await client.post(payment.webhook, json=data, timeout=40)
            r.raise_for_status()
            await mark_webhook_sent(payment.payment_hash, r.status_code)
        except httpx.HTTPStatusError as exc:
            await mark_webhook_sent(payment.payment_hash, exc.response.status_code)
            logger.warning(
                f"webhook returned a bad status_code: {exc.response.status_code} "
                f"while requesting {exc.request.url!r}."
            )
        except httpx.RequestError:
            await mark_webhook_sent(payment.payment_hash, -1)
            logger.warning(f"Could not send webhook to {payment.webhook}")


async def send_payment_push_notification(payment: Payment):
    wallet = await get_wallet(payment.wallet_id)

    if wallet:
        subscriptions = await get_webpush_subscriptions_for_user(wallet.user)

        amount = int(payment.amount / 1000)

        title = f"LNbits: {wallet.name}"
        body = f"You just received {amount} sat{'s'[:amount^1]}!"

        if payment.memo:
            body += f"\r\n{payment.memo}"

        for subscription in subscriptions:
            # todo: review permissions when user-id-only not allowed
            # todo: replace all this logic with websockets?
            url = (
                f"https://{subscription.host}/wallet?usr={wallet.user}&wal={wallet.id}"
            )
            await send_push_notification(subscription, title, body, url)


def feed_reverse_funding_source(wallet: Wallet, routers: APIRouter):
    print("### y")

    async def _coro():
        print("### z")
        api_key = getattr(wallet, wallet.config.reverse_funding_access, "")

        reverse_wallet = WebSocketReverseWallet(
            wallet.id,
            api_key,
            wallet.config.reverse_funding_ws_url(),
            routers,
        )
        await reverse_wallet()

    return _coro


async def register_reverse_funding_sources(routers: APIRouter):
    while settings.lnbits_running:
        try:
            wallet = await reverse_funding_wallets_ids.get()

            active_reverse_wallet = reverse_funding_wallets.get(wallet.id)
            if wallet.reverse_funding_enabled and active_reverse_wallet:
                # check if different url
                # update apy key, make public
                active_reverse_wallet._api_key = getattr(
                    wallet, wallet.config.reverse_funding_access, ""
                )

            if not wallet.reverse_funding_enabled and active_reverse_wallet:
                active_reverse_wallet.disconnect()
                reverse_funding_wallets.__delitem__(wallet.id)
                continue

            coro = feed_reverse_funding_source(wallet, routers)
            create_unique_task(
                f"reverse_funding_wallet_{wallet.id}",
                coro(),
            )
            print("### out")

        except Exception as exc:
            logger.warning(exc)
            await asyncio.sleep(60)


# get all active reverse wallets
