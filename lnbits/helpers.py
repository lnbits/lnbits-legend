import glob
import os
import base64
from typing import Any, List, NamedTuple, Optional

import jinja2
import shortuuid  # type: ignore

from lnbits.jinja2_templating import Jinja2Templates
from lnbits.requestvars import g
from lnbits.settings import settings

from .extension_manager import get_valid_extensions


def urlsafe_short_hash() -> str:
    return shortuuid.uuid()


def get_js_vendored(prefer_minified: bool = False) -> List[str]:
    paths = get_vendored(".js", prefer_minified)

    def sorter(key: str):
        if "moment@" in key:
            return 1
        if "vue@" in key:
            return 2
        if "vue-router@" in key:
            return 3
        if "polyfills" in key:
            return 4
        return 9

    return sorted(paths, key=sorter)


def get_css_vendored(prefer_minified: bool = False) -> List[str]:
    paths = get_vendored(".css", prefer_minified)

    def sorter(key: str):
        if "quasar@" in key:
            return 1
        if "vue@" in key:
            return 2
        if "chart.js@" in key:
            return 100
        return 9

    return sorted(paths, key=sorter)


def get_vendored(ext: str, prefer_minified: bool = False) -> List[str]:
    paths: List[str] = []
    for path in glob.glob(
        os.path.join(settings.lnbits_path, "static/vendor/**"), recursive=True
    ):
        if path.endswith(".min" + ext):
            # path is minified
            unminified = path.replace(".min" + ext, ext)
            if prefer_minified:
                paths.append(path)
                if unminified in paths:
                    paths.remove(unminified)
            elif unminified not in paths:
                paths.append(path)

        elif path.endswith(ext):
            # path is not minified
            minified = path.replace(ext, ".min" + ext)
            if not prefer_minified:
                paths.append(path)
                if minified in paths:
                    paths.remove(minified)
            elif minified not in paths:
                paths.append(path)

    return sorted(paths)


def url_for_vendored(abspath: str) -> str:
    return "/" + os.path.relpath(abspath, settings.lnbits_path)


def url_for(endpoint: str, external: Optional[bool] = False, **params: Any) -> str:
    base = g().base_url if external else ""
    url_params = "?"
    for key in params:
        url_params += f"{key}={params[key]}&"
    url = f"{base}{endpoint}{url_params}"
    return url


def template_renderer(additional_folders: List = []) -> Jinja2Templates:

    t = Jinja2Templates(
        loader=jinja2.FileSystemLoader(
            ["lnbits/templates", "lnbits/core/templates", *additional_folders]
        )
    )

    if settings.lnbits_ad_space_enabled:
        t.env.globals["AD_SPACE"] = settings.lnbits_ad_space.split(",")
        t.env.globals["AD_SPACE_TITLE"] = settings.lnbits_ad_space_title

    t.env.globals["HIDE_API"] = settings.lnbits_hide_api
    t.env.globals["SITE_TITLE"] = settings.lnbits_site_title
    t.env.globals["LNBITS_DENOMINATION"] = settings.lnbits_denomination
    t.env.globals["SITE_TAGLINE"] = settings.lnbits_site_tagline
    t.env.globals["SITE_DESCRIPTION"] = settings.lnbits_site_description
    t.env.globals["LNBITS_THEME_OPTIONS"] = settings.lnbits_theme_options
    t.env.globals["LNBITS_VERSION"] = settings.lnbits_commit
    t.env.globals["EXTENSIONS"] = [
        e
        for e in get_valid_extensions()
        if e.code not in settings.lnbits_deactivated_extensions
    ]
    if settings.lnbits_custom_logo:
        t.env.globals["USE_CUSTOM_LOGO"] = settings.lnbits_custom_logo

    if settings.debug:
        t.env.globals["VENDORED_JS"] = map(url_for_vendored, get_js_vendored())
        t.env.globals["VENDORED_CSS"] = map(url_for_vendored, get_css_vendored())
    else:
        t.env.globals["VENDORED_JS"] = ["/static/bundle.js"]
        t.env.globals["VENDORED_CSS"] = ["/static/bundle.css"]

    return t


def get_current_extension_name() -> str:
    """
    Returns the name of the extension that calls this method.
    """
    import inspect
    import json
    import os

    callee_filepath = inspect.stack()[1].filename
    callee_dirname, callee_filename = os.path.split(callee_filepath)

    path = os.path.normpath(callee_dirname)
    extension_director_name = path.split(os.sep)[-1]
    try:
        config_path = os.path.join(callee_dirname, "config.json")
        with open(config_path) as json_file:
            config = json.load(json_file)
        ext_name = config["name"]
    except:
        ext_name = extension_director_name
    return ext_name

def b64_hex_transform(plain_str: str) -> str:
    """Returns the b64 transformed version of a hex string"""
    a_string = bytes.fromhex(plain_str)
    return base64.b64encode(a_string).decode()

def b64_transform(plain_str: str) -> str:
    """Returns the b64 transformed version of a string"""
    return base64.b64encode(plain_str.encode()).decode()
