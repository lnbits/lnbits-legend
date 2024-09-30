from http import HTTPStatus
from typing import Annotated, Literal, Optional, Type, Union

import jwt
from fastapi import Cookie, Depends, Query, Request, Security
from fastapi.exceptions import HTTPException
from fastapi.openapi.models import APIKey, APIKeyIn, SecuritySchemeType
from fastapi.security import APIKeyHeader, APIKeyQuery, OAuth2PasswordBearer
from fastapi.security.base import SecurityBase
from loguru import logger
from pydantic.types import UUID4

from lnbits.core.crud import (
    get_account,
    get_account_by_email,
    get_account_by_username,
    get_user,
    get_user_active_extensions_ids,
    get_wallet_for_key,
)
from lnbits.core.models import (
    AccessTokenPayload,
    Account,
    KeyType,
    SimpleStatus,
    User,
    WalletTypeInfo,
)
from lnbits.db import Filter, Filters, TFilterModel
from lnbits.settings import AuthMethods, settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth", auto_error=False)

api_key_header = APIKeyHeader(
    name="X-API-KEY",
    auto_error=False,
    description="Admin or Invoice key for wallet API's",
)
api_key_query = APIKeyQuery(
    name="api-key",
    auto_error=False,
    description="Admin or Invoice key for wallet API's",
)


class KeyChecker(SecurityBase):
    def __init__(
        self,
        api_key: Optional[str] = None,
        expected_key_type: Optional[KeyType] = None,
    ):
        self.auto_error: bool = True
        self.expected_key_type = expected_key_type
        self._api_key = api_key
        if api_key:
            openapi_model = APIKey(
                **{"in": APIKeyIn.query},
                type=SecuritySchemeType.apiKey,
                name="X-API-KEY",
                description="Wallet API Key - QUERY",
            )
        else:
            openapi_model = APIKey(
                **{"in": APIKeyIn.header},
                type=SecuritySchemeType.apiKey,
                name="X-API-KEY",
                description="Wallet API Key - HEADER",
            )
        self.model: APIKey = openapi_model  # type: ignore

    async def __call__(self, request: Request) -> WalletTypeInfo:

        key_value = (
            self._api_key
            if self._api_key
            else request.headers.get("X-API-KEY") or request.query_params.get("api-key")
        )

        if not key_value:
            raise HTTPException(
                status_code=HTTPStatus.UNAUTHORIZED,
                detail="No Api Key provided.",
            )

        wallet = await get_wallet_for_key(key_value)

        if not wallet:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail="Wallet not found.",
            )

        if self.expected_key_type is KeyType.admin and wallet.adminkey != key_value:
            raise HTTPException(
                status_code=HTTPStatus.UNAUTHORIZED,
                detail="Invalid adminkey.",
            )

        await _check_user_extension_access(wallet.user, request["path"])

        key_type = KeyType.admin if wallet.adminkey == key_value else KeyType.invoice
        return WalletTypeInfo(key_type, wallet)


async def require_admin_key(
    request: Request,
    api_key_header: str = Security(api_key_header),
    api_key_query: str = Security(api_key_query),
) -> WalletTypeInfo:
    check: KeyChecker = KeyChecker(
        api_key=api_key_header or api_key_query,
        expected_key_type=KeyType.admin,
    )
    return await check(request)


async def require_invoice_key(
    request: Request,
    api_key_header: str = Security(api_key_header),
    api_key_query: str = Security(api_key_query),
) -> WalletTypeInfo:
    check: KeyChecker = KeyChecker(
        api_key=api_key_header or api_key_query,
        expected_key_type=KeyType.invoice,
    )
    return await check(request)


async def check_access_token(
    header_access_token: Annotated[Union[str, None], Depends(oauth2_scheme)],
    cookie_access_token: Annotated[Union[str, None], Cookie()] = None,
) -> Optional[str]:
    return header_access_token or cookie_access_token


async def check_user_exists(
    r: Request,
    access_token: Annotated[Optional[str], Depends(check_access_token)],
    usr: Optional[UUID4] = None,
) -> User:
    if access_token:
        account = await _get_account_from_token(access_token)
    elif usr and settings.is_auth_method_allowed(AuthMethods.user_id_only):
        account = await get_account(usr.hex)
    else:
        raise HTTPException(HTTPStatus.UNAUTHORIZED, "Missing user ID or access token.")

    if not account:
        raise HTTPException(HTTPStatus.UNAUTHORIZED, "User not found.")

    if not settings.is_user_allowed(account.id):
        raise HTTPException(HTTPStatus.UNAUTHORIZED, "User not allowed.")

    user = await get_user(account)
    if not user:
        raise HTTPException(HTTPStatus.UNAUTHORIZED, "User not found.")
    await _check_user_extension_access(user.id, r["path"])
    return user


