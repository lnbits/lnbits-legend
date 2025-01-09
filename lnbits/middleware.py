import asyncio
import json
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any, Optional, Union

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from loguru import logger
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from lnbits.core.db import core_app_extra
from lnbits.core.models import AuditEntry
from lnbits.helpers import template_renderer
from lnbits.settings import settings


class InstalledExtensionMiddleware:
    # This middleware class intercepts calls made to the extensions API and:
    #  - it blocks the calls if the extension has been disabled or uninstalled.
    #  - it redirects the calls to the latest version of the extension
    #    if the extension has been upgraded.
    #  - otherwise it has no effect
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        full_path = scope.get("path", "/")
        if full_path == "/":
            await self.app(scope, receive, send)
            return

        top_path, *rest = (p for p in full_path.split("/") if p)
        headers = scope.get("headers", [])

        # block path for all users if the extension is disabled
        if top_path in settings.lnbits_deactivated_extensions:
            response = self._response_by_accepted_type(
                scope, headers, f"Extension '{top_path}' disabled", HTTPStatus.NOT_FOUND
            )
            await response(scope, receive, send)
            return

        # static resources do not require redirect
        if rest[0:1] == ["static"]:
            await self.app(scope, receive, send)
            return

        # re-route all trafic if the extension has been upgraded
        if top_path in settings.lnbits_upgraded_extensions:
            upgrade_path = (
                f"""{settings.lnbits_upgraded_extensions[top_path]}/{top_path}"""
            )
            tail = "/".join(rest)
            scope["path"] = f"/upgrades/{upgrade_path}/{tail}"

        await self.app(scope, receive, send)

    def _response_by_accepted_type(
        self, scope: Scope, headers: list[Any], msg: str, status_code: HTTPStatus
    ) -> Union[HTMLResponse, JSONResponse]:
        """
        Build an HTTP response containing the `msg` as HTTP body and the `status_code`
        as HTTP code. If the `accept` HTTP header is present int the request and
        contains the value of `text/html` then return an `HTMLResponse`,
        otherwise return an `JSONResponse`.
        """
        accept_header: str = next(
            (
                h[1].decode("UTF-8")
                for h in headers
                if len(h) >= 2 and h[0].decode("UTF-8") == "accept"
            ),
            "",
        )

        if "text/html" in accept_header.split(","):
            return HTMLResponse(
                status_code=status_code,
                content=template_renderer()
                .TemplateResponse(Request(scope), "error.html", {"err": msg})
                .body,
            )

        return JSONResponse(
            status_code=status_code,
            content={"detail": msg},
        )


class ExtensionsRedirectMiddleware:
    # Extensions are allowed to specify redirect paths. A call to a path outside the
    # scope of the extension can be redirected to one of the extension's endpoints.
    # Eg: redirect `GET /.well-known` to `GET /lnurlp/api/v1/well-known`

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if "path" not in scope:
            await self.app(scope, receive, send)
            return

        req_headers = scope["headers"] if "headers" in scope else []
        redirect = settings.find_extension_redirect(scope["path"], req_headers)
        if redirect:
            scope["path"] = redirect.new_path_from(scope["path"])

        await self.app(scope, receive, send)


class AuditMiddleware(BaseHTTPMiddleware):

    def __init__(self, app: ASGIApp, audit_queue: asyncio.Queue) -> None:
        super().__init__(app)
        self.audit_queue = audit_queue
        # delete_time purge after X days
        # time, # include pats, exclude paths (regex)

    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = datetime.now(timezone.utc)
        request_details = await self._request_details(request)
        response: Optional[Response] = None

        try:
            response = await call_next(request)
            assert response
            return response
        finally:
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            await self._log_audit(request, response, duration, request_details)

    async def _log_audit(
        self,
        request: Request,
        response: Optional[Response],
        duration: float,
        request_details: Optional[str],
    ):
        try:
            http_method = request.scope.get("method", None)
            path: Optional[str] = getattr(request.scope.get("route", {}), "path", None)
            response_code = str(response.status_code) if response else None
            if not settings.audit_http_request(http_method, path, response_code):
                return None
            ip_address = (
                request.client.host
                if settings.lnbits_audit_log_ip_address and request.client
                else None
            )
            user_id = request.scope.get("user_id", None)
            if settings.is_super_user(user_id):
                user_id = "super_user"
            component = "core"
            if path and not path.startswith("/api"):
                component = path.split("/")[1]

            data = AuditEntry(
                component=component,
                ip_address=ip_address,
                user_id=user_id,
                path=path,
                request_type=request.scope.get("type", None),
                request_method=http_method,
                request_details=request_details,
                response_code=response_code,
                duration=duration,
            )
            await self.audit_queue.put(data)
        except Exception as ex:
            logger.warning(ex)

    async def _request_details(self, request: Request) -> Optional[str]:
        if not settings.audit_http_request_details():
            return None

        try:
            http_method = request.scope.get("method", None)
            path = request.scope.get("path", None)

            if not settings.audit_http_request(http_method, path):
                return None

            details: dict = {}
            if settings.lnbits_audit_log_path_params:
                details["path_params"] = request.path_params
            if settings.lnbits_audit_log_query_params:
                details["query_params"] = dict(request.query_params)
            if settings.lnbits_audit_log_request_body:
                _body = await request.body()
                details["body"] = _body.decode("utf-8")
            details_str = json.dumps(details)
            # Make sure the super_user id is not leaked
            return details_str.replace(settings.super_user, "super_user")
        except Exception as e:
            logger.warning(e)
        return None


def add_ratelimit_middleware(app: FastAPI):
    core_app_extra.register_new_ratelimiter()
    # latest https://slowapi.readthedocs.io/en/latest/
    # shows this as a valid way to add the handler
    app.add_exception_handler(
        RateLimitExceeded,
        _rate_limit_exceeded_handler,  # type: ignore
    )
    app.add_middleware(SlowAPIMiddleware)


def add_ip_block_middleware(app: FastAPI):
    @app.middleware("http")
    async def block_allow_ip_middleware(request: Request, call_next):
        if not request.client:
            return JSONResponse(
                status_code=HTTPStatus.FORBIDDEN,
                content={"detail": "No request client"},
            )
        if (
            request.client.host in settings.lnbits_blocked_ips
            and request.client.host not in settings.lnbits_allowed_ips
        ):
            return JSONResponse(
                status_code=HTTPStatus.FORBIDDEN,
                content={"detail": "IP is blocked"},
            )
        return await call_next(request)


def add_first_install_middleware(app: FastAPI):
    @app.middleware("http")
    async def first_install_middleware(request: Request, call_next):
        if (
            settings.first_install
            and request.url.path != "/api/v1/auth/first_install"
            and request.url.path != "/first_install"
            and not request.url.path.startswith("/static")
        ):
            return RedirectResponse("/first_install")
        return await call_next(request)
