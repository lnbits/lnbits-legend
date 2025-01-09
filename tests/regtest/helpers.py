import hashlib
import json
import os
import time
from subprocess import PIPE, Popen, TimeoutExpired

from loguru import logger

docker_lightning_cli = [
    "docker",
    "exec",
    "lnbits-lnd-1-1",
    "lncli",
    "--network",
    "regtest",
    "--rpcserver=lnd-1",
]

docker_bitcoin_cli = [
    "docker",
    "exec",
    "lnbits-bitcoind-1",
    "bitcoin-cli",
    "-rpcuser=lnbits",
    "-rpcpassword=lnbits",
    "-regtest",
]


docker_lightning_unconnected_cli = [
    "docker",
    "exec",
    "lnbits-lnd-2-1",
    "lncli",
    "--network",
    "regtest",
    "--rpcserver=lnd-2",
]


def run_cmd(cmd: list) -> str:
    timeout = 10
    process = Popen(cmd, stdout=PIPE, stderr=PIPE)

    logger.debug(f"running command: {cmd}")

    def process_communication(comm):
        stdout, stderr = comm
        output = stdout.decode("utf-8").strip()
        error = stderr.decode("utf-8").strip()
        return output, error

    try:
        now = time.time()
        output, error = process_communication(process.communicate(timeout=timeout))
        took = time.time() - now
        logger.debug(f"ran command output: {output}, error: {error}, took: {took}s")
        return output
    except TimeoutExpired:
        process.kill()
        output, error = process_communication(process.communicate())
        logger.error(f"timeout command: {cmd}, output: {output}, error: {error}")
        raise


def run_cmd_json(cmd: list) -> dict:
    output = run_cmd(cmd)
    try:
        return json.loads(output) if output else {}
    except json.decoder.JSONDecodeError:
        logger.error(f"failed to decode json from cmd `{cmd}`: {output}")
        raise


def get_hold_invoice(sats: int) -> tuple[str, dict]:
    preimage = os.urandom(32)
    preimage_hash = hashlib.sha256(preimage).hexdigest()
    cmd = docker_lightning_cli.copy()
    cmd.extend(["addholdinvoice", preimage_hash, str(sats)])
    json = run_cmd_json(cmd)
    return preimage.hex(), json


def settle_invoice(preimage: str) -> str:
    cmd = docker_lightning_cli.copy()
    cmd.extend(["settleinvoice", preimage])
    return run_cmd(cmd)


def cancel_invoice(preimage_hash: str) -> str:
    cmd = docker_lightning_cli.copy()
    cmd.extend(["cancelinvoice", preimage_hash])
    return run_cmd(cmd)


def get_real_invoice(sats: int) -> dict:
    cmd = docker_lightning_cli.copy()
    cmd.extend(["addinvoice", str(sats)])
    return run_cmd_json(cmd)


def pay_real_invoice(invoice: str) -> str:
    cmd = docker_lightning_cli.copy()
    cmd.extend(["payinvoice", "--force", invoice])
    return run_cmd(cmd)


def mine_blocks(blocks: int = 1) -> str:
    cmd = docker_bitcoin_cli.copy()
    cmd.extend(["-generate", str(blocks)])
    return run_cmd(cmd)


def get_unconnected_node_uri() -> str:
    cmd = docker_lightning_unconnected_cli.copy()
    cmd.append("getinfo")
    info = run_cmd_json(cmd)
    pubkey = info["identity_pubkey"]
    return f"{pubkey}@lnd-2:9735"


def create_onchain_address(address_type: str = "bech32") -> str:
    cmd = docker_bitcoin_cli.copy()
    cmd.extend(["getnewaddress", address_type])
    return run_cmd(cmd)


def pay_onchain(address: str, sats: int) -> str:
    btc = sats * 0.00000001
    cmd = docker_bitcoin_cli.copy()
    cmd.extend(["sendtoaddress", address, str(btc)])
    return run_cmd(cmd)
