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

                try:
                    ws_call = WebsocketCall(routers)
                    await ws_call(
                        {
                            "type": "http",
                            "method": "GET",
                            "path": "/api/v1/wallet",
                            "query_string": "",  # todo: test value
                            "headers": [(b"x-api-key", item_id.encode("utf-8"))],
                        }
                    )
                    #  b"65f14a3501624bb09279744b1865bffe"

                    print("#### ws_call.response", ws_call.response)
                except Exception as ex:
                    print("### ex2", ex)
        except WebSocketDisconnect:
            websocket_manager.disconnect(websocket)


class WebsocketCall:

    def __init__(self, routers: APIRouter):
        self.routers = routers
        self.response = {}

    async def __call__(self, scope: dict):
        await self.routers(scope, self.receive, self.send)

    # todo: fix typing
    async def receive(self, message):
        print("### receive", message)
        return message

    # todo: fix typing
    async def send(self, message):
        print("### send", message)
        self.response = {**self.response, **message}
        return message
