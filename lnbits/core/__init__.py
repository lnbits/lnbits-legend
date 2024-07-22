from fastapi import APIRouter, FastAPI

from .db import core_app_extra, db
from .views.admin_api import admin_router
from .views.api import api_router
from .views.auth_api import auth_router
from .views.extension_api import extension_router

# this compat is needed for usermanager extension
from .views.generic import generic_router
from .views.lnurl_api import lnurl_router
from .views.node_api import node_router, public_node_router, super_node_router
from .views.payment_api import payment_router
from .views.public_api import public_router
from .views.tinyurl_api import tinyurl_router
from .views.user_api import users_router
from .views.wallet_api import wallet_router
from .views.webpush_api import webpush_router
from .views.websocket_api import websocket_router

# backwards compatibility for extensions
core_app = APIRouter(tags=["Core"])


def init_core_routers(app: FastAPI):
    app.include_router(core_app)
    app.include_router(generic_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(node_router)
    app.include_router(extension_router)
    app.include_router(super_node_router)
    app.include_router(public_node_router)
    app.include_router(public_router)
    app.include_router(payment_router)
    app.include_router(wallet_router)
    app.include_router(api_router)
    app.include_router(websocket_router)
    app.include_router(tinyurl_router)
    app.include_router(webpush_router)
    app.include_router(users_router)
    app.include_router(lnurl_router)
