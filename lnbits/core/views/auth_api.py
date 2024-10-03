import base64
import importlib
import json
from http import HTTPStatus
from time import time
from typing import Callable, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi_sso.sso.base import OpenID, SSOBase
from loguru import logger

from lnbits.core.services import create_user_account
from lnbits.decorators import access_token_payload, check_user_exists
from lnbits.helpers import (
    create_access_token,
    decrypt_internal_message,
    encrypt_internal_message,
    is_valid_email_address,
    is_valid_username,
)
from lnbits.settings import AuthMethods, settings
from lnbits.utils.nostr import normalize_public_key, verify_event

from ..crud import (
    get_account,
    get_account_by_email,
    get_account_by_pubkey,
    get_account_by_username,
    get_account_by_username_or_email,
    get_user,
    update_account,
)
from ..models import (
    AccessTokenPayload,
    Account,
    CreateUser,
    LoginUsernamePassword,
    LoginUsr,
    ResetUserPassword,
    UpdateSuperuserPassword,
    UpdateUser,
    UpdateUserPassword,
    UpdateUserPubkey,
    User,
    UserExtra,
)

auth_router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


@auth_router.get("", description="Get the authenticated user")
async def get_auth_user(user: User = Depends(check_user_exists)) -> User:
    return user


@auth_router.post("", description="Login via the username and password")
async def login(data: LoginUsernamePassword) -> JSONResponse:
    if not settings.is_auth_method_allowed(AuthMethods.username_and_password):
        raise HTTPException(
            HTTPStatus.UNAUTHORIZED, "Login by 'Username and Password' not allowed."
        )
    account = await get_account_by_username_or_email(data.username)
    if not account or not account.verify_password(data.password):
        raise HTTPException(HTTPStatus.UNAUTHORIZED, "Invalid credentials.")
    return _auth_success_response(account.username, account.id, account.email)


@auth_router.post("/nostr", description="Login via Nostr")
async def nostr_login(request: Request) -> JSONResponse:
    if not settings.is_auth_method_allowed(AuthMethods.nostr_auth_nip98):
        raise HTTPException(
            HTTPStatus.UNAUTHORIZED, "Login with Nostr Auth not allowed."
        )
    event = _nostr_nip98_event(request)
    account = await get_account_by_pubkey(event["pubkey"])
    if not account:
        account = Account(
            id=uuid4().hex,
            pubkey=event["pubkey"],
            extra=UserExtra(provider="nostr"),
        )
        await create_user_account(account)
    return _auth_success_response(account.username or "", account.id, account.email)


@auth_router.post("/usr", description="Login via the User ID")
async def login_usr(data: LoginUsr) -> JSONResponse:
    if not settings.is_auth_method_allowed(AuthMethods.user_id_only):
        raise HTTPException(
            HTTPStatus.UNAUTHORIZED,
            "Login by 'User ID' not allowed.",
        )
    account = await get_account(data.usr)
    if not account:
        raise HTTPException(HTTPStatus.UNAUTHORIZED, "User ID does not exist.")
    return _auth_success_response(account.username, account.id, account.email)


@auth_router.get("/{provider}", description="SSO Provider")
async def login_with_sso_provider(
    request: Request, provider: str, user_id: Optional[str] = None
):
    provider_sso = _new_sso(provider)
    if not provider_sso:
        raise HTTPException(
            HTTPStatus.UNAUTHORIZED,
            f"Login by '{provider}' not allowed.",
        )

    provider_sso.redirect_uri = str(request.base_url) + f"api/v1/auth/{provider}/token"
    with provider_sso:
        state = encrypt_internal_message(user_id)
        return await provider_sso.get_login_redirect(state=state)


@auth_router.get("/{provider}/token", description="Handle OAuth callback")
async def handle_oauth_token(request: Request, provider: str) -> RedirectResponse:
    provider_sso = _new_sso(provider)
    if not provider_sso:
        raise HTTPException(
            HTTPStatus.UNAUTHORIZED,
            f"Login by '{provider}' not allowed.",
        )

    with provider_sso:
        userinfo = await provider_sso.verify_and_process(request)
        if not userinfo:
            raise HTTPException(HTTPStatus.UNAUTHORIZED, "Invalid user info.")
        user_id = decrypt_internal_message(provider_sso.state)
    request.session.pop("user", None)
    return await _handle_sso_login(userinfo, user_id)


@auth_router.post("/logout")
async def logout() -> JSONResponse:
    response = JSONResponse({"status": "success"}, HTTPStatus.OK)
    response.delete_cookie("cookie_access_token")
    response.delete_cookie("is_lnbits_user_authorized")
    response.delete_cookie("is_access_token_expired")
    response.delete_cookie("lnbits_last_active_wallet")

    return response


