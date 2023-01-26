import asyncio
import hashlib
import json
import time
import uuid
from http import HTTPStatus
from io import BytesIO
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import ParseResult, parse_qs, urlencode, urlparse, urlunparse

import async_timeout
import httpx
import pyqrcode
from fastapi import (
    Body,
    Depends,
    Header,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.exceptions import HTTPException
from loguru import logger
from pydantic import BaseModel
from pydantic.fields import Field
from sse_starlette.sse import EventSourceResponse
from starlette.responses import RedirectResponse, StreamingResponse

from lnbits import bolt11, lnurl
from lnbits.core.helpers import migrate_extension_database
from lnbits.core.models import Payment, User, Wallet
from lnbits.decorators import (
    WalletTypeInfo,
    check_admin,
    get_key_type,
    require_admin_key,
    require_invoice_key,
)
from lnbits.extension_manager import (
    CreateExtension,
    Extension,
    ExtensionRelease,
    InstallableExtension,
    get_valid_extensions,
)
from lnbits.helpers import url_for
from lnbits.settings import get_wallet_class, settings
from lnbits.utils.exchange_rates import (
    currencies,
    fiat_amount_as_satoshis,
    satoshis_amount_as_fiat,
)

from .. import core_app, core_app_extra, db
from ..crud import (
    add_installed_extension,
    create_tinyurl,
    delete_installed_extension,
    delete_tinyurl,
    get_dbversions,
    get_payments,
    get_standalone_payment,
    get_tinyurl,
    get_tinyurl_by_url,
    get_total_balance,
    get_wallet_for_key,
    save_balance_check,
    update_wallet,
)
from ..services import (
    InvoiceFailure,
    PaymentFailure,
    check_transaction_status,
    create_invoice,
    pay_invoice,
    pay_with_keysend,
    perform_lnurlauth,
    websocketManager,
    websocketUpdater,
)
from ..tasks import api_invoice_listeners


@core_app.get("/api/v1/health", status_code=HTTPStatus.OK)
async def health():
    return


@core_app.get("/api/v1/wallet")
async def api_wallet(wallet: WalletTypeInfo = Depends(get_key_type)):
    if wallet.wallet_type == 0:
        return {
            "id": wallet.wallet.id,
            "name": wallet.wallet.name,
            "balance": wallet.wallet.balance_msat,
        }
    else:
        return {"name": wallet.wallet.name, "balance": wallet.wallet.balance_msat}


@core_app.put("/api/v1/wallet/{new_name}")
async def api_update_wallet(
    new_name: str, wallet: WalletTypeInfo = Depends(require_admin_key)
):
    await update_wallet(wallet.wallet.id, new_name)
    return {
        "id": wallet.wallet.id,
        "name": wallet.wallet.name,
        "balance": wallet.wallet.balance_msat,
    }


@core_app.get("/api/v1/payments")
async def api_payments(
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    wallet: WalletTypeInfo = Depends(get_key_type),
):
    pendingPayments = await get_payments(
        wallet_id=wallet.wallet.id,
        pending=True,
        exclude_uncheckable=True,
        limit=limit,
        offset=offset,
    )
    for payment in pendingPayments:
        await check_transaction_status(
            wallet_id=payment.wallet_id, payment_hash=payment.payment_hash
        )
    return await get_payments(
        wallet_id=wallet.wallet.id,
        pending=True,
        complete=True,
        limit=limit,
        offset=offset,
    )


class CreateInvoiceData(BaseModel):
    out: Optional[bool] = True
    amount: float = Query(None, ge=0)
    memo: Optional[str] = None
    unit: Optional[str] = "sat"
    description_hash: Optional[str] = None
    unhashed_description: Optional[str] = None
    expiry: Optional[int] = None
    lnurl_callback: Optional[str] = None
    lnurl_balance_check: Optional[str] = None
    extra: Optional[dict] = None
    webhook: Optional[str] = None
    internal: Optional[bool] = False
    bolt11: Optional[str] = None


async def api_payments_create_invoice(data: CreateInvoiceData, wallet: Wallet):
    if data.description_hash or data.unhashed_description:
        try:
            description_hash = (
                bytes.fromhex(data.description_hash) if data.description_hash else b""
            )
            unhashed_description = (
                bytes.fromhex(data.unhashed_description)
                if data.unhashed_description
                else b""
            )
        except ValueError:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="'description_hash' and 'unhashed_description' must be a valid hex strings",
            )
        memo = ""
    else:
        description_hash = b""
        unhashed_description = b""
        memo = data.memo or settings.lnbits_site_title

    if data.unit == "sat":
        amount = int(data.amount)
    else:
        assert data.unit is not None, "unit not set"
        price_in_sats = await fiat_amount_as_satoshis(data.amount, data.unit)
        amount = price_in_sats

    async with db.connect() as conn:
        try:
            payment_hash, payment_request = await create_invoice(
                wallet_id=wallet.id,
                amount=amount,
                memo=memo,
                description_hash=description_hash,
                unhashed_description=unhashed_description,
                expiry=data.expiry,
                extra=data.extra,
                webhook=data.webhook,
                internal=data.internal,
                conn=conn,
            )
        except InvoiceFailure as e:
            raise HTTPException(status_code=520, detail=str(e))
        except Exception as exc:
            raise exc

    invoice = bolt11.decode(payment_request)

    lnurl_response: Union[None, bool, str] = None
    if data.lnurl_callback:
        if data.lnurl_balance_check is not None:
            await save_balance_check(wallet.id, data.lnurl_balance_check)

        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(
                    data.lnurl_callback,
                    params={
                        "pr": payment_request,
                        "balanceNotify": url_for(
                            f"/withdraw/notify/{urlparse(data.lnurl_callback).netloc}",
                            external=True,
                            wal=wallet.id,
                        ),
                    },
                    timeout=10,
                )
                if r.is_error:
                    lnurl_response = r.text
                else:
                    resp = json.loads(r.text)
                    if resp["status"] != "OK":
                        lnurl_response = resp["reason"]
                    else:
                        lnurl_response = True
            except (httpx.ConnectError, httpx.RequestError) as ex:
                logger.error(ex)
                lnurl_response = False

    return {
        "payment_hash": invoice.payment_hash,
        "payment_request": payment_request,
        # maintain backwards compatibility with API clients:
        "checking_id": invoice.payment_hash,
        "lnurl_response": lnurl_response,
    }


