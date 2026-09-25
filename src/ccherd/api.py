"""Read-only calls to the Anthropic OAuth API, with the token of one account."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

BASE_URL = "https://api.anthropic.com/api/oauth"


def get(path: str, token: str) -> dict:
    """GET {BASE_URL}/{path}. Raises urllib.error.URLError, OSError or ValueError."""
    req = urllib.request.Request(f"{BASE_URL}/{path}", headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
    })
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def write_cache(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.chmod(tmp, 0o600)
    tmp.replace(path)
