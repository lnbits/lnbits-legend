from __future__ import annotations

import importlib
import importlib.metadata
import inspect
import json
from enum import Enum
from hashlib import sha256
from os import path
from sqlite3 import Row
from time import time
from typing import Any, Optional

import httpx
from loguru import logger
from pydantic import BaseModel, BaseSettings, Extra, Field, validator


def list_parse_fallback(v: str):
    v = v.replace(" ", "")
    if len(v) > 0:
        if v.startswith("[") or v.startswith("{"):
            return json.loads(v)
        else:
            return v.split(",")
    else:
        return []


class LNbitsSettings(BaseModel):
    @classmethod
    def validate_list(cls, val):
        if isinstance(val, str):
            val = val.split(",") if val else []
        return val


class UsersSettings(LNbitsSettings):
    lnbits_admin_users: list[str] = Field(default=[])
    lnbits_allowed_users: list[str] = Field(default=[])
    lnbits_allow_new_accounts: bool = Field(default=True)

    @property
    def new_accounts_allowed(self) -> bool:
        return self.lnbits_allow_new_accounts and len(self.lnbits_allowed_users) == 0


class ExtensionsSettings(LNbitsSettings):
    lnbits_admin_extensions: list[str] = Field(default=[])
    lnbits_extensions_deactivate_all: bool = Field(default=False)
    lnbits_extensions_manifests: list[str] = Field(
        default=[
            "https://raw.githubusercontent.com/lnbits/lnbits-extensions/main/extensions.json"
        ]
    )


class ExtensionsInstallSettings(LNbitsSettings):
    lnbits_extensions_default_install: list[str] = Field(default=[])
    # required due to GitHUb rate-limit
    lnbits_ext_github_token: str = Field(default="")


class InstalledExtensionsSettings(LNbitsSettings):
    # installed extensions that have been deactivated
    lnbits_deactivated_extensions: set[str] = Field(default=[])
    # upgraded extensions that require API redirects
    lnbits_upgraded_extensions: set[str] = Field(default=[])
    # list of redirects that extensions want to perform
    lnbits_extensions_redirects: list[Any] = Field(default=[])

    # list of all extension ids
    lnbits_all_extensions_ids: set[Any] = Field(default=[])

    def extension_upgrade_path(self, ext_id: str) -> Optional[str]:
        return next(
            (e for e in self.lnbits_upgraded_extensions if e.endswith(f"/{ext_id}")),
            None,
        )

    def extension_upgrade_hash(self, ext_id: str) -> Optional[str]:
        path = settings.extension_upgrade_path(ext_id)
        return path.split("/")[0] if path else None


class ThemesSettings(LNbitsSettings):
    lnbits_site_title: str = Field(default="LNbits")
    lnbits_site_tagline: str = Field(default="free and open-source lightning wallet")
    lnbits_site_description: Optional[str] = Field(
        default="The world's most powerful suite of bitcoin tools."
    )
    lnbits_show_home_page_elements: bool = Field(default=True)
    lnbits_default_wallet_name: str = Field(default="LNbits wallet")
    lnbits_custom_badge: Optional[str] = Field(default=None)
    lnbits_custom_badge_color: str = Field(default="warning")
    lnbits_theme_options: list[str] = Field(
        default=[
            "classic",
            "freedom",
            "mint",
            "salvador",
            "monochrome",
            "autumn",
            "cyber",
        ]
    )
    lnbits_custom_logo: Optional[str] = Field(default=None)
    lnbits_ad_space_title: str = Field(default="Supported by")
    lnbits_ad_space: str = Field(
        default="https://shop.lnbits.com/;/static/images/bitcoin-shop-banner.png;/static/images/bitcoin-shop-banner.png,https://affil.trezor.io/aff_c?offer_id=169&aff_id=33845;/static/images/bitcoin-hardware-wallet.png;/static/images/bitcoin-hardware-wallet.png,https://opensats.org/;/static/images/open-sats.png;/static/images/open-sats.png"
    )  # sneaky sneaky
    lnbits_ad_space_enabled: bool = Field(default=False)
    lnbits_allowed_currencies: list[str] = Field(default=[])
    lnbits_default_accounting_currency: Optional[str] = Field(default=None)
    lnbits_qr_logo: str = Field(default="/static/images/logos/lnbits.png")