async def api_payments_pay_invoice(bolt11: str, wallet: Wallet):
    try:
        payment_hash = await pay_invoice(wallet_id=wallet.id, payment_request=bolt11)
    except ValueError as e:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail=str(e))
    except PaymentFailure as e:
        raise HTTPException(status_code=520, detail=str(e))
    except Exception as exc:
        raise exc

    return {
        "payment_hash": payment_hash,
        # maintain backwards compatibility with API clients:
        "checking_id": payment_hash,
    }


@core_app.post(
    "/api/v1/payments",
    status_code=HTTPStatus.CREATED,
)
async def api_payments_create(
    wallet: WalletTypeInfo = Depends(require_invoice_key),
    invoiceData: CreateInvoiceData = Body(...),
):
    if invoiceData.out is True and wallet.wallet_type == 0:
        if not invoiceData.bolt11:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="BOLT11 string is invalid or not given",
            )
        return await api_payments_pay_invoice(
            invoiceData.bolt11, wallet.wallet
        )  # admin key
    elif not invoiceData.out:
        # invoice key
        return await api_payments_create_invoice(invoiceData, wallet.wallet)
    else:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED,
            detail="Invoice (or Admin) key required.",
        )


class CreateKeysendData(BaseModel):
    pubkey: str
    amount: int
    comment: Optional[str] = None

@core_app.post("/api/v1/payments/keysend")
async def api_payments_pay_keysend(
    data: CreateKeysendData, wallet: WalletTypeInfo = Depends(require_admin_key)
):
    payment_hash = await pay_with_keysend(
        wallet_id=wallet.wallet.id,
        public_key=data.pubkey,
        amount_msat=data.amount,
        description=data.comment,
    )

    return {
        "payment_hash": payment_hash,
    }

class CreateLNURLData(BaseModel):
    description_hash: str
    callback: str
    amount: int
    comment: Optional[str] = None
    description: Optional[str] = None

