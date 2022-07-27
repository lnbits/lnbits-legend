# flake8: noqa

from .cln import CoreLightningWallet  # legacy .env support
from .cln import CoreLightningWallet as CLightningWallet
from .eclair import EclairWallet
from .fake import FakeWallet
from .lnbits import LNbitsWallet
from .lndrest import LndRestWallet
from .lnpay import LNPayWallet
from .lntxbot import LntxbotWallet
from .opennode import OpenNodeWallet
from .spark import SparkWallet
from .void import VoidWallet