class OpsSettings(LNbitsSettings):
    lnbits_baseurl: str = Field(default="http://127.0.0.1:5000/")
    lnbits_reserve_fee_min: int = Field(default=2000)
    lnbits_reserve_fee_percent: float = Field(default=1.0)
    lnbits_service_fee: float = Field(default=0)
    lnbits_service_fee_ignore_internal: bool = Field(default=True)
    lnbits_service_fee_max: int = Field(default=0)
    lnbits_service_fee_wallet: Optional[str] = Field(default=None)
    lnbits_hide_api: bool = Field(default=False)
    lnbits_denomination: str = Field(default="sats")


class SecuritySettings(LNbitsSettings):
    lnbits_rate_limit_no: str = Field(default="200")
    lnbits_rate_limit_unit: str = Field(default="minute")
    lnbits_allowed_ips: list[str] = Field(default=[])
    lnbits_blocked_ips: list[str] = Field(default=[])
    lnbits_notifications: bool = Field(default=False)
    lnbits_killswitch: bool = Field(default=False)
    lnbits_killswitch_interval: int = Field(default=60)
    lnbits_wallet_limit_max_balance: int = Field(default=0)
    lnbits_wallet_limit_daily_max_withdraw: int = Field(default=0)
    lnbits_wallet_limit_secs_between_trans: int = Field(default=0)
    lnbits_watchdog: bool = Field(default=False)
    lnbits_watchdog_interval: int = Field(default=60)
    lnbits_watchdog_delta: int = Field(default=1_000_000)
    lnbits_status_manifest: str = Field(
        default=(
            "https://raw.githubusercontent.com/lnbits/lnbits-status/main/manifest.json"
        )
    )
    lnbits_ws_tunnel_enabled: bool = Field(default=True)  # todo: default to false

    def is_wallet_max_balance_exceeded(self, amount):
        return (
            self.lnbits_wallet_limit_max_balance
            and self.lnbits_wallet_limit_max_balance > 0
            and amount > self.lnbits_wallet_limit_max_balance
        )


class FakeWalletFundingSource(LNbitsSettings):
    fake_wallet_secret: str = Field(default="ToTheMoon1")


class LNbitsFundingSource(LNbitsSettings):
    lnbits_endpoint: str = Field(default="https://demo.lnbits.com")
    lnbits_key: Optional[str] = Field(default=None)
    lnbits_admin_key: Optional[str] = Field(default=None)
    lnbits_invoice_key: Optional[str] = Field(default=None)


class ClicheFundingSource(LNbitsSettings):
    cliche_endpoint: Optional[str] = Field(default=None)


class CoreLightningFundingSource(LNbitsSettings):
    corelightning_rpc: Optional[str] = Field(default=None)
    corelightning_pay_command: str = Field(default="pay")
    clightning_rpc: Optional[str] = Field(default=None)


class CoreLightningRestFundingSource(LNbitsSettings):
    corelightning_rest_url: Optional[str] = Field(default=None)
    corelightning_rest_macaroon: Optional[str] = Field(default=None)
    corelightning_rest_cert: Optional[str] = Field(default=None)


class EclairFundingSource(LNbitsSettings):
    eclair_url: Optional[str] = Field(default=None)
    eclair_pass: Optional[str] = Field(default=None)


class LndRestFundingSource(LNbitsSettings):
    lnd_rest_endpoint: Optional[str] = Field(default=None)
    lnd_rest_cert: Optional[str] = Field(default=None)
    lnd_rest_macaroon: Optional[str] = Field(default=None)
    lnd_rest_macaroon_encrypted: Optional[str] = Field(default=None)
    lnd_rest_route_hints: bool = Field(default=True)
    lnd_cert: Optional[str] = Field(default=None)
    lnd_admin_macaroon: Optional[str] = Field(default=None)
    lnd_invoice_macaroon: Optional[str] = Field(default=None)
    lnd_rest_admin_macaroon: Optional[str] = Field(default=None)
    lnd_rest_invoice_macaroon: Optional[str] = Field(default=None)