@core_app.post("/api/v1/payments/lnurl")
async def api_payments_pay_lnurl(
    data: CreateLNURLData, wallet: WalletTypeInfo = Depends(require_admin_key)
):
    domain = urlparse(data.callback).netloc

    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                data.callback,
                params={"amount": data.amount, "comment": data.comment},
                timeout=40,
            )
            if r.is_error:
                raise httpx.ConnectError("LNURL callback connection error")
        except (httpx.ConnectError, httpx.RequestError):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"Failed to connect to {domain}.",
            )

    params = json.loads(r.text)
    if params.get("status") == "ERROR":
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"{domain} said: '{params.get('reason', '')}'",
        )

    if not params.get("pr"):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"{domain} did not return a payment request.",
        )

    invoice = bolt11.decode(params["pr"])
    if invoice.amount_msat != data.amount:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"{domain} returned an invalid invoice. Expected {data.amount} msat, got {invoice.amount_msat}.",
        )

    if invoice.description_hash != data.description_hash:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"{domain} returned an invalid invoice. Expected description_hash == {data.description_hash}, got {invoice.description_hash}.",
        )

    extra = {}

    if params.get("successAction"):
        extra["success_action"] = params["successAction"]
    if data.comment:
        extra["comment"] = data.comment
    assert data.description is not None, "description is required"
    payment_hash = await pay_invoice(
        wallet_id=wallet.wallet.id,
        payment_request=params["pr"],
        description=data.description,
        extra=extra,
    )

    return {
        "success_action": params.get("successAction"),
        "payment_hash": payment_hash,
        # maintain backwards compatibility with API clients:
        "checking_id": payment_hash,
    }


async def subscribe_wallet_invoices(request: Request, wallet: Wallet):
    """
    Subscribe to new invoices for a wallet. Can be wrapped in EventSourceResponse.
    Listenes invoming payments for a wallet and yields jsons with payment details.
    """
    this_wallet_id = wallet.id

    payment_queue: asyncio.Queue[Payment] = asyncio.Queue(0)

    uid = f"{this_wallet_id}_{str(uuid.uuid4())[:8]}"
    logger.debug(f"adding sse listener for wallet: {uid}")
    api_invoice_listeners[uid] = payment_queue

    send_queue: asyncio.Queue[Tuple[str, Payment]] = asyncio.Queue(0)

    async def payment_received() -> None:
        while True:
            try:
                async with async_timeout.timeout(1):
                    payment: Payment = await payment_queue.get()
                    if payment.wallet_id == this_wallet_id:
                        logger.debug("sse listener: payment receieved", payment)
                        await send_queue.put(("payment-received", payment))
            except asyncio.TimeoutError:
                pass

    task = asyncio.create_task(payment_received())

    try:
        while True:
            if await request.is_disconnected():
                await request.close()
                break
            typ, data = await send_queue.get()
            if data:
                jdata = json.dumps(dict(data.dict(), pending=False))

            yield dict(data=jdata, event=typ)
    except asyncio.CancelledError:
        logger.debug(f"removing listener for wallet {uid}")
        api_invoice_listeners.pop(uid)
        task.cancel()
        return


@core_app.get("/api/v1/payments/sse")
async def api_payments_sse(
    request: Request, wallet: WalletTypeInfo = Depends(get_key_type)
):
    return EventSourceResponse(
        subscribe_wallet_invoices(request, wallet.wallet),
        ping=20,
        media_type="text/event-stream",
    )


