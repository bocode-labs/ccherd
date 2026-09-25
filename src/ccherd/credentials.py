"""Read (never refresh) the OAuth token Claude Code stored for one config dir.

Linux keeps it in <config dir>/.credentials.json. macOS keeps it in the login
keychain, under "Claude Code-credentials" for ~/.claude and
"Claude Code-credentials-<first 8 hex of sha256(config dir)>" for any other dir.

ccherd never refreshes a token itself: the refresh token rotates on use, so a
refresh done here would log out the Claude Code instance that owns the account.
When an access token has expired and no session runs on that account, it asks
Claude Code to do it (`claude auth status` under that config dir) - Claude Code
then rotates and stores the tokens the way it always does.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config


def keychain_service(cfg: Path) -> str:
    if cfg.expanduser().resolve() == config.DEFAULT_CLAUDE_DIR.resolve():
        return "Claude Code-credentials"
    digest = hashlib.sha256(str(cfg.expanduser()).encode()).hexdigest()[:8]
    return f"Claude Code-credentials-{digest}"


def _raw(cfg: Path) -> str | None:
    file = cfg / ".credentials.json"
    if file.is_file():
        try:
            return file.read_text()
        except OSError:
            return None
    if sys.platform == "darwin":
        r = subprocess.run(["security", "find-generic-password", "-s", keychain_service(cfg), "-w"],
                           capture_output=True, check=False, text=True)
        return r.stdout if r.returncode == 0 else None
    return None


def read_oauth(cfg: Path) -> dict:
    raw = _raw(cfg)
    if not raw:
        return {}
    try:
        return json.loads(raw).get("claudeAiOauth") or {}
    except ValueError:
        return {}


def is_logged_in(cfg: Path) -> bool:
    return bool(read_oauth(cfg).get("accessToken"))


def expired(oauth: dict) -> bool:
    return oauth.get("expiresAt", 0) / 1000 <= time.time()


def fresh_oauth(cfg: Path) -> dict:
    """read_oauth, after letting Claude Code renew an expired access token when that is safe."""
    oauth = read_oauth(cfg)
    if oauth.get("accessToken") and expired(oauth) and _renew_via_claude(cfg):
        oauth = read_oauth(cfg)
    return oauth


def _renew_via_claude(cfg: Path) -> bool:
    from .sessions import sessions_in

    if sessions_in(cfg) or not shutil.which("claude"):
        return False  # a running session renews its own token; two renewing at once would clash
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(cfg)}
    try:
        subprocess.run(["claude", "auth", "status"], env=env, capture_output=True, check=False, timeout=30)
    except subprocess.TimeoutExpired:
        return False
    return True
