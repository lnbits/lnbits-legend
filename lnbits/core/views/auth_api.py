import base64
import importlib
import json
from time import time
from http import HTTPStatus
from typing import Callable, Optional

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
    urlsafe_short_hash,
)
from lnbits.settings import AuthMethods, settings
from lnbits.utils.nostr import normalize_public_key, verify_event

from ..crud import (
    create_account,
    get_account,
    get_account_by_email,
    get_account_by_pubkey,
    get_account_by_username,
    get_account_by_username_or_email,
    get_user,
    get_user_password,
    update_account,
    update_user_password,
    update_user_pubkey,
    verify_user_password,
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
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED, detail="Invalid credentials."
        )
    return _auth_success_response(account.username, account.id)


@auth_router.post("/nostr", description="Login via Nostr")
async def nostr_login(request: Request) -> JSONResponse:
    if not settings.is_auth_method_allowed(AuthMethods.nostr_auth_nip98):
        raise HTTPException(HTTP_401_UNAUTHORIZED, "Login with Nostr Auth not allowed.")

    try:
        event = _nostr_nip98_event(request)

        user = await get_account_by_pubkey(event["pubkey"])
        if not user:
            user = await create_user_account(
                pubkey=event["pubkey"], user_config=UserConfig(provider="nostr")
            )

        return _auth_success_response(user.username or "", user.id, user.email)
    except HTTPException as exc:
        raise exc
    except AssertionError as exc:
        raise HTTPException(HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except Exception as exc:
        logger.warning(exc)
        raise HTTPException(HTTP_500_INTERNAL_SERVER_ERROR, "Cannot login.") from exc


@auth_router.post("/usr", description="Login via the User ID")
async def login_usr(data: LoginUsr) -> JSONResponse:
    if not settings.is_auth_method_allowed(AuthMethods.user_id_only):
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED,
            detail="Login by 'User ID' not allowed.",
        )
    account = await get_account(data.usr)
    if not account:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED, detail="User ID does not exist."
        )
    return _auth_success_response(account.username, account.id)


@auth_router.get("/{provider}", description="SSO Provider")
async def login_with_sso_provider(
    request: Request, provider: str, user_id: Optional[str] = None
):
    provider_sso = _new_sso(provider)
    if not provider_sso:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED,
            detail=f"Login by '{provider}' not allowed.",
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
            status_code=HTTPStatus.UNAUTHORIZED,
            detail=f"Login by '{provider}' not allowed.",
        )

    try:
        with provider_sso:
            userinfo = await provider_sso.verify_and_process(request)
            assert userinfo is not None
            user_id = decrypt_internal_message(provider_sso.state)
        request.session.pop("user", None)
        return await _handle_sso_login(userinfo, user_id)
    except HTTPException as exc:
        raise exc
    except ValueError as exc:
        raise HTTPException(HTTPStatus.FORBIDDEN, str(exc)) from exc
    except Exception as exc:
        logger.debug(exc)
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=f"Cannot authenticate user with {provider} Auth.",
        ) from exc


@auth_router.post("/logout")
async def logout() -> JSONResponse:
    response = JSONResponse({"status": "success"}, status_code=HTTPStatus.OK)
    response.delete_cookie("cookie_access_token")
    response.delete_cookie("is_lnbits_user_authorized")
    response.delete_cookie("is_access_token_expired")
    response.delete_cookie("lnbits_last_active_wallet")

    return response


@auth_router.post("/register")
async def register(data: CreateUser) -> JSONResponse:
    if not settings.is_auth_method_allowed(AuthMethods.username_and_password):
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED,
            detail="Register by 'Username and Password' not allowed.",
        )

    if data.password != data.password_repeat:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="Passwords do not match."
        )

    if not data.username:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="Missing username."
        )
    if not is_valid_username(data.username):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="Invalid username."
        )

    if data.email and not is_valid_email_address(data.email):
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail="Invalid email.")

    account = Account(
        id=urlsafe_short_hash(),
        email=data.email,
        username=data.username,
    )
    account.hash_password(data.password)
    await create_account(account)
    return _auth_success_response(account.username)



@auth_router.put("/pubkey")
async def update_pubkey(
    data: UpdateUserPubkey,
    user: User = Depends(check_user_exists),
    payload: AccessTokenPayload = Depends(access_token_payload),
) -> Optional[User]:
    if data.user_id != user.id:
        raise HTTPException(HTTP_400_BAD_REQUEST, "Invalid user ID.")

    try:
        data.pubkey = normalize_public_key(data.pubkey)
        return await update_user_pubkey(data, payload.auth_time or 0)

    except AssertionError as exc:
        raise HTTPException(HTTP_403_FORBIDDEN, str(exc)) from exc
    except Exception as exc:
        logger.debug(exc)


