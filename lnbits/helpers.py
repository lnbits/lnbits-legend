import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Type

import jinja2
import jwt
import shortuuid
from pydantic.schema import field_schema

from lnbits.core.extensions.models import Extension
from lnbits.jinja2_templating import Jinja2Templates
from lnbits.nodes import get_node_class
from lnbits.requestvars import g
from lnbits.settings import settings
from lnbits.utils.crypto import AESCipher

from .db import FilterModel


def get_db_vendor_name():
    db_url = settings.lnbits_database_url
    return (
        "PostgreSQL"
        if db_url and db_url.startswith("postgres://")
        else (
            "CockroachDB"
            if db_url and db_url.startswith("cockroachdb://")
            else "SQLite"
        )
    )


def urlsafe_short_hash() -> str:
    return shortuuid.uuid()


def url_for(endpoint: str, external: Optional[bool] = False, **params: Any) -> str:
    base = g().base_url if external else ""
    url_params = "?"
    for key, value in params.items():
        url_params += f"{key}={value}&"
    url = f"{base}{endpoint}{url_params}"
    return url


def static_url_for(static: str, path: str) -> str:
    return f"/{static}/{path}?v={settings.server_startup_time}"


def template_renderer(additional_folders: Optional[list] = None) -> Jinja2Templates:
    folders = ["lnbits/templates", "lnbits/core/templates"]
    if additional_folders:
        additional_folders += [
            Path(settings.lnbits_extensions_path, "extensions", f)
            for f in additional_folders
        ]
        folders.extend(additional_folders)
    t = Jinja2Templates(loader=jinja2.FileSystemLoader(folders))
    t.env.globals["static_url_for"] = static_url_for

    if settings.lnbits_ad_space_enabled:
        t.env.globals["AD_SPACE"] = settings.lnbits_ad_space.split(",")
        t.env.globals["AD_SPACE_TITLE"] = settings.lnbits_ad_space_title

    t.env.globals["VOIDWALLET"] = settings.lnbits_backend_wallet_class == "VoidWallet"
    t.env.globals["HIDE_API"] = settings.lnbits_hide_api
    t.env.globals["SITE_TITLE"] = settings.lnbits_site_title
    t.env.globals["LNBITS_DENOMINATION"] = settings.lnbits_denomination
    t.env.globals["SITE_TAGLINE"] = settings.lnbits_site_tagline
    t.env.globals["SITE_DESCRIPTION"] = settings.lnbits_site_description
    t.env.globals["LNBITS_SHOW_HOME_PAGE_ELEMENTS"] = (
        settings.lnbits_show_home_page_elements
    )
    t.env.globals["LNBITS_CUSTOM_BADGE"] = settings.lnbits_custom_badge
    t.env.globals["LNBITS_CUSTOM_BADGE_COLOR"] = settings.lnbits_custom_badge_color
    t.env.globals["LNBITS_THEME_OPTIONS"] = settings.lnbits_theme_options
    t.env.globals["LNBITS_QR_LOGO"] = settings.lnbits_qr_logo
    t.env.globals["LNBITS_VERSION"] = settings.version
    t.env.globals["LNBITS_NEW_ACCOUNTS_ALLOWED"] = settings.new_accounts_allowed
    t.env.globals["LNBITS_AUTH_METHODS"] = settings.auth_allowed_methods
    t.env.globals["LNBITS_ADMIN_UI"] = settings.lnbits_admin_ui
    t.env.globals["LNBITS_EXTENSIONS_DEACTIVATE_ALL"] = (
        settings.lnbits_extensions_deactivate_all
    )
    t.env.globals["LNBITS_SERVICE_FEE"] = settings.lnbits_service_fee
    t.env.globals["LNBITS_SERVICE_FEE_MAX"] = settings.lnbits_service_fee_max
    t.env.globals["LNBITS_SERVICE_FEE_WALLET"] = settings.lnbits_service_fee_wallet
    t.env.globals["LNBITS_NODE_UI"] = (
        settings.lnbits_node_ui and get_node_class() is not None
    )
    t.env.globals["LNBITS_NODE_UI_AVAILABLE"] = get_node_class() is not None
    t.env.globals["EXTENSIONS"] = Extension.get_valid_extensions(False)
    if settings.lnbits_custom_logo:
        t.env.globals["USE_CUSTOM_LOGO"] = settings.lnbits_custom_logo

    if settings.bundle_assets:
        t.env.globals["INCLUDED_JS"] = ["bundle.min.js"]
        t.env.globals["INCLUDED_CSS"] = ["bundle.min.css"]
        t.env.globals["INCLUDED_COMPONENTS"] = ["bundle-components.min.js"]
    else:
        vendor_filepath = Path(settings.lnbits_path, "static", "vendor.json")
        with open(vendor_filepath) as vendor_file:
            vendor_files = json.loads(vendor_file.read())
            t.env.globals["INCLUDED_JS"] = vendor_files["js"]
            t.env.globals["INCLUDED_CSS"] = vendor_files["css"]
            t.env.globals["INCLUDED_COMPONENTS"] = vendor_files["components"]

    t.env.globals["WEBPUSH_PUBKEY"] = settings.lnbits_webpush_pubkey

    return t


