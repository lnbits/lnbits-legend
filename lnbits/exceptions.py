import sys
import traceback
from http import HTTPStatus
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from loguru import logger

from .helpers import template_renderer


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
    if (
        request.headers
        and "accept" in request.headers
        and "text/html" in request.headers["accept"]
    ):
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
            request, "error.html", {"err": f"Error: {exc!s}"}, status_code
        )

    return None


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
        logger.warning(f"AssertionError: {exc!s}")
        return render_html_error(request, exc) or JSONResponse(
            status_code=HTTPStatus.BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
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
