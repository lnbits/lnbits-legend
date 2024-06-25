import asyncio
import uuid
from asyncio import Queue, TimeoutError
from http import HTTPStatus
from json import dumps, loads
from typing import Any, Awaitable, Dict, Mapping, Optional
from urllib.parse import urlencode

import httpx
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

        self._req_resp: Dict[str, Queue] = {}

    @property
    def connected(self) -> bool:
        return self._send_fn is not None and self._receive_fn is not None

    async def connect(self, send_fn: Awaitable, receive_fn: Awaitable):
        self._send_fn = send_fn
        self._receive_fn = receive_fn

        while settings.lnbits_running and self.connected:
            resp = await self._receive_fn()
            print("### receive resp", resp)

            await self._handle_response(resp)

    def disconnect(self):
        self._send_fn = None
        self._receive_fn = None

    async def request(
        self,
        method: str,
        url: str,
        *,
        data: Optional[str] = None,
        json: Optional[dict] = None,
        params: Optional[Mapping[str, str]] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[int],
    ) -> "HTTPTunnelResponse":
        request_id = uuid.uuid4().hex
        try:
            assert self.connected, "Tunnel connection not established."

            self._req_resp[request_id] = Queue()
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
            resp = await asyncio.wait_for(
                self._req_resp[request_id].get(), timeout or 30
            )
            print("### req-resp", resp)

            del self._req_resp[request_id]
        except TimeoutError as exc:
            logger.warning(exc)
            return HTTPTunnelResponse({"status": int(HTTPStatus.REQUEST_TIMEOUT)})
        except Exception as exc:
            logger.warning(exc)
            return HTTPTunnelResponse(
                {"status": int(HTTPStatus.INTERNAL_SERVER_ERROR), "detail": str(exc)}
            )
        finally:
            del self._req_resp[request_id]

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
        self.disconnect()

    async def _handle_response(self, resp: Optional[dict]):
        if not resp:
            return
        request_id = resp.get("request_id")
        if request_id:
            await self._handle_request_id(resp, request_id)
        else:
            self._handle_streaming()

    async def _handle_request_id(self, resp, request_id):
        awaiting_req = self._req_resp.get(request_id)
        if awaiting_req:
            await awaiting_req.put(resp)
        else:
            logger.warning(f"Unknown request id: '{request_id}'. Possible timeout!")

    def _handle_streaming(self):
        print("### handle streaming here")


class HTTPTunnelResponse:

    # status code, detail
    def __init__(self, resp: Optional[dict]):
        self._resp = resp

    @property
    def is_error(self) -> bool:
        status = self._resp.get("status", 500)
        return 400 <= status <= 599

    @property
    def is_success(self) -> bool:
        status = int(self._resp.get("status", 500))
        return 200 <= status <= 299

    @property
    def text(self) -> str:
        if not self._resp or "body" not in self._resp:
            return ""
        return self._resp["body"]

    def raise_for_status(self) -> "HTTPTunnelResponse":
        if self._resp is None:
            raise RuntimeError(
                "Cannot call `raise_for_status` as the response "
                "instance has not been set on this response."
            )
        print("### self.is_success", self.is_success, self._resp)
        if self.is_success:
            return self

        # todo add request, test flow
        raise httpx.HTTPStatusError(self.text, request=None, response=self)

    def json(self, **kwargs: Any) -> Any:
        body = self.text
        return loads(body, **kwargs) if body else None


class HTTPInternalCall:

    def __init__(self, routers: APIRouter):
        self._request_id: Optional[str] = None
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
            return {
                "request_id": self._request_id,
                "status": int(exc.status_code),
                "detail": exc.detail,
            }
        except Exception as exc:
            logger.warning(exc)
            return {
                "request_id": self._request_id,
                "status": int(HTTPStatus.INTERNAL_SERVER_ERROR),
                "detail": str(exc),
            }

    def _normalize_request(self, reqest: dict) -> dict:
        _req = {"type": "http"}
        _req["headers"] = (
            [
                (k and k.encode("utf-8"), v and v.encode("utf-8"))
                for k, v in reqest["headers"].items()
            ]
            if reqest.get("headers")
            else []
        )
        _req["query_string"] = (
            urlencode(reqest["params"]) if reqest.get("params") else None
        )

        # todo: normalize if domaine present
        _req["path"] = reqest["url"] if reqest.get("url") else None

        self._body = reqest["body"] if reqest.get("body") else None
        self._request_id = reqest.get("request_id")

        return {**reqest, **_req}

    def _normalize_response(self, response: dict) -> dict:
        _resp = {
            "request_id": self._request_id,
            "status": response.get("status", int(HTTPStatus.BAD_GATEWAY)),
        }
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
