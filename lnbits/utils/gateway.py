import uuid
from http import HTTPStatus
from json import dumps, loads
from typing import Any, Awaitable, Mapping, Optional
from urllib.parse import urlencode

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from fastapi.routing import APIRouter
from loguru import logger

from lnbits.settings import settings


class HTTPTunnelClient:

    # todo: add typings
    def __init__(
        self,
        send_fn: Optional[Awaitable] = None,
        receive_fn: Optional[Awaitable] = None,
    ):
        self._send_fn = send_fn
        self._receive_fn = receive_fn

    async def connect(self, send_fn: Awaitable, receive_fn: Awaitable):
        self._send_fn = send_fn
        self._receive_fn = receive_fn

        while settings.lnbits_running:
            req = await self._receive_fn()
            print("### req", req)

    def disconnect(self):
        self._send_fn = None
        self._receive_fn = None

    @property
    def connected(self) -> bool:
        return self._send_fn and self._receive_fn

    async def request(
        self,
        method: str,
        url: str,
        *,
        data: Optional[str] = None,
        json: Optional[dict] = None,
        params: Optional[Mapping[str, str]] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> "HTTPTunnelResponse":
        try:
            assert self.connected, "Tunnel connection not established."
            request_id = uuid.uuid4().hex
            body = data
            if json:
                body = dumps(json)
            await self._send_fn(
                {
                    "request_id": request_id,
                    "method": method,
                    "url": url,
                    "body": body,
                    "params": params,
                    "headers": headers,
                }
            )
        except Exception as exc:
            logger.warning(exc)

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
        print("### todo close fn")


class HTTPTunnelResponse:

    # status code, detail
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
            request = loads(request_json)
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


# todo: extrct models?
http_tunnel_client = HTTPTunnelClient()


async def websocket_tunnel(websocket: WebSocket):
    try:
        await websocket.accept()

        async def _send_fn(resp):
            await websocket.send_json(resp)

        async def _receive_fn():
            return await websocket.receive_json()

        await http_tunnel_client.connect(_send_fn, _receive_fn)

    except WebSocketDisconnect as exc:
        logger.warning(exc)
    except Exception as exc:
        logger.warning(exc)
        raise exc