@core_app.get("/api/v1/payments/{payment_hash}")
async def api_payment(payment_hash, X_Api_Key: Optional[str] = Header(None)):
    # We use X_Api_Key here because we want this call to work with and without keys
    # If a valid key is given, we also return the field "details", otherwise not
    wallet = await get_wallet_for_key(X_Api_Key) if type(X_Api_Key) == str else None

    # we have to specify the wallet id here, because postgres and sqlite return internal payments in different order
    # and get_standalone_payment otherwise just fetches the first one, causing unpredictable results
    payment = await get_standalone_payment(
        payment_hash, wallet_id=wallet.id if wallet else None
    )
    if payment is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Payment does not exist."
        )
    await check_transaction_status(payment.wallet_id, payment_hash)
    payment = await get_standalone_payment(
        payment_hash, wallet_id=wallet.id if wallet else None
    )
    if not payment:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Payment does not exist."
        )
    elif not payment.pending:
        if wallet and wallet.id == payment.wallet_id:
            return {"paid": True, "preimage": payment.preimage, "details": payment}
        return {"paid": True, "preimage": payment.preimage}

    try:
        await payment.check_status()
    except Exception:
        if wallet and wallet.id == payment.wallet_id:
            return {"paid": False, "details": payment}
        return {"paid": False}

    if wallet and wallet.id == payment.wallet_id:
        return {
            "paid": not payment.pending,
            "preimage": payment.preimage,
            "details": payment,
        }
    return {"paid": not payment.pending, "preimage": payment.preimage}


@core_app.get("/api/v1/lnurlscan/{code}")
async def api_lnurlscan(code: str, wallet: WalletTypeInfo = Depends(get_key_type)):
    try:
        url = lnurl.decode(code)
        domain = urlparse(url).netloc
    except:
        # parse internet identifier (user@domain.com)
        name_domain = code.split("@")
        if len(name_domain) == 2 and len(name_domain[1].split(".")) >= 2:
            name, domain = name_domain
            url = (
                ("http://" if domain.endswith(".onion") else "https://")
                + domain
                + "/.well-known/lnurlp/"
                + name
            )
            # will proceed with these values
        else:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST, detail="invalid lnurl"
            )

    # params is what will be returned to the client
    params: Dict = {"domain": domain}

    if "tag=login" in url:
        params.update(kind="auth")
        params.update(callback=url)  # with k1 already in it

        lnurlauth_key = wallet.wallet.lnurlauth_key(domain)
        params.update(pubkey=lnurlauth_key.verifying_key.to_string("compressed").hex())
    else:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=5)
            if r.is_error:
                raise HTTPException(
                    status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                    detail={"domain": domain, "message": "failed to get parameters"},
                )

        try:
            data = json.loads(r.text)
        except json.decoder.JSONDecodeError:
            raise HTTPException(
                status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                detail={
                    "domain": domain,
                    "message": f"got invalid response '{r.text[:200]}'",
                },
            )

        try:
            tag: str = data.get("tag")
            params.update(**data)
            if tag == "channelRequest":
                raise HTTPException(
                    status_code=HTTPStatus.BAD_REQUEST,
                    detail={
                        "domain": domain,
                        "kind": "channel",
                        "message": "unsupported",
                    },
                )
            elif tag == "withdrawRequest":
                params.update(kind="withdraw")
                params.update(fixed=data["minWithdrawable"] == data["maxWithdrawable"])

                # callback with k1 already in it
                parsed_callback: ParseResult = urlparse(data["callback"])
                qs: Dict = parse_qs(parsed_callback.query)
                qs["k1"] = data["k1"]

                # balanceCheck/balanceNotify
                if "balanceCheck" in data:
                    params.update(balanceCheck=data["balanceCheck"])

                # format callback url and send to client
                parsed_callback = parsed_callback._replace(
                    query=urlencode(qs, doseq=True)
                )
                params.update(callback=urlunparse(parsed_callback))
            elif tag == "payRequest":
                params.update(kind="pay")
                params.update(fixed=data["minSendable"] == data["maxSendable"])

                params.update(
                    description_hash=hashlib.sha256(
                        data["metadata"].encode()
                    ).hexdigest()
                )
                metadata = json.loads(data["metadata"])
                for [k, v] in metadata:
                    if k == "text/plain":
                        params.update(description=v)
                    if k == "image/jpeg;base64" or k == "image/png;base64":
                        data_uri = "data:" + k + "," + v
                        params.update(image=data_uri)
                    if k == "text/email" or k == "text/identifier":
                        params.update(targetUser=v)
                params.update(commentAllowed=data.get("commentAllowed", 0))

        except KeyError as exc:
            raise HTTPException(
                status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                detail={
                    "domain": domain,
                    "message": f"lnurl JSON response invalid: {exc}",
                },
            )

    return params


class DecodePayment(BaseModel):
    data: str