@auth_router.put("/password")
async def update_password(
    data: UpdateUserPassword,
    user: User = Depends(check_user_exists),
    payload: AccessTokenPayload = Depends(access_token_payload),
) -> Optional[User]:
    if data.user_id != user.id:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="Invalid user ID."
        )
    if data.username and await get_account_by_username(data.username):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="Username already exists."
        )

    account = await get_account(user.id)
    if not account:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Account not found."
        )

    # old accounts do not have a password
    if account.password_hash:
        if not data.password_old:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST, detail="Missing old password."
            )
        if not account.verify_password(data.password_old):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST, detail="Invalid credentials."
            )

    account.username = data.username
    account.hash_password(data.password)
    await update_account(account)
    _user = await get_user(account)
    if not _user:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="User not found.")
    return _user


@auth_router.put("/reset")
async def reset_password(data: ResetUserPassword) -> JSONResponse:
    if not settings.is_auth_method_allowed(AuthMethods.username_and_password):
        raise HTTPException(
            HTTPStatus.UNAUTHORIZED, "Auth by 'Username and Password' not allowed."
        )

    assert data.reset_key[:10] == "reset_key_", "This is not a reset key."

    reset_data_json = decrypt_internal_message(
        base64.b64decode(data.reset_key[10:]).decode()
    )
    assert reset_data_json, "Cannot process reset key."

    action, user_id, request_time = json.loads(reset_data_json)
    assert action == "reset", "Expected reset action."
    assert user_id is not None, "Missing user ID."
    assert request_time is not None, "Missing reset time."

    user = await get_account(user_id)
    assert user, "User not found."

    update_pwd = UpdateUserPassword(
        user_id=user.id,
        username=user.username or "",
        password=data.password,
        password_repeat=data.password_repeat,
    )
    user = await update_user_password(update_pwd, request_time)

    return _auth_success_response(
        username=user.username, user_id=user_id, email=user.email
    )


@auth_router.put("/update")
async def update(
    data: UpdateUser, user: User = Depends(check_user_exists)
) -> Optional[User]:
    if data.user_id != user.id:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="Invalid user ID."
        )
    if data.username and not is_valid_username(data.username):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="Invalid username."
        )
    if data.email != user.email:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Email mismatch.",
        )
    account = await get_account(user.id)
    if not account:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Account not found."
        )
    if data.username and await get_account_by_username(data.username):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="Username already exists."
        )
    if data.email and await get_account_by_email(data.email):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="Email already exists."
        )

    account = await get_account(user.id)
    if not account:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Account not found."
        )

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
    return _auth_success_response(username=account.username)


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
        if not settings.new_accounts_allowed:
            raise HTTPException(HTTPStatus.BAD_REQUEST, "Account creation is disabled.")
        account = Account(
            id=urlsafe_short_hash(), email=email, extra=UserExtra(email_verified=True)
        )
        await create_account(account)
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
    assert auth_header, "Nostr Auth header missing."

    scheme, token = auth_header.split()
    assert scheme.lower() == "nostr", "Authorization header is not nostr."

    event = None
    try:
        event_json = base64.b64decode(token.encode("ascii"))
        event = json.loads(event_json)
    except Exception as exc:
        logger.warning(exc)

    assert event, "Nostr login event cannot be parsed."

    assert verify_event(event), "Nostr login event is not valid."

    assert event["kind"] == 27_235, "Invalid event kind."
    auth_threshold = settings.auth_credetials_update_threshold
    assert (
        abs(time() - event["created_at"]) < auth_threshold
    ), f"More than {auth_threshold} seconds have passed since the event was signed."

    method: Optional[str] = next((v for k, v in event["tags"] if k == "method"), None)
    assert method, "Tag 'method' is missing."
    assert method.upper() == "POST", "Incorrect value for tag 'method'."

    url = next((v for k, v in event["tags"] if k == "u"), None)
    assert url, "Tag 'u' for URL is missing."
    accepted_urls = [f"{u}/nostr" for u in settings.nostr_absolute_request_urls]
    assert url in accepted_urls, f"Incorrect value for tag 'u': '{url}'."

    return event