@auth_router.post("/register")
async def register(data: CreateUser) -> JSONResponse:
    if not settings.is_auth_method_allowed(AuthMethods.username_and_password):
        raise HTTPException(
            HTTPStatus.UNAUTHORIZED,
            "Register by 'Username and Password' not allowed.",
        )

    if data.password != data.password_repeat:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Passwords do not match.")

    if not data.username:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Missing username.")
    if not is_valid_username(data.username):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Invalid username.")

    if await get_account_by_username(data.username):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Username already exists.")

    if data.email and not is_valid_email_address(data.email):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Invalid email.")

    account = Account(
        id=uuid4().hex,
        email=data.email,
        username=data.username,
    )
    account.hash_password(data.password)
    await create_user_account(account)
    return _auth_success_response(account.username, account.id, account.email)


@auth_router.put("/pubkey")
async def update_pubkey(
    data: UpdateUserPubkey,
    user: User = Depends(check_user_exists),
    payload: AccessTokenPayload = Depends(access_token_payload),
) -> Optional[User]:
    if data.user_id != user.id:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Invalid user ID.")

    _validate_auth_timeout(payload.auth_time)
    if (
        data.pubkey
        and data.pubkey != user.pubkey
        and await get_account_by_pubkey(data.pubkey)
    ):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Public key already in use.")

    account = await get_account(user.id)
    if not account:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Account not found.")

    account.pubkey = normalize_public_key(data.pubkey)
    await update_account(account)
    return await get_user(account)


@auth_router.put("/password")
async def update_password(
    data: UpdateUserPassword,
    user: User = Depends(check_user_exists),
    payload: AccessTokenPayload = Depends(access_token_payload),
) -> Optional[User]:
    _validate_auth_timeout(payload.auth_time)
    assert data.user_id == user.id, "Invalid user ID."
    if (
        data.username
        and user.username != data.username
        and await get_account_by_username(data.username)
    ):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Username already exists.")

    account = await get_account(user.id)
    assert account, "Account not found."

    # old accounts do not have a password
    if account.password_hash:
        assert data.password_old, "Missing old password."
        assert account.verify_password(data.password_old), "Invalid old password."

    account.username = data.username
    account.hash_password(data.password)
    await update_account(account)
    _user = await get_user(account)
    if not _user:
        raise HTTPException(HTTPStatus.NOT_FOUND, "User not found.")
    return _user


@auth_router.put("/reset")
async def reset_password(data: ResetUserPassword) -> JSONResponse:
    if not settings.is_auth_method_allowed(AuthMethods.username_and_password):
        raise HTTPException(
            HTTPStatus.UNAUTHORIZED, "Auth by 'Username and Password' not allowed."
        )

    assert data.password == data.password_repeat, "Passwords do not match."
    assert data.reset_key[:10].startswith("reset_key_"), "This is not a reset key."

    try:
        reset_key = base64.b64decode(data.reset_key[10:]).decode()
        reset_data_json = decrypt_internal_message(reset_key)
    except Exception as exc:
        raise ValueError("Invalid reset key.") from exc

    assert reset_data_json, "Cannot process reset key."

    action, user_id, request_time = json.loads(reset_data_json)
    assert action, "Missing action."
    assert user_id, "Missing user ID."
    assert request_time, "Missing reset time."

    _validate_auth_timeout(request_time)

    account = await get_account(user_id)
    if not account:
        raise HTTPException(HTTPStatus.NOT_FOUND, "User not found.")

    account.hash_password(data.password)
    await update_account(account)
    return _auth_success_response(account.username, user_id, account.email)


@auth_router.put("/update")
async def update(
    data: UpdateUser, user: User = Depends(check_user_exists)
) -> Optional[User]:
    if data.user_id != user.id:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Invalid user ID.")
    if data.username and not is_valid_username(data.username):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Invalid username.")
    if data.email != user.email:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST,
            "Email mismatch.",
        )
    if (
        data.username
        and user.username != data.username
        and await get_account_by_username(data.username)
    ):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Username already exists.")
    if (
        data.email
        and data.email != user.email
        and await get_account_by_email(data.email)
    ):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Email already exists.")

    account = await get_account(user.id)
    if not account:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Account not found.")

    if data.username:
        account.username = data.username
    if data.email:
        account.email = data.email
    if data.extra:
        account.extra = data.extra

    await update_account(account)
    return await get_user(account)


@auth_router.put("/first_install")
async def first_install(data: UpdateSuperuserPassword) -> JSONResponse:
    if not settings.first_install:
        raise HTTPException(HTTPStatus.UNAUTHORIZED, "This is not your first install")
    account = await get_account(settings.super_user)
    if not account:
        raise HTTPException(HTTPStatus.INTERNAL_SERVER_ERROR, "Superuser not found.")
    account.username = data.username
    account.extra = account.extra or UserExtra()
    account.extra.provider = "lnbits"
    account.hash_password(data.password)
    await update_account(account)
    settings.first_install = False
    return _auth_success_response(account.username, account.id, account.email)


