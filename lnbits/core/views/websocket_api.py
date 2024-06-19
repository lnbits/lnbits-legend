from http import HTTPStatus
from urllib.parse import urlencode

from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
)

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


def enable_ws_tunnel_for_routers(routers: APIRouter):
    @routers.websocket("/api/v1/tunnel/{item_id}")
    async def websocket_tunnel(websocket: WebSocket, item_id: str):
        await websocket_manager.connect(websocket, item_id)
        try:
            while settings.lnbits_running:

                await websocket.receive_text()

                ws_call = WebsocketCall(routers)
                resp = await ws_call(
                    {
                        "method": "GET",
                        "path": "/api/v1/wallet",
                        "headers": {"x-api-key": item_id, "x": None},
                    }
                )
                #  b"65f14a3501624bb09279744b1865bffe"

                print("#### ws_call.response", resp)

        except WebSocketDisconnect:
            websocket_manager.disconnect(websocket)


class WebsocketCall:

    def __init__(self, routers: APIRouter):
        self._routers = routers
        self._response: dict = {}

    async def __call__(self, scope: dict) -> dict:
        try:
            scope = self._normalize_request(scope)
            await self._routers(scope, self._receive, self._send)
            return self._normalize_response(self._response)
        except Exception as ex:
            return {"status": int(HTTPStatus.INTERNAL_SERVER_ERROR), "detail": str(ex)}

    def _normalize_request(self, req: dict) -> dict:
        scope = {"type": "http"}
        scope["headers"] = (
            [
                (k.encode("utf-8"), v and v.encode("utf-8"))
                for k, v in req["headers"].items()
            ]
            if "headers" in req
            else None
        )
        scope["query_string"] = urlencode(req["params"]) if "params" in req else None

        return {**req, **scope}

    def _normalize_response(self, resp: dict) -> dict:
        response = {"status": resp["status"] if "status" in resp else 502}
        if resp.get("headers"):
            response["headers"] = {}
            for header in resp["headers"]:
                key = header[0].decode("utf-8") if header[0] else None
                value = header[1].decode("utf-8") if header[1] else None
                response["headers"][key] = value

        if "body" in resp:
            response["body"] = resp["body"].decode("utf-8") if resp["body"] else None

        return response

    # todo: fix typing
    async def _receive(self, message):
        return message

    # todo: fix typing
    async def _send(self, message):
        self._response = {**self._response, **message}
        return message
