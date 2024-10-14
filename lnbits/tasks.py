import asyncio
import json
import time
import traceback
import uuid
from http import HTTPStatus
from typing import (
    Callable,
    Coroutine,
    Dict,
    List,
    Optional,
)

from loguru import logger
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from lnbits.core.crud import (
    delete_webpush_subscriptions,
    get_payments,
    get_standalone_payment,
    update_payment,
)
from lnbits.core.models import Payment, PaymentState
from lnbits.settings import settings
from lnbits.wallets import get_funding_source

tasks: List[asyncio.Task] = []
unique_tasks: Dict[str, asyncio.Task] = {}


def create_task(coro: Coroutine) -> asyncio.Task:
    task = asyncio.create_task(coro)
    tasks.append(task)
    return task


def create_unique_task(name: str, coro: Coroutine) -> asyncio.Task:
    if unique_tasks.get(name):
        logger.warning(f"task `{name}` already exists, cancelling it")
        try:
            unique_tasks[name].cancel()
        except Exception as exc:
            logger.warning(f"error while cancelling task `{name}`: {exc!s}")
    task = asyncio.create_task(coro)
    unique_tasks[name] = task
    return task


def create_permanent_task(func: Callable[[], Coroutine]) -> asyncio.Task:
    return create_task(catch_everything_and_restart(func))


def create_permanent_unique_task(
    name: str, coro: Callable[[], Coroutine]
) -> asyncio.Task:
    return create_unique_task(name, catch_everything_and_restart(coro, name))


def cancel_all_tasks() -> None:
    for task in tasks:
        try:
            task.cancel()
        except Exception as exc:
            logger.warning(f"error while cancelling task: {exc!s}")
    for name, task in unique_tasks.items():
        try:
            task.cancel()
        except Exception as exc:
            logger.warning(f"error while cancelling task `{name}`: {exc!s}")


async def catch_everything_and_restart(
    func: Callable[[], Coroutine],
    name: str = "unnamed",
) -> Coroutine:
    try:
        return await func()
    except asyncio.CancelledError:
        raise  # because we must pass this up
    except Exception as exc:
        logger.error(f"exception in background task `{name}`:", exc)
        logger.error(traceback.format_exc())
        logger.error("will restart the task in 5 seconds.")
        await asyncio.sleep(5)
        return await catch_everything_and_restart(func, name)


invoice_listeners: Dict[str, asyncio.Queue] = {}


# TODO: name should not be optional
# some extensions still dont use a name, but they should
def register_invoice_listener(send_chan: asyncio.Queue, name: Optional[str] = None):
    """
    A method intended for extensions (and core/tasks.py) to call when they want to be
    notified about new invoice payments incoming. Will emit all incoming payments.
    """
    if not name:
        # fallback to a random name if extension didn't provide one
        name = f"no_name_{str(uuid.uuid4())[:8]}"

    if invoice_listeners.get(name):
        logger.warning(f"invoice listener `{name}` already exists, replacing it")

    logger.trace(f"registering invoice listener `{name}`")
    invoice_listeners[name] = send_chan


internal_invoice_queue: asyncio.Queue = asyncio.Queue(0)


async def internal_invoice_listener() -> None:
    """
    internal_invoice_queue will be filled directly in core/services.py
    after the payment was deemed to be settled internally.

    Called by the app startup sequence.
    """
    while settings.lnbits_running:
        checking_id = await internal_invoice_queue.get()
        logger.info(f"got an internal payment notification {checking_id}")
        await invoice_callback_dispatcher(checking_id, is_internal=True)


async def invoice_listener() -> None:
    """
    invoice_listener will collect all invoices that come directly
    from the backend wallet.

    Called by the app startup sequence.
    """
    funding_source = get_funding_source()
    async for checking_id in funding_source.paid_invoices_stream():
        logger.info(f"got a payment notification {checking_id}")
        await invoice_callback_dispatcher(checking_id)


def wait_for_paid_invoices(
    invoice_listener_name: str,
    func: Callable[[Payment], Coroutine],
) -> Callable[[], Coroutine]:

    async def wrapper() -> None:
        invoice_queue: asyncio.Queue = asyncio.Queue()
        register_invoice_listener(invoice_queue, invoice_listener_name)
        while settings.lnbits_running:
            payment = await invoice_queue.get()
            await func(payment)

    return wrapper


async def check_pending_payments():
    """
    check_pending_payments is called during startup to check for pending payments with
    the backend and also to delete expired invoices. Incoming payments will be
    checked only once, outgoing pending payments will be checked regularly.
    """
    sleep_time = 60 * 30  # 30 minutes

    while settings.lnbits_running:
        funding_source = get_funding_source()
        if funding_source.__class__.__name__ == "VoidWallet":
            logger.warning("Task: skipping pending check for VoidWallet")
            await asyncio.sleep(sleep_time)
            continue
        start_time = time.time()
        pending_payments = await get_payments(
            since=(int(time.time()) - 60 * 60 * 24 * 15),  # 15 days ago
            complete=False,
            pending=True,
            exclude_uncheckable=True,
        )
        count = len(pending_payments)
        if count > 0:
            logger.info(f"Task: checking {count} pending payments of last 15 days...")
            for i, payment in enumerate(pending_payments):
                status = await payment.check_status()
                prefix = f"payment ({i+1} / {count})"
                if status.failed:
                    payment.status = PaymentState.FAILED
                    await update_payment(payment)
                    logger.debug(f"{prefix} failed {payment.checking_id}")
                elif status.success:
                    payment.fee = status.fee_msat or 0
                    payment.preimage = status.preimage
                    payment.status = PaymentState.SUCCESS
                    await update_payment(payment)
                    logger.debug(f"{prefix} success {payment.checking_id}")
                else:
                    logger.debug(f"{prefix} pending {payment.checking_id}")
                await asyncio.sleep(0.01)  # to avoid complete blocking
            logger.info(
                f"Task: pending check finished for {count} payments"
                f" (took {time.time() - start_time:0.3f} s)"
            )
        await asyncio.sleep(sleep_time)


async def invoice_callback_dispatcher(checking_id: str, is_internal: bool = False):
    """
    Takes an incoming payment, checks its status, and dispatches it to
    invoice_listeners from core and extensions.
    """
    payment = await get_standalone_payment(checking_id, incoming=True)
    if payment and payment.is_in:
        status = await payment.check_status()
        payment.fee = status.fee_msat or 0
        payment.preimage = status.preimage
        payment.status = PaymentState.SUCCESS
        await update_payment(payment)
        internal = "internal" if is_internal else ""
        logger.success(f"{internal} invoice {checking_id} settled")
        for name, send_chan in invoice_listeners.items():
            logger.trace(f"invoice listeners: sending to `{name}`")
            await send_chan.put(payment)


async def send_push_notification(subscription, title, body, url=""):
    vapid = Vapid()
    try:
        logger.debug("sending push notification")
        webpush(
            json.loads(subscription.data),
            json.dumps({"title": title, "body": body, "url": url}),
            (
                vapid.from_pem(bytes(settings.lnbits_webpush_privkey, "utf-8"))
                if settings.lnbits_webpush_privkey
                else None
            ),
            {"aud": "", "sub": "mailto:alan@lnbits.com"},
        )
    except WebPushException as e:
        if e.response and e.response.status_code == HTTPStatus.GONE:
            # cleanup unsubscribed or expired push subscriptions
            await delete_webpush_subscriptions(subscription.endpoint)
        else:
            logger.error(
                f"failed sending push notification: "
                f"{e.response.text if e.response else e}"
            )
