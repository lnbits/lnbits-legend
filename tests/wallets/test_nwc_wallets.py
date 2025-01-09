import base64
import hashlib
import json
import time
from typing import cast

import pytest
import secp256k1
from Cryptodome import Random
from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import pad, unpad
from websockets.server import serve as ws_serve

from lnbits.wallets.nwc import NWCWallet
from tests.wallets.helpers import (
    WalletTest,
    build_test_id,
    check_assertions,
    load_funding_source,
    wallet_fixtures_from_json,
)


def encrypt_content(priv_key, dest_pub_key, content):
    p = secp256k1.PublicKey(bytes.fromhex("02" + dest_pub_key), True)
    shared = p.tweak_mul(bytes.fromhex(priv_key)).serialize()[1:]
    iv = Random.new().read(AES.block_size)
    aes = AES.new(shared, AES.MODE_CBC, iv)

    content_bytes = content.encode("utf-8")
    content_bytes = pad(content_bytes, AES.block_size)

    encrypted_b64 = base64.b64encode(aes.encrypt(content_bytes)).decode("ascii")
    iv_b64 = base64.b64encode(iv).decode("ascii")
    encrypted_content = encrypted_b64 + "?iv=" + iv_b64
    return encrypted_content


def decrypt_content(priv_key, source_pub_key, content):
    p = secp256k1.PublicKey(bytes.fromhex("02" + source_pub_key), True)
    shared = p.tweak_mul(bytes.fromhex(priv_key)).serialize()[1:]
    (encrypted_content_b64, iv_b64) = content.split("?iv=")
    encrypted_content = base64.b64decode(encrypted_content_b64.encode("ascii"))
    iv = base64.b64decode(iv_b64.encode("ascii"))
    aes = AES.new(shared, AES.MODE_CBC, iv)
    decrypted_bytes = aes.decrypt(encrypted_content)
    decrypted_bytes = unpad(decrypted_bytes, AES.block_size)
    return decrypted_bytes.decode("utf-8")


def json_dumps(data):
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if v is not None}
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def sign_event(pub_key, priv_key, event):
    signature_data = json_dumps(
        [
            0,
            pub_key,
            event["created_at"],
            event["kind"],
            event["tags"],
            event["content"],
        ]
    )
    event_id = hashlib.sha256(signature_data.encode()).hexdigest()
    event["id"] = event_id
    event["pubkey"] = pub_key
    s = secp256k1.PrivateKey(bytes.fromhex(priv_key))
    signature = (s.schnorr_sign(bytes.fromhex(event_id), None, raw=True)).hex()
    event["sig"] = signature
    return event


async def handle(wallet, mock_settings, data, websocket, path):
    async for message in websocket:
        if not wallet:
            continue
        msg = json.loads(message)
        if msg[0] == "REQ":
            sub_id = msg[1]
            sub_filter = msg[2]
            kinds = sub_filter["kinds"]
            if 13194 in kinds:  # Send info event
                event = {
                    "kind": 13194,
                    "content": " ".join(mock_settings["supported_methods"]),
                    "created_at": int(time.time()),
                    "tags": [],
                }
                sign_event(
                    mock_settings["service_public_key"],
                    mock_settings["service_private_key"],
                    event,
                )
                await websocket.send(json.dumps(["EVENT", sub_id, event]))
        elif msg[0] == "EVENT":
            event = msg[1]
            decrypted_content = decrypt_content(
                mock_settings["service_private_key"],
                mock_settings["user_public_key"],
                event["content"],
            )
            content = json.loads(decrypted_content)
            mock = None
            for m in data.mocks:
                rb = m.request_body
                if rb and rb["method"] == content["method"]:
                    p1 = rb["params"]
                    p2 = content["params"]
                    p1 = json_dumps({k: v for k, v in p1.items() if v is not None})
                    p2 = json_dumps({k: v for k, v in p2.items() if v is not None})
                    if p1 == p2:
                        mock = m
                        break
            if mock:
                sub_id = None
                nwcwallet = cast(NWCWallet, wallet)
                for subscription in nwcwallet.conn.subscriptions.values():
                    if subscription["event_id"] == event["id"]:
                        sub_id = subscription["sub_id"]
                        break
                if sub_id:
                    response = mock.response
                    encrypted_content = encrypt_content(
                        mock_settings["service_private_key"],
                        mock_settings["user_public_key"],
                        json_dumps(response),
                    )
                    response_event = {
                        "kind": 23195,
                        "content": encrypted_content,
                        "created_at": int(time.time()),
                        "tags": [
                            ["e", event["id"]],
                            ["p", mock_settings["user_public_key"]],
                        ],
                    }
                    sign_event(
                        mock_settings["service_public_key"],
                        mock_settings["service_private_key"],
                        response_event,
                    )
                    await websocket.send(json.dumps(["EVENT", sub_id, response_event]))
            else:
                raise Exception(
                    "No mock found for "
                    + content["method"]
                    + " "
                    + json_dumps(content["params"])
                )


async def run(data: WalletTest):
    if data.skip:
        pytest.skip()

    wallet = None
    mock_settings = data.funding_source.mock_settings
    if mock_settings is None:
        return

    async def handler(websocket, path):
        return await handle(wallet, mock_settings, data, websocket, path)

    if mock_settings is not None:
        async with ws_serve(handler, "localhost", mock_settings["port"]) as server:
            await server.start_serving()
            wallet = load_funding_source(data.funding_source)
            await check_assertions(wallet, data)
            nwcwallet = cast(NWCWallet, wallet)
            await nwcwallet.cleanup()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "test_data",
    wallet_fixtures_from_json("tests/wallets/fixtures/json/fixtures_nwc.json"),
    ids=build_test_id,
)
async def test_nwc_wallet(test_data: WalletTest):
    await run(test_data)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "test_data",
    wallet_fixtures_from_json("tests/wallets/fixtures/json/fixtures_nwc_bad.json"),
    ids=build_test_id,
)
async def test_nwc_wallet_bad(test_data: WalletTest):
    await run(test_data)