def get_current_extension_name() -> str:
    """
    DEPRECATED: Use the repo name instead, will be removed in the future
    before: `register_invoice_listener(invoice_queue, get_current_extension_name())`
    after: `register_invoice_listener(invoice_queue, "my-extension")`

    Returns the name of the extension that calls this method.
    """
    import inspect
    import json
    import os

    callee_filepath = inspect.stack()[1].filename
    callee_dirname, _ = os.path.split(callee_filepath)

    path = os.path.normpath(callee_dirname)
    extension_director_name = path.split(os.sep)[-1]
    try:
        config_path = os.path.join(callee_dirname, "config.json")
        with open(config_path) as json_file:
            config = json.load(json_file)
        ext_name = config["name"]
    except Exception:
        ext_name = extension_director_name
    return ext_name


def generate_filter_params_openapi(model: Type[FilterModel], keep_optional=False):
    """
    Generate openapi documentation for Filters. This is intended to be used along
    parse_filters (see example)
    :param model: Filter model
    :param keep_optional: If false, all parameters will be optional,
    otherwise inferred from model
    """
    fields = list(model.__fields__.values())
    params = []
    for field in fields:
        schema, _, _ = field_schema(field, model_name_map={})

        description = "Supports Filtering"
        if (
            hasattr(model, "__search_fields__")
            and field.name in model.__search_fields__
        ):
            description += ". Supports Search"

        parameter = {
            "name": field.alias,
            "in": "query",
            "required": field.required if keep_optional else False,
            "schema": schema,
            "description": description,
        }
        params.append(parameter)

    return {
        "parameters": params,
    }


def is_valid_email_address(email: str) -> bool:
    email_regex = r"[A-Za-z0-9\._%+-]+@[A-Za-z0-9\.-]+\.[A-Za-z]{2,63}"
    return re.fullmatch(email_regex, email) is not None


def is_valid_username(username: str) -> bool:
    username_regex = r"(?=[a-zA-Z0-9._]{2,20}$)(?!.*[_.]{2})[^_.].*[^_.]"
    return re.fullmatch(username_regex, username) is not None


def create_access_token(data: dict):
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.auth_token_expire_minutes
    )
    to_encode = data.copy()
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.auth_secret_key, "HS256")


def encrypt_internal_message(m: Optional[str] = None) -> Optional[str]:
    """Encrypt message with the internal secret key"""
    if not m:
        return None
    return AESCipher(key=settings.auth_secret_key).encrypt(m.encode())


def decrypt_internal_message(m: Optional[str] = None) -> Optional[str]:
    """Decrypt message with the internal secret key"""
    if not m:
        return None
    return AESCipher(key=settings.auth_secret_key).decrypt(m)


def filter_dict_keys(data: dict, filter_keys: Optional[list[str]]) -> dict:
    if not filter_keys:
        # return shallow clone of the dict even if there are no filters
        return {**data}
    return {key: data[key] for key in filter_keys if key in data}