@core_app.post("/api/v1/payments/decode", status_code=HTTPStatus.OK)
async def api_payments_decode(data: DecodePayment, response: Response):
    payment_str = data.data
    try:
        if payment_str[:5] == "LNURL":
            url = lnurl.decode(payment_str)
            return {"domain": url}
        else:
            invoice = bolt11.decode(payment_str)
            return {
                "payment_hash": invoice.payment_hash,
                "amount_msat": invoice.amount_msat,
                "description": invoice.description,
                "description_hash": invoice.description_hash,
                "payee": invoice.payee,
                "date": invoice.date,
                "expiry": invoice.expiry,
                "secret": invoice.secret,
                "route_hints": invoice.route_hints,
                "min_final_cltv_expiry": invoice.min_final_cltv_expiry,
            }
    except:
        response.status_code = HTTPStatus.BAD_REQUEST
        return {"message": "Failed to decode"}


class Callback(BaseModel):
    callback: str = Query(...)


@core_app.post("/api/v1/lnurlauth")
async def api_perform_lnurlauth(
    callback: Callback, wallet: WalletTypeInfo = Depends(require_admin_key)
):
    err = await perform_lnurlauth(callback.callback, wallet=wallet)
    if err:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE, detail=err.reason
        )
    return ""


@core_app.get("/api/v1/currencies")
async def api_list_currencies_available():
    return list(currencies.keys())


class ConversionData(BaseModel):
    from_: str = Field("sat", alias="from")
    amount: float
    to: str = Query("usd")


@core_app.post("/api/v1/conversion")
async def api_fiat_as_sats(data: ConversionData):
    output = {}
    if data.from_ == "sat":
        output["BTC"] = data.amount / 100000000
        output["sats"] = int(data.amount)
        for currency in data.to.split(","):
            output[currency.strip().upper()] = await satoshis_amount_as_fiat(
                data.amount, currency.strip()
            )
        return output
    else:
        output[data.from_.upper()] = data.amount
        output["sats"] = await fiat_amount_as_satoshis(data.amount, data.from_)
        output["BTC"] = output["sats"] / 100000000
        return output


@core_app.get("/api/v1/qrcode/{data}", response_class=StreamingResponse)
async def img(request: Request, data):
    qr = pyqrcode.create(data)
    stream = BytesIO()
    qr.svg(stream, scale=3)
    stream.seek(0)

    async def _generator(stream: BytesIO):
        yield stream.getvalue()

    return StreamingResponse(
        _generator(stream),
        headers={
            "Content-Type": "image/svg+xml",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@core_app.get("/api/v1/audit", dependencies=[Depends(check_admin)])
async def api_auditor():
    WALLET = get_wallet_class()
    total_balance = await get_total_balance()
    error_message, node_balance = await WALLET.status()

    if not error_message:
        delta = node_balance - total_balance
    else:
        node_balance, delta = None, None

    return {
        "node_balance_msats": int(node_balance),
        "lnbits_balance_msats": int(total_balance),
        "delta_msats": int(delta),
        "timestamp": int(time.time()),
    }


##################UNIVERSAL WEBSOCKET MANAGER########################


@core_app.websocket("/api/v1/ws/{item_id}")
async def websocket_connect(websocket: WebSocket, item_id: str):
    await websocketManager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        websocketManager.disconnect(websocket)


@core_app.post("/api/v1/ws/{item_id}")
async def websocket_update_post(item_id: str, data: str):
    try:
        await websocketUpdater(item_id, data)
        return {"sent": True, "data": data}
    except:
        return {"sent": False, "data": data}


@core_app.get("/api/v1/ws/{item_id}/{data}")
async def websocket_update_get(item_id: str, data: str):
    try:
        await websocketUpdater(item_id, data)
        return {"sent": True, "data": data}
    except:
        return {"sent": False, "data": data}


@core_app.post("/api/v1/extension")
async def api_install_extension(
    data: CreateExtension, user: User = Depends(check_admin)
):

    release = await InstallableExtension.get_extension_release(
        data.ext_id, data.source_repo, data.archive
    )
    if not release:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Release not found"
        )
    ext_info = InstallableExtension(
        id=data.ext_id, name=data.ext_id, installed_release=release, icon=release.icon
    )

    ext_info.download_archive()

    try:
        ext_info.extract_archive()

        extension = Extension.from_installable_ext(ext_info)

        db_version = (await get_dbversions()).get(data.ext_id, 0)
        await migrate_extension_database(extension, db_version)

        await add_installed_extension(ext_info)
        if data.ext_id not in settings.lnbits_deactivated_extensions:
            settings.lnbits_deactivated_extensions += [data.ext_id]

        # mount routes for the new version
        core_app_extra.register_new_ext_routes(extension)

        if extension.upgrade_hash:
            ext_info.nofiy_upgrade()

    except Exception as ex:
        logger.warning(ex)
        ext_info.clean_extension_files()
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail="Failed to install extension.",
        )


