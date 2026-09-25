"""Who a config dir is logged in as: user and organization, from the API.

One login can hold several seats (a private plan and a company team), so the
organization is what tells accounts apart - the profile file Claude Code writes
next to a config dir can name the wrong one. The last answer per dir is cached,
so a dir whose token has expired can still be placed.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
from pathlib import Path

from . import api, config
from .credentials import fresh_oauth


def _cache(cfg: Path) -> Path:
    key = hashlib.sha256(str(cfg.expanduser().resolve()).encode()).hexdigest()[:12]
    return config.CACHE_DIR / f"profile-{key}.json"


def cached_profile(cfg: Path) -> dict | None:
    try:
        return json.loads(_cache(cfg).read_text())
    except (OSError, ValueError):
        return None


def fetch_profile(cfg: Path) -> dict | None:
    """Ask the API when the token is valid, otherwise fall back to the cached answer."""
    oauth = fresh_oauth(cfg)
    if oauth.get("accessToken") and oauth.get("expiresAt", 0) / 1000 > time.time():
        try:
            profile = api.get("profile", oauth["accessToken"])
            api.write_cache(_cache(cfg), profile)
            return profile
        except (urllib.error.URLError, OSError, ValueError):
            pass
    return cached_profile(cfg)


def organization(profile: dict | None) -> dict | None:
    """{"uuid", "name"} of the profile's organization, or None if unknown."""
    org = (profile or {}).get("organization") or {}
    return {"uuid": org["uuid"], "name": org.get("name") or org["uuid"]} if org.get("uuid") else None