async def _handle_sso_login(userinfo: OpenID, verified_user_id: Optional[str] = None):
    email = userinfo.email
    if not email or not is_valid_email_address(email):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Invalid email.")

    redirect_path = "/wallet"
    account = await get_account_by_email(email)

    if verified_user_id:
        if account:
            raise HTTPException(HTTPStatus.UNAUTHORIZED, "Email already used.")
        account = await get_account(verified_user_id)
        if not account:
            raise HTTPException(HTTPStatus.UNAUTHORIZED, "Cannot verify user email.")
        redirect_path = "/account"

    if account:
        account.extra = account.extra or UserExtra()
        account.extra.email_verified = True
        await update_account(account)
    else:
        account = Account(
            id=uuid4().hex, email=email, extra=UserExtra(email_verified=True)
        )
        await create_user_account(account)
    return _auth_redirect_response(redirect_path, email)


def _auth_success_response(
    username: Optional[str] = None,
    user_id: Optional[str] = None,
    email: Optional[str] = None,
) -> JSONResponse:
    payload = AccessTokenPayload(
        sub=username or "", usr=user_id, email=email, auth_time=int(time())
    )
    access_token = create_access_token(data=payload.dict())
    response = JSONResponse({"access_token": access_token, "token_type": "bearer"})
    response.set_cookie("cookie_access_token", access_token, httponly=True)
    response.set_cookie("is_lnbits_user_authorized", "true")
    response.delete_cookie("is_access_token_expired")

    return response


def _auth_redirect_response(path: str, email: str) -> RedirectResponse:
    payload = AccessTokenPayload(sub="" or "", email=email, auth_time=int(time()))
    access_token = create_access_token(data=payload.dict())
    response = RedirectResponse(path)
    response.set_cookie("cookie_access_token", access_token, httponly=True)
    response.set_cookie("is_lnbits_user_authorized", "true")
    response.delete_cookie("is_access_token_expired")
    return response


def _new_sso(provider: str) -> Optional[SSOBase]:
    try:
        if not settings.is_auth_method_allowed(AuthMethods(f"{provider}-auth")):
            return None

        client_id = getattr(settings, f"{provider}_client_id", None)
        client_secret = getattr(settings, f"{provider}_client_secret", None)
        discovery_url = getattr(settings, f"{provider}_discovery_url", None)

        if not client_id or not client_secret:
            logger.warning(f"{provider} auth allowed but not configured.")
            return None

        sso_provider_class = _find_auth_provider_class(provider)
        sso_provider = sso_provider_class(
            client_id, client_secret, None, allow_insecure_http=True
        )
        if (
            discovery_url
            and getattr(sso_provider, "discovery_url", discovery_url) != discovery_url
        ):
            sso_provider.discovery_url = discovery_url
        return sso_provider
    except Exception as e:
        logger.warning(e)

    return None


def _find_auth_provider_class(provider: str) -> Callable:
    sso_modules = ["lnbits.core.sso", "fastapi_sso.sso"]
    for module in sso_modules:
        try:
            provider_module = importlib.import_module(f"{module}.{provider}")
            provider_class = getattr(provider_module, f"{provider.title()}SSO")
            if provider_class:
                return provider_class
        except Exception:
            pass

    raise ValueError(f"No SSO provider found for '{provider}'.")


def _nostr_nip98_event(request: Request) -> dict:
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(HTTPStatus.UNAUTHORIZED, "Nostr Auth header missing.")
    scheme, token = auth_header.split()
    if scheme.lower() != "nostr":
        raise HTTPException(HTTPStatus.UNAUTHORIZED, "Invalid Authorization scheme.")
    event = None
    try:
        event_json = base64.b64decode(token.encode("ascii"))
        event = json.loads(event_json)
    except Exception as exc:
        logger.warning(exc)
    assert event, "Nostr login event cannot be parsed."

    if not verify_event(event):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Nostr login event is not valid.")
    assert event["kind"] == 27_235, "Invalid event kind."

    auth_threshold = settings.auth_credetials_update_threshold
    assert (
        abs(time() - event["created_at"]) < auth_threshold
    ), f"More than {auth_threshold} seconds have passed since the event was signed."

    method: Optional[str] = next((v for k, v in event["tags"] if k == "method"), None)
    assert method, "Tag 'method' is missing."
    assert method.upper() == "POST", "Invalid value for tag 'method'."

    url = next((v for k, v in event["tags"] if k == "u"), None)

    assert url, "Tag 'u' for URL is missing."
    accepted_urls = [f"{u}/nostr" for u in settings.nostr_absolute_request_urls]
    assert url in accepted_urls, f"Invalid value for tag 'u': '{url}'."

    return event


def _validate_auth_timeout(auth_time: Optional[int] = 0):
    if abs(time() - (auth_time or 0)) > settings.auth_credetials_update_threshold:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST,
            "You can only update your credentials in the first"
            f" {settings.auth_credetials_update_threshold} seconds."
            " Please login again or ask a new reset key!",
        )