async def optional_user_id(
    access_token: Annotated[Optional[str], Depends(check_access_token)],
    usr: Optional[UUID4] = None,
) -> Optional[str]:
    if usr and settings.is_auth_method_allowed(AuthMethods.user_id_only):
        return usr.hex
    if access_token:
        account = await _get_account_from_token(access_token)
        return account.id if account else None

    return None


async def access_token_payload(
    access_token: Annotated[Optional[str], Depends(check_access_token)],
) -> AccessTokenPayload:
    if not access_token:
        raise HTTPException(HTTPStatus.UNAUTHORIZED, "Missing access token.")

    payload: dict = jwt.decode(access_token, settings.auth_secret_key, ["HS256"])
    return AccessTokenPayload(**payload)


async def check_admin(user: Annotated[User, Depends(check_user_exists)]) -> User:
    if user.id != settings.super_user and user.id not in settings.lnbits_admin_users:
        raise HTTPException(
            HTTPStatus.UNAUTHORIZED, "User not authorized. No admin privileges."
        )

    return user


async def check_super_user(user: Annotated[User, Depends(check_user_exists)]) -> User:
    if user.id != settings.super_user:
        raise HTTPException(
            HTTPStatus.UNAUTHORIZED, "User not authorized. No super user privileges."
        )
    return user


def parse_filters(model: Type[TFilterModel]):
    """
    Parses the query params as filters.
    :param model: model used for validation of filter values
    """

    def dependency(
        request: Request,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        sortby: Optional[str] = None,
        direction: Optional[Literal["asc", "desc"]] = None,
        search: Optional[str] = Query(None, description="Text based search"),
    ):
        params = request.query_params
        filters = []
        for i, key in enumerate(params.keys()):
            try:
                filters.append(Filter.parse_query(key, params.getlist(key), model, i))
            except ValueError:
                continue

        return Filters(
            filters=filters,
            limit=limit,
            offset=offset,
            sortby=sortby,
            direction=direction,
            search=search,
            model=model,
        )

    return dependency


async def check_user_extension_access(user_id: str, ext_id: str) -> SimpleStatus:
    """
    Check if the user has access to a particular extension.
    Raises HTTP Forbidden if the user is not allowed.
    """
    if settings.is_admin_extension(ext_id) and not settings.is_admin_user(user_id):
        return SimpleStatus(
            success=False, message=f"User not authorized for extension '{ext_id}'."
        )

    if settings.is_extension_id(ext_id):
        ext_ids = await get_user_active_extensions_ids(user_id)
        if ext_id not in ext_ids:
            return SimpleStatus(
                success=False, message=f"User extension '{ext_id}' not enabled."
            )

    return SimpleStatus(success=True, message="OK")


async def _check_user_extension_access(user_id: str, current_path: str):
    path = current_path.split("/")
    ext_id = path[3] if path[1] == "upgrades" else path[1]
    status = await check_user_extension_access(user_id, ext_id)
    if not status.success:
        raise HTTPException(
            HTTPStatus.FORBIDDEN,
            status.message,
        )


async def _get_account_from_token(access_token) -> Optional[Account]:
    try:
        payload: dict = jwt.decode(access_token, settings.auth_secret_key, ["HS256"])
        user = await _get_user_from_jwt_payload(payload)
        if not user:
            raise HTTPException(
                HTTPStatus.UNAUTHORIZED, "Data missing for access token."
            )

        return user

    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            HTTPStatus.UNAUTHORIZED, "Session expired.", {"token-expired": "true"}
        ) from exc
    except jwt.PyJWTError as exc:
        logger.debug(exc)
        raise HTTPException(HTTPStatus.UNAUTHORIZED, "Invalid access token.") from exc


async def _get_user_from_jwt_payload(payload) -> Optional[Account]:
    if "sub" in payload and payload.get("sub"):
        return await get_account_by_username(str(payload.get("sub")))
    if "usr" in payload and payload.get("usr"):
        return await get_account(str(payload.get("usr")))
    if "email" in payload and payload.get("email"):
        return await get_account_by_email(str(payload.get("email")))
    return None