class LndGrpcFundingSource(LNbitsSettings):
    lnd_grpc_endpoint: Optional[str] = Field(default=None)
    lnd_grpc_cert: Optional[str] = Field(default=None)
    lnd_grpc_port: Optional[int] = Field(default=None)
    lnd_grpc_admin_macaroon: Optional[str] = Field(default=None)
    lnd_grpc_invoice_macaroon: Optional[str] = Field(default=None)
    lnd_grpc_macaroon: Optional[str] = Field(default=None)
    lnd_grpc_macaroon_encrypted: Optional[str] = Field(default=None)


class LnPayFundingSource(LNbitsSettings):
    lnpay_api_endpoint: Optional[str] = Field(default=None)
    lnpay_api_key: Optional[str] = Field(default=None)
    lnpay_wallet_key: Optional[str] = Field(default=None)
    lnpay_admin_key: Optional[str] = Field(default=None)


class ZBDFundingSource(LNbitsSettings):
    zbd_api_endpoint: Optional[str] = Field(default="https://api.zebedee.io/v0/")
    zbd_api_key: Optional[str] = Field(default=None)


class PhoenixdFundingSource(LNbitsSettings):
    phoenixd_api_endpoint: Optional[str] = Field(default="http://localhost:9740/")
    phoenixd_api_password: Optional[str] = Field(default=None)


class AlbyFundingSource(LNbitsSettings):
    alby_api_endpoint: Optional[str] = Field(default="https://api.getalby.com/")
    alby_access_token: Optional[str] = Field(default=None)


class OpenNodeFundingSource(LNbitsSettings):
    opennode_api_endpoint: Optional[str] = Field(default=None)
    opennode_key: Optional[str] = Field(default=None)
    opennode_admin_key: Optional[str] = Field(default=None)
    opennode_invoice_key: Optional[str] = Field(default=None)


class SparkFundingSource(LNbitsSettings):
    spark_url: Optional[str] = Field(default=None)
    spark_token: Optional[str] = Field(default=None)


class LnTipsFundingSource(LNbitsSettings):
    lntips_api_endpoint: Optional[str] = Field(default=None)
    lntips_api_key: Optional[str] = Field(default=None)
    lntips_admin_key: Optional[str] = Field(default=None)
    lntips_invoice_key: Optional[str] = Field(default=None)


class LightningSettings(LNbitsSettings):
    lightning_invoice_expiry: int = Field(default=3600)


class FundingSourcesSettings(
    FakeWalletFundingSource,
    LNbitsFundingSource,
    ClicheFundingSource,
    CoreLightningFundingSource,
    CoreLightningRestFundingSource,
    EclairFundingSource,
    LndRestFundingSource,
    LndGrpcFundingSource,
    LnPayFundingSource,
    AlbyFundingSource,
    ZBDFundingSource,
    PhoenixdFundingSource,
    OpenNodeFundingSource,
    SparkFundingSource,
    LnTipsFundingSource,
):
    lnbits_backend_wallet_class: str = Field(default="VoidWallet")


class WebPushSettings(LNbitsSettings):
    lnbits_webpush_pubkey: Optional[str] = Field(default=None)
    lnbits_webpush_privkey: Optional[str] = Field(default=None)


class NodeUISettings(LNbitsSettings):
    # on-off switch for node ui
    lnbits_node_ui: bool = Field(default=False)
    # whether to display the public node ui (only if lnbits_node_ui is True)
    lnbits_public_node_ui: bool = Field(default=False)
    # can be used to disable the transactions tab in the node ui
    # (recommended for large cln nodes)
    lnbits_node_ui_transactions: bool = Field(default=False)


