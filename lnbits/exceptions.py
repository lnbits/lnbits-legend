import sys
import traceback
from http import HTTPStatus
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from loguru import logger

from lnbits.settings import settings

from .helpers import path_segments, template_renderer


class PaymentError(Exception):
    def __init__(self, message: str, status: str = "pending"):
        self.message = message
        self.status = status


class InvoiceError(Exception):
    def __init__(self, message: str, status: str = "pending"):
        self.message = message
        self.status = status


def render_html_error(request: Request, exc: Exception) -> Optional[Response]:
    # Only the browser sends "text/html" request
    # not fail proof, but everything else get's a JSON response

    if not request.headers:
        return None

    if not _is_browser_request(request):
        return None

    if (
        isinstance(exc, HTTPException)
        and exc.headers
        and "token-expired" in exc.headers
    ):
        response = RedirectResponse("/")
        response.delete_cookie("cookie_access_token")
        response.delete_cookie("is_lnbits_user_authorized")
        response.set_cookie("is_access_token_expired", "true")
        return response

    status_code: int = (
        exc.status_code
        if isinstance(exc, HTTPException)
        else HTTPStatus.INTERNAL_SERVER_ERROR
    )

    return template_renderer().TemplateResponse(
        request,
        "error.html",
        {
            "err": f"Error: {exc!s}",
            "status_code": int(status_code),
            "message": str(exc).split(":")[-1].strip(),
        },
        status_code,
    )


def register_exception_handlers(app: FastAPI):
    """Register exception handlers for the FastAPI app"""

    @app.exception_handler(Exception)
    async def exception_handler(request: Request, exc: Exception):
        etype, _, tb = sys.exc_info()
        traceback.print_exception(etype, exc, tb)
        logger.error(f"Exception: {exc!s}")
        return render_html_error(request, exc) or JSONResponse(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            content={"detail": str(exc)},
        )

    @app.exception_handler(AssertionError)
    async def assert_error_handler(request: Request, exc: AssertionError):
        etype, _, tb = sys.exc_info()
        traceback.print_exception(etype, exc, tb)
        logger.warning(f"AssertionError: {exc!s}")
        return render_html_error(request, exc) or JSONResponse(
            status_code=HTTPStatus.BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        etype, _, tb = sys.exc_info()
        traceback.print_exception(etype, exc, tb)
        logger.warning(f"ValueError: {exc!s}")
        return render_html_error(request, exc) or JSONResponse(
            status_code=HTTPStatus.BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ):
        logger.error(f"RequestValidationError: {exc!s}")
        return render_html_error(request, exc) or JSONResponse(
            status_code=HTTPStatus.BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        logger.error(f"HTTPException {exc.status_code}: {exc.detail}")
        return render_html_error(request, exc) or JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    @app.exception_handler(PaymentError)
    async def payment_error_handler(request: Request, exc: PaymentError):
        logger.error(f"{exc.message}, {exc.status}")
        return JSONResponse(
            status_code=520,
            content={"detail": exc.message, "status": exc.status},
        )

    @app.exception_handler(InvoiceError)
    async def invoice_error_handler(request: Request, exc: InvoiceError):
        logger.error(f"{exc.message}, Status: {exc.status}")
        return JSONResponse(
            status_code=520,
            content={"detail": exc.message, "status": exc.status},
        )

    @app.exception_handler(404)
    async def error_handler_404(request: Request, exc: HTTPException):
        logger.error(f"404: {request.url.path} {exc.status_code}: {exc.detail}")

        if not _is_browser_request(request):
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
            )

        path = path_segments(request.url.path)[0]
        status_code = HTTPStatus.NOT_FOUND
        message: str = "Page not found."

        if path in settings.lnbits_all_extensions_ids:
            status_code = HTTPStatus.FORBIDDEN
            message = f"Extension '{path}' not installed. Ask the admin to install it."

        return template_renderer().TemplateResponse(
            request,
            "error.html",
            {"status_code": int(status_code), "message": message},
            int(status_code),
        )


def _is_browser_request(request: Request) -> bool:
    # Check a few common browser agents, also not fail proof
    if "api/v1" in request.url.path:
        return False

    browser_agents = ["Mozilla", "Chrome", "Safari"]
    if any(agent in request.headers.get("user-agent", "") for agent in browser_agents):
        return True

    if "text/html" in request.headers.get("accept", ""):
        return True

    return False
