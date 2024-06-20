import json

from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
)
from loguru import logger

from lnbits.settings import settings
from lnbits.utils.gateway import HTTPInternalCall, websocket_tunnel

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
    @routers.websocket("/api/v1/tunnel")
    async def websocket_temp(websocket: WebSocket):
        try:
            await websocket.accept()

            while settings.lnbits_running:
                req = await websocket.receive_text()

                resp = await HTTPInternalCall(routers)(req)

                await websocket.send_text(json.dumps(resp))
        except WebSocketDisconnect as exc:
            logger.warning(exc)

    @routers.websocket("/api/v1/feeder")
    async def websocket_feeder(websocket: WebSocket):
        await websocket_tunnel(websocket)