class AuthMethods(Enum):
    user_id_only = "user-id-only"
    username_and_password = "username-password"
    google_auth = "google-auth"
    github_auth = "github-auth"
    keycloak_auth = "keycloak-auth"


class AuthSettings(LNbitsSettings):
    auth_token_expire_minutes: int = Field(default=525600)
    auth_all_methods = [a.value for a in AuthMethods]
    auth_allowed_methods: list[str] = Field(
        default=[
            AuthMethods.user_id_only.value,
            AuthMethods.username_and_password.value,
        ]
    )

    def is_auth_method_allowed(self, method: AuthMethods):
        return method.value in self.auth_allowed_methods


class GoogleAuthSettings(LNbitsSettings):
    google_client_id: str = Field(default="")
    google_client_secret: str = Field(default="")


class GitHubAuthSettings(LNbitsSettings):
    github_client_id: str = Field(default="")
    github_client_secret: str = Field(default="")


class KeycloakAuthSettings(LNbitsSettings):
    keycloak_discovery_url: str = Field(default="")
    keycloak_client_id: str = Field(default="")
    keycloak_client_secret: str = Field(default="")


class EditableSettings(
    UsersSettings,
    ExtensionsSettings,
    ThemesSettings,
    OpsSettings,
    SecuritySettings,
    FundingSourcesSettings,
    LightningSettings,
    WebPushSettings,
    NodeUISettings,
    AuthSettings,
    GoogleAuthSettings,
    GitHubAuthSettings,
    KeycloakAuthSettings,
):
    @validator(
        "lnbits_admin_users",
        "lnbits_allowed_users",
        "lnbits_theme_options",
        "lnbits_admin_extensions",
        pre=True,
    )
    @classmethod
    def validate_editable_settings(cls, val):
        return super().validate_list(val)

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            **{k: v for k, v in d.items() if k in inspect.signature(cls).parameters}
        )

    # fixes openapi.json validation, remove field env_names
    class Config:
        @staticmethod
        def schema_extra(schema: dict[str, Any]) -> None:
            for prop in schema.get("properties", {}).values():
                prop.pop("env_names", None)


class UpdateSettings(EditableSettings):
    class Config:
        extra = Extra.forbid


class EnvSettings(LNbitsSettings):
    debug: bool = Field(default=False)
    debug_database: bool = Field(default=False)
    bundle_assets: bool = Field(default=True)
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=5000)
    forwarded_allow_ips: str = Field(default="*")
    lnbits_title: str = Field(default="LNbits API")
    lnbits_path: str = Field(default=".")
    lnbits_extensions_path: str = Field(default="lnbits")
    super_user: str = Field(default="")
    auth_secret_key: str = Field(default="")
    version: str = Field(default="0.0.0")
    user_agent: str = Field(default="")
    enable_log_to_file: bool = Field(default=True)
    log_rotation: str = Field(default="100 MB")
    log_retention: str = Field(default="3 months")
    server_startup_time: int = Field(default=time())
    cleanup_wallets_days: int = Field(default=90)
    funding_source_max_retries: int = Field(default=4)

    @property
    def has_default_extension_path(self) -> bool:
        return self.lnbits_extensions_path == "lnbits"


class SaaSSettings(LNbitsSettings):
    lnbits_saas_callback: Optional[str] = Field(default=None)
    lnbits_saas_secret: Optional[str] = Field(default=None)
    lnbits_saas_instance_id: Optional[str] = Field(default=None)


class PersistenceSettings(LNbitsSettings):
    lnbits_data_folder: str = Field(default="./data")
    lnbits_database_url: str = Field(default=None)


class SuperUserSettings(LNbitsSettings):
    lnbits_allowed_funding_sources: list[str] = Field(
        default=[
            "AlbyWallet",
            "FakeWallet",
            "CoreLightningRestWallet",
            "CoreLightningWallet",
            "EclairWallet",
            "LNbitsWallet",
            "LndRestWallet",
            "LNPayWallet",
            "LnTipsWallet",
            "LndWallet",
            "OpenNodeWallet",
            "PhoenixdWallet",
            "VoidWallet",
            "ZBDWallet",
        ]
    )


