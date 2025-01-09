import base64
import hashlib
import json
from typing import Union

import secp256k1
from bech32 import bech32_decode, bech32_encode, convertbits
from Cryptodome import Random
from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import pad, unpad


def encrypt_content(
    content: str, service_pubkey: secp256k1.PublicKey, account_private_key_hex: str
) -> str:
    """
    Encrypts the content to be sent to the service.

    Args:
        content (str): The content to be encrypted.
        service_pubkey (secp256k1.PublicKey): The service provider's public key.
        account_private_key_hex (str): The account private key in hex format.

    Returns:
        str: The encrypted content.
    """
    shared = service_pubkey.tweak_mul(
        bytes.fromhex(account_private_key_hex)
    ).serialize()[1:]
    # random iv (16B)
    iv = Random.new().read(AES.block_size)
    aes = AES.new(shared, AES.MODE_CBC, iv)

    content_bytes = content.encode("utf-8")

    # padding
    content_bytes = pad(content_bytes, AES.block_size)

    # Encrypt
    encrypted_b64 = base64.b64encode(aes.encrypt(content_bytes)).decode("ascii")
    iv_b64 = base64.b64encode(iv).decode("ascii")
    encrypted_content = encrypted_b64 + "?iv=" + iv_b64
    return encrypted_content


def decrypt_content(
    content: str, service_pubkey: secp256k1.PublicKey, account_private_key_hex: str
) -> str:
    """
    Decrypts the content coming from the service.

    Args:
        content (str): The encrypted content.
        service_pubkey (secp256k1.PublicKey): The service provider's public key.
        account_private_key_hex (str): The account private key in hex format.

    Returns:
        str: The decrypted content.
    """
    shared = service_pubkey.tweak_mul(
        bytes.fromhex(account_private_key_hex)
    ).serialize()[1:]
    # extract iv and content
    (encrypted_content_b64, iv_b64) = content.split("?iv=")
    encrypted_content = base64.b64decode(encrypted_content_b64.encode("ascii"))
    iv = base64.b64decode(iv_b64.encode("ascii"))
    # Decrypt
    aes = AES.new(shared, AES.MODE_CBC, iv)
    decrypted_bytes = aes.decrypt(encrypted_content)
    decrypted_bytes = unpad(decrypted_bytes, AES.block_size)
    decrypted = decrypted_bytes.decode("utf-8")

    return decrypted


def verify_event(event: dict) -> bool:
    """
    Verify the event signature

    Args:
        event (Dict): The event to verify.

    Returns:
        bool: True if the event signature is valid, False otherwise.
    """
    signature_data = json_dumps(
        [
            0,
            event["pubkey"],
            event["created_at"],
            event["kind"],
            event["tags"],
            event["content"],
        ]
    )
    event_id = hashlib.sha256(signature_data.encode()).hexdigest()
    if event_id != event["id"]:
        return False
    pubkey_hex = event["pubkey"]
    pubkey = secp256k1.PublicKey(bytes.fromhex("02" + pubkey_hex), True)
    if not pubkey.schnorr_verify(
        bytes.fromhex(event_id), bytes.fromhex(event["sig"]), None, raw=True
    ):
        return False
    return True


def sign_event(
    event: dict, account_public_key_hex: str, account_private_key: secp256k1.PrivateKey
) -> dict:
    """
    Signs the event (in place) with the service secret

    Args:
        event (Dict): The event to be signed.
        account_public_key_hex (str): The account public key in hex format.
        account_private_key (secp256k1.PrivateKey): The account private key.

    Returns:
        Dict: The input event with the signature added.
    """
    signature_data = json_dumps(
        [
            0,
            account_public_key_hex,
            event["created_at"],
            event["kind"],
            event["tags"],
            event["content"],
        ]
    )
    event_id = hashlib.sha256(signature_data.encode()).hexdigest()
    event["id"] = event_id
    event["pubkey"] = account_public_key_hex

    signature = (
        account_private_key.schnorr_sign(bytes.fromhex(event_id), None, raw=True)
    ).hex()
    event["sig"] = signature
    return event


def json_dumps(data: Union[dict, list]) -> str:
    """
    Converts a Python dictionary to a JSON string with compact encoding.

    Args:
        data (Dict): The dictionary to be converted.

    Returns:
        str: The compact JSON string.
    """
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if v is not None}
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def normalize_public_key(pubkey: str) -> str:
    if pubkey.startswith("npub1"):
        _, decoded_data = bech32_decode(pubkey)
        assert decoded_data, "Public Key is not valid npub."

        decoded_data_bits = convertbits(decoded_data, 5, 8, False)
        assert decoded_data_bits, "Public Key is not valid npub."

        return bytes(decoded_data_bits).hex()

    assert len(pubkey) == 64, "Public key has wrong length."
    try:
        int(pubkey, 16)
    except Exception as exc:
        raise AssertionError("Public Key is not valid hex.") from exc
    return pubkey


def hex_to_npub(hex_pubkey: str) -> str:
    """
    Converts a hex public key to a Nostr public key.

    Args:
        hex_pubkey (str): The hex public key to convert.

    Returns:
        str: The Nostr public key.
    """
    normalize_public_key(hex_pubkey)
    pubkey_bytes = bytes.fromhex(hex_pubkey)
    bits = convertbits(pubkey_bytes, 8, 5, True)
    assert bits
    return bech32_encode("npub", bits)