@core_app.delete("/api/v1/extension/{ext_id}")
async def api_uninstall_extension(ext_id: str, user: User = Depends(check_admin)):

    installable_extensions: List[
        InstallableExtension
    ] = await InstallableExtension.get_installable_extensions()

    extensions = [e for e in installable_extensions if e.id == ext_id]
    if len(extensions) == 0:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"Unknown extension id: {ext_id}",
        )

    # check that other extensions do not depend on this one
    for valid_ext_id in list(map(lambda e: e.code, get_valid_extensions())):
        installed_ext = next(
            (ext for ext in installable_extensions if ext.id == valid_ext_id), None
        )
        if installed_ext and ext_id in installed_ext.dependencies:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"Cannot uninstall. Extension '{installed_ext.name}' depends on this one.",
            )

    try:
        if ext_id not in settings.lnbits_deactivated_extensions:
            settings.lnbits_deactivated_extensions += [ext_id]

        for ext_info in extensions:
            ext_info.clean_extension_files()
            await delete_installed_extension(ext_id=ext_info.id)

    except Exception as ex:
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(ex)
        )


@core_app.get("/api/v1/extension/{ext_id}/releases")
async def get_extension_releases(ext_id: str, user: User = Depends(check_admin)):
    try:
        extension_releases: List[
            ExtensionRelease
        ] = await InstallableExtension.get_extension_releases(ext_id)

        return extension_releases

    except Exception as ex:
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(ex)
        )


############################TINYURL##################################


@core_app.post("/api/v1/tinyurl")
async def api_create_tinyurl(
    url: str, endless: bool = False, wallet: WalletTypeInfo = Depends(get_key_type)
):
    tinyurls = await get_tinyurl_by_url(url)
    try:
        for tinyurl in tinyurls:
            if tinyurl:
                if tinyurl.wallet == wallet.wallet.inkey:
                    return tinyurl
        return await create_tinyurl(url, endless, wallet.wallet.inkey)
    except:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="Unable to create tinyurl"
        )


@core_app.get("/api/v1/tinyurl/{tinyurl_id}")
async def api_get_tinyurl(
    tinyurl_id: str, wallet: WalletTypeInfo = Depends(get_key_type)
):
    try:
        tinyurl = await get_tinyurl(tinyurl_id)
        if tinyurl:
            if tinyurl.wallet == wallet.wallet.inkey:
                return tinyurl
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN, detail="Wrong key provided."
        )
    except:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Unable to fetch tinyurl"
        )


@core_app.delete("/api/v1/tinyurl/{tinyurl_id}")
async def api_delete_tinyurl(
    tinyurl_id: str, wallet: WalletTypeInfo = Depends(get_key_type)
):
    try:
        tinyurl = await get_tinyurl(tinyurl_id)
        if tinyurl:
            if tinyurl.wallet == wallet.wallet.inkey:
                await delete_tinyurl(tinyurl_id)
                return {"deleted": True}
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN, detail="Wrong key provided."
        )
    except:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="Unable to delete"
        )


@core_app.get("/t/{tinyurl_id}")
async def api_tinyurl(tinyurl_id: str):
    try:
        tinyurl = await get_tinyurl(tinyurl_id)
        if tinyurl:
            response = RedirectResponse(url=tinyurl.url)
            return response
        else:
            return
    except:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="unable to find tinyurl"
        )
