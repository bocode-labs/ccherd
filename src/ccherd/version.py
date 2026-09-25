"""Is this the newest ccherd on PyPI?"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

from . import __version__, api, config

PYPI_URL = "https://pypi.org/pypi/ccherd/json"
CHECK_EVERY_S = 86400


def latest() -> str | None:
    """The newest released version, asked at most once a day; None when PyPI cannot be reached."""
    cache = config.CACHE_DIR / "latest-version.json"
    try:
        cached = json.loads(cache.read_text())
        if time.time() - cached["checked_at"] < CHECK_EVERY_S:
            return cached["version"]
    except (OSError, ValueError, KeyError):
        pass
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=3) as r:
            version = json.load(r)["info"]["version"]
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return None
    api.write_cache(cache, {"checked_at": time.time(), "version": version})
    return version


def parse(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split(".") if p.isdigit())


def upgrade_command() -> str:
    """How this copy was installed decides how to upgrade it."""
    prefix = sys.prefix.replace("\\", "/")
    if "/uv/tools/" in prefix:
        return "uv tool upgrade ccherd"
    if "/pipx/venvs/" in prefix:
        return "pipx upgrade ccherd"
    return "pip install --upgrade ccherd"


def status() -> tuple[bool | None, str]:
    """(up to date?, detail). None: could not check."""
    newest = latest()
    if newest is None:
        return None, f"{__version__} (could not reach PyPI to compare)"
    if parse(newest) > parse(__version__):
        return False, f"{__version__} installed, {newest} available - {upgrade_command()}"
    return True, f"{__version__}, the newest"
