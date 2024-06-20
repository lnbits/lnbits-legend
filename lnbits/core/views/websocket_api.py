import json
from http import HTTPStatus
from urllib.parse import urlencode

from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
)
from loguru import logger

from lnbits.settings import settings

from ..services import (
    websocket_manager,
    websocket_updater,
)

websocket_router = APIRouter(prefix="/api/v1/ws", tags=["Websocket"])


@websocket_router.websocket("/{item_id}")
async def websocket_connect(websocket: WebSocket, item_id: str):
    await websocket_manager.connect(websocket, item_id)
    try:
        while settings.lnbits_running:
            await websocket.receive_text()
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket)


@websocket_router.post("/{item_id}")
async def websocket_update_post(item_id: str, data: str):
    try:
        await websocket_updater(item_id, data)
        return {"sent": True, "data": data}
    except Exception:
        return {"sent": False, "data": data}


@websocket_router.get("/{item_id}/{data}")
async def websocket_update_get(item_id: str, data: str):
    try:
        await websocket_updater(item_id, data)
        return {"sent": True, "data": data}
    except Exception:
        return {"sent": False, "data": data}


# sample request
# {
#     "method": "GET",
#     "path": "/api/v1/wallet",
#     "headers": {"x-api-key": "65f14a3501624bb09279744b1865bffe"},
# }


def enable_ws_tunnel_for_routers(routers: APIRouter):
    @routers.websocket("/api/v1/tunnel")
    async def websocket_tunnel(websocket: WebSocket):
        try:
            await websocket.accept()

            while settings.lnbits_running:
                req = await websocket.receive_text()

                resp = await HTTPInternalCall(routers)(req)

                await websocket.send_text(json.dumps(resp))
        except WebSocketDisconnect as exc:
            logger.warning(exc)


class HTTPInternalCall:

    def __init__(self, routers: APIRouter):
        self._routers = routers
        self._response: dict = {}

    async def __call__(self, request_json: str) -> dict:
        try:
            request = json.loads(request_json)
            scope = self._normalize_request(request)
            await self._routers(scope, self._receive, self._send)
            return self._normalize_response(self._response)
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
    async def _receive(self, message):
        return message

    # todo: fix typing
    async def _send(self, message):
        self._response = {**self._response, **message}
        return message
