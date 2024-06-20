import json
from http import HTTPStatus
from urllib.parse import urlencode

from fastapi.routing import APIRouter
from loguru import logger


class HTTPInternalCall:

    def __init__(self, routers: APIRouter):
        self._routers = routers
        self._response: dict = {}

    async def __call__(self, request_json: str) -> dict:
        try:
            request = json.loads(request_json)
            scope = self._normalize_request(request)
            await self._routers(scope, self._receive, self._send)
            print("### self._response", self._response)
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
