import json
from http import HTTPStatus
from typing import Any, Mapping, Optional
from urllib.parse import urlencode

from fastapi import HTTPException, WebSocket
from fastapi.routing import APIRouter
from loguru import logger


class HTTPTunnelClient:

    def __init__(self, websocket: WebSocket):
        self.ws = websocket

    async def request(
        self,
        method: str,
        url: str,
        *,
        data: Optional[dict] = None,
        json: Optional[dict] = None,
        params: Optional[Mapping[str, str]] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> "HTTPTunnelResponse":
        pass

    async def get(
        self,
        url: str,
        *,
        params: Optional[Mapping[str, str]] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> "HTTPTunnelResponse":
        return await self.request(
            "GET",
            url,
            params=params,
            headers=headers,
            timeout=timeout,
        )

    async def options(
        self,
        url: str,
        *,
        params: Optional[Mapping[str, str]] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> "HTTPTunnelResponse":
        return await self.request(
            "OPTIONS",
            url,
            params=params,
            headers=headers,
            timeout=timeout,
        )

    async def head(
        self,
        url: str,
        *,
        params: Optional[Mapping[str, str]] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> "HTTPTunnelResponse":
        return await self.request(
            "HEAD",
            url,
            params=params,
            headers=headers,
            timeout=timeout,
        )

    async def post(
        self,
        url: str,
        *,
        data: Optional[dict] = None,
        json: Optional[dict] = None,
        params: Optional[Mapping[str, str]] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> "HTTPTunnelResponse":
        return await self.request(
            "POST",
            url,
            data=data,
            json=json,
            params=params,
            headers=headers,
            timeout=timeout,
        )

    async def put(
        self,
        url: str,
        *,
        data: Optional[dict] = None,
        json: Optional[dict] = None,
        params: Optional[Mapping[str, str]] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> "HTTPTunnelResponse":
        return await self.request(
            "PUT",
            url,
            data=data,
            json=json,
            params=params,
            headers=headers,
            timeout=timeout,
        )

    async def patch(
        self,
        url: str,
        *,
        data: Optional[dict] = None,
        json: Optional[dict] = None,
        params: Optional[Mapping[str, str]] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> "HTTPTunnelResponse":
        return await self.request(
            "PATCH",
            url,
            data=data,
            json=json,
            params=params,
            headers=headers,
            timeout=timeout,
        )

    async def delete(
        self,
        url: str,
        *,
        params: Optional[Mapping[str, str]] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> "HTTPTunnelResponse":
        return await self.request(
            "DELETE",
            url,
            params=params,
            headers=headers,
            timeout=timeout,
        )

    async def aclose(self) -> None:
        pass


class HTTPTunnelResponse:

    def __init__(self):
        pass

    @property
    def is_error(self) -> bool:
        pass

    @property
    def text(self) -> str:
        pass

    def raise_for_status(self) -> "HTTPTunnelResponse":
        pass

    def json(self, **kwargs: Any) -> Any:
        pass


class HTTPInternalCall:

    def __init__(self, routers: APIRouter):
        self._routers = routers
        self._response: dict = {}
        self._body: Optional[str] = None

    async def __call__(self, request_json: str) -> dict:
        try:
            request = json.loads(request_json)
            scope = self._normalize_request(request)
            await self._routers(scope, self._receive, self._send)
            return self._normalize_response(self._response)
        except HTTPException as exc:
            return {"status": int(exc.status_code), "detail": exc.detail}
        except Exception as exc:
            logger.warning(exc)
            return {"status": int(HTTPStatus.INTERNAL_SERVER_ERROR), "detail": str(exc)}

    def _normalize_request(self, reqest: dict) -> dict:
        _req = {"type": "http"}
        _req["headers"] = (
            [
                (k and k.encode("utf-8"), v and v.encode("utf-8"))
                for k, v in reqest["headers"].items()
            ]
            if "headers" in reqest
            else None
        )
        _req["query_string"] = (
            urlencode(reqest["params"]) if "params" in reqest else None
        )

        self._body = reqest["body"] if "body" in reqest else None

        return {**reqest, **_req}

    def _normalize_response(self, response: dict) -> dict:
        _resp = {"status": response.get("status", int(HTTPStatus.BAD_GATEWAY))}
        if response.get("headers"):
            _resp["headers"] = {}
            for header in response["headers"]:
                key = header[0].decode("utf-8") if header[0] else None
                value = header[1].decode("utf-8") if header[1] else None
                _resp["headers"][key] = value

        if "body" in response:
            _resp["body"] = (
                response["body"].decode("utf-8") if response["body"] else None
            )

        return _resp

    # todo: fix typing
    async def _receive(self):
        if not self._body:
            return None
        return {
            "type": "http.request",
            "body": self._body.encode("utf-8"),
            "more_body": False,
        }

    # todo: fix typing
    async def _send(self, message):
        self._response = {**self._response, **message}
        return message