class TransientSettings(InstalledExtensionsSettings):
    # Transient Settings:
    #  - are initialized, updated and used at runtime
    #  - are not read from a file or from the `settings` table
    #  - are not persisted in the `settings` table when the settings are updated
    #  - are cleared on server restart
    first_install: bool = Field(default=False)

    # Indicates that the server should continue to run.
    # When set to false it indicates that the shutdown procedure is ongoing.
    # If false no new tasks, threads, etc should be started.
    # Long running while loops should use this flag instead of `while True:`
    lnbits_running: bool = Field(default=True)

    @classmethod
    def readonly_fields(cls):
        return [f for f in inspect.signature(cls).parameters if not f.startswith("_")]


class ReadOnlySettings(
    EnvSettings,
    ExtensionsInstallSettings,
    SaaSSettings,
    PersistenceSettings,
    SuperUserSettings,
):
    lnbits_admin_ui: bool = Field(default=False)

    @validator(
        "lnbits_allowed_funding_sources",
        pre=True,
    )
    @classmethod
    def validate_readonly_settings(cls, val):
        return super().validate_list(val)

    @classmethod
    def readonly_fields(cls):
        return [f for f in inspect.signature(cls).parameters if not f.startswith("_")]


class Settings(EditableSettings, ReadOnlySettings, TransientSettings, BaseSettings):
    @classmethod
    def from_row(cls, row: Row) -> Settings:
        data = dict(row)
        return cls(**data)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        json_loads = list_parse_fallback

    def is_user_allowed(self, user_id: str) -> bool:
        return (
            len(self.lnbits_allowed_users) == 0
            or user_id in self.lnbits_allowed_users
            or user_id in self.lnbits_admin_users
            or user_id == self.super_user
        )

    def is_admin_user(self, user_id: str) -> bool:
        return user_id in self.lnbits_admin_users or user_id == self.super_user

    def is_admin_extension(self, ext_id: str) -> bool:
        return ext_id in self.lnbits_admin_extensions

    def is_extension_id(self, ext_id: str) -> bool:
        return ext_id in self.lnbits_all_extensions_ids


class SuperSettings(EditableSettings):
    super_user: str


class AdminSettings(EditableSettings):
    is_super_user: bool
    lnbits_allowed_funding_sources: Optional[list[str]]


def set_cli_settings(**kwargs):
    for key, value in kwargs.items():
        setattr(settings, key, value)


def send_admin_user_to_saas():
    if settings.lnbits_saas_callback:
        with httpx.Client() as client:
            headers = {
                "Content-Type": "application/json; charset=utf-8",
                "X-API-KEY": settings.lnbits_saas_secret,
            }
            payload = {
                "instance_id": settings.lnbits_saas_instance_id,
                "adminuser": settings.super_user,
            }
            try:
                client.post(
                    settings.lnbits_saas_callback,
                    headers=headers,
                    json=payload,
                )
                logger.success("sent super_user to saas application")
            except Exception as e:
                logger.error(
                    "error sending super_user to saas:"
                    f" {settings.lnbits_saas_callback}. Error: {e!s}"
                )


readonly_variables = ReadOnlySettings.readonly_fields()
transient_variables = TransientSettings.readonly_fields()

settings = Settings()

settings.lnbits_path = str(path.dirname(path.realpath(__file__)))

settings.version = importlib.metadata.version("lnbits")
settings.auth_secret_key = (
    settings.auth_secret_key or sha256(settings.super_user.encode("utf-8")).hexdigest()
)

if not settings.user_agent:
    settings.user_agent = f"LNbits/{settings.version}"

# printing environment variable for debugging
if not settings.lnbits_admin_ui:
    logger.debug("Environment Settings:")
    for key, value in settings.dict(exclude_none=True).items():
        logger.debug(f"{key}: {value}")


def get_funding_source():
    """
    Backwards compatibility
    """
    from lnbits.wallets import get_funding_source

    return get_funding_source()
