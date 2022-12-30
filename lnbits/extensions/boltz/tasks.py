import asyncio

from boltz_client.boltz import BoltzNotFoundException, BoltzSwapStatusException
from boltz_client.mempool import MempoolBlockHeightException
from loguru import logger

from lnbits.core.models import Payment
from lnbits.core.services import check_transaction_status
from lnbits.helpers import get_current_extension_name
from lnbits.tasks import register_invoice_listener

from .crud import (
    get_all_pending_reverse_submarine_swaps,
    get_all_pending_submarine_swaps,
    get_submarine_swap,
    get_reverse_submarine_swap,
    update_swap_status,
)
from .utils import create_boltz_client

"""
testcases for boltz startup
A. normal swaps
  1. test: create -> kill -> start -> startup invoice listeners -> pay onchain funds -> should complete
  2. test: create -> kill -> pay onchain funds -> start -> startup check  -> should complete
  3. test: create -> kill -> mine blocks and hit timeout -> start -> should go timeout/failed
  4. test: create -> kill -> pay to less onchain funds -> mine blocks hit timeout -> start lnbits -> should be refunded

B. reverse swaps
  1. test: create instant -> kill -> boltz does lockup -> not confirmed -> start lnbits -> should claim/complete
  2. test: create instant -> kill -> no lockup -> start lnbits -> should start onchain listener -> boltz does lockup -> should claim/complete (difficult to test)
  3. test: create -> kill -> boltz does lockup -> not confirmed -> start lnbits -> should start tx listener -> after confirmation -> should claim/complete
  4. test: create -> kill -> boltz does lockup -> confirmed -> start lnbits -> should claim/complete
  5. test: create -> kill -> boltz does lockup -> hit timeout -> boltz refunds -> start -> should timeout
"""


async def check_for_pending_swaps():
    client = create_boltz_client()
    try:
        swaps = await get_all_pending_submarine_swaps()
        reverse_swaps = await get_all_pending_reverse_submarine_swaps()
        if len(swaps) > 0 or len(reverse_swaps) > 0:
            logger.debug(f"Boltz - startup swap check")
    except:
        logger.error(
            f"Boltz - startup swap check, database is not created yet, do nothing"
        )
        return

    if len(swaps) > 0:
        logger.debug(f"Boltz - {len(swaps)} pending swaps")
        for swap in swaps:
            try:
                swap_status = client.swap_status(swap.boltz_id)
                payment_status = await check_transaction_status(
                    swap.wallet, swap.payment_hash
                )
                if payment_status.paid:
                    logger.debug(
                        f"Boltz - swap: {swap.boltz_id} got paid while offline."
                    )
                    await update_swap_status(swap.id, "complete")
                else:
                    logger.debug(f"Boltz - refunding swap: {swap.id}...")
                    try:
                        await client.refund_swap(
                            privkey_wif=swap.refund_privkey,
                            lockup_address=swap.address,
                            receive_address=swap.refund_address,
                            redeem_script_hex=swap.redeem_script,
                            timeout_block_height=swap.timeout_block_height,
                        )
                        await update_swap_status(swap.id, "refunded")
                    except MempoolBlockHeightException as exc:
                        logger.error(
                            f"Boltz - tried to refund swap: {swap.id}, but has not reached the timeout."
                        )

            except BoltzSwapStatusException as exc:
                logger.debug(f"Boltz - swap_status: {str(exc)}")
                await update_swap_status(swap.id, "failed")
            # should only happen while development when regtest is reset
            except BoltzNotFoundException as exc:
                logger.debug(f"Boltz - swap: {swap.boltz_id} does not exist.")
                await update_swap_status(swap.id, "failed")
            except Exception as exc:
                logger.error(
                    f"Boltz - unhandled exception, swap: {swap.id} - {str(exc)}"
                )

    if len(reverse_swaps) > 0:
        logger.debug(f"Boltz - {len(reverse_swaps)} pending reverse swaps")
        for reverse_swap in reverse_swaps:
            try:
                _ = client.swap_status(reverse_swap.boltz_id)
                await client.claim_reverse_swap(
                    lockup_address=reverse_swap.lockup_address,
                    receive_address=reverse_swap.onchain_address,
                    privkey_wif=reverse_swap.claim_privkey,
                    preimage_hex=reverse_swap.preimage,
                    redeem_script_hex=reverse_swap.redeem_script,
                    zeroconf=reverse_swap.instant_settlement,
                )

            except BoltzSwapStatusException as exc:
                logger.debug(f"Boltz - swap_status: {str(exc)}")
                await update_swap_status(reverse_swap.id, "failed")
            # should only happen while development when regtest is reset
            except BoltzNotFoundException as exc:
                logger.debug(
                    f"Boltz - reverse swap: {reverse_swap.boltz_id} does not exist."
                )
                await update_swap_status(reverse_swap.id, "failed")
            except Exception as exc:
                logger.error(
                    f"Boltz - unhandled exception, reverse swap: {reverse_swap.id} - {str(exc)}"
                )


async def wait_for_paid_invoices():
    invoice_queue = asyncio.Queue()
    register_invoice_listener(invoice_queue, get_current_extension_name())

    while True:
        payment = await invoice_queue.get()
        await on_invoice_paid(payment)


async def on_invoice_paid(payment: Payment) -> None:
    if payment.extra and "boltz" != payment.extra.get("tag"):
        # not a boltz invoice
        return

    await payment.set_pending(False)
    if payment.extra:
        swap_id = payment.extra.get("swap_id")
        if swap_id:
            swap = await get_submarine_swap(swap_id)
            if swap:
                logger.info(
                    f"Boltz - lightning invoice is paid, normal swap completed. swap_id: {swap_id}"
                )
                await update_swap_status(swap_id, "complete")
            reverse_swap = await get_reverse_submarine_swap(swap_id)
            if reverse_swap:
                logger.info(
                    f"Boltz - lightning invoice is paid, reverse swap completed. swap_id: {swap_id}"
                )
                await update_swap_status(swap_id, "complete")
