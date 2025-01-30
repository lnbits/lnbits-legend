from __future__ import annotations

import importlib
from typing import Optional

from lnbits.nodes import set_node_class
from lnbits.settings import settings
from lnbits.wallets.base import Wallet

from .alby import AlbyWallet
from .blink import BlinkWallet
from .boltz import BoltzWallet
from .breez import BreezSdkWallet
from .breez_liquid import BreezLiquidSdkWallet
from .cliche import ClicheWallet
from .corelightning import CoreLightningWallet

# The following import is intentional to keep backwards compatibility
# for old configs that called it CLightningWallet. Do not remove.
from .corelightning import CoreLightningWallet as CLightningWallet
from .corelightningrest import CoreLightningRestWallet
from .eclair import EclairWallet
from .fake import FakeWallet
from .lnbits import LNbitsWallet
from .lndgrpc import LndWallet
from .lndrest import LndRestWallet
from .lnpay import LNPayWallet
from .lntips import LnTipsWallet
from .nwc import NWCWallet
from .opennode import OpenNodeWallet
from .phoenixd import PhoenixdWallet
from .spark import SparkWallet
from .void import VoidWallet
from .zbd import ZBDWallet


def set_funding_source(class_name: Optional[str] = None):
    backend_wallet_class = class_name or settings.lnbits_backend_wallet_class
    funding_source_constructor = getattr(wallets_module, backend_wallet_class)
    global funding_source
    funding_source = funding_source_constructor()
    if funding_source.__node_cls__:
        set_node_class(funding_source.__node_cls__(funding_source))


def get_funding_source() -> Wallet:
    return funding_source


wallets_module = importlib.import_module("lnbits.wallets")
fake_wallet = FakeWallet()

# initialize as fake wallet
funding_source: Wallet = fake_wallet


__all__ = [
    "AlbyWallet",
    "BlinkWallet",
    "BoltzWallet",
    "BreezSdkWallet",
    "BreezLiquidSdkWallet",
    "ClicheWallet",
    "CoreLightningWallet",
    "CLightningWallet",
    "CoreLightningRestWallet",
    "EclairWallet",
    "FakeWallet",
    "LNbitsWallet",
    "LndWallet",
    "LndRestWallet",
    "LNPayWallet",
    "LnTipsWallet",
    "NWCWallet",
    "OpenNodeWallet",
    "PhoenixdWallet",
    "SparkWallet",
    "VoidWallet",
    "ZBDWallet",
]
