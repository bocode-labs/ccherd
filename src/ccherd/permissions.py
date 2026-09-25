"""Permission modes: what the calling session may do, and what it may hand on.

A subagent runs at its caller's mode or narrower, never wider - a wider child
would be a way around the caller's own permission decision.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import config

# How much a mode lets a session do without asking, widest first. "default" is
# how a transcript spells what the CLI flag calls "manual".
MODE_RANK = {"bypassPermissions": 5, "auto": 4, "acceptEdits": 3, "manual": 2, "default": 2,
             "dontAsk": 1, "plan": 0}
MODES = ["bypassPermissions", "auto", "acceptEdits", "manual", "dontAsk", "plan"]


def caller_mode() -> str | None:
    """The permission mode the calling session is in right now.

    The mode can change mid-session, so it is not derivable from the process. But
    every transcript line records the mode active when it was written
    ("permissionMode"), so the last one in this session's transcript is current.
    """
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        return None
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(cfg) if cfg else config.DEFAULT_CLAUDE_DIR
    transcripts = sorted(base.glob(f"projects/*/{sid}.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in transcripts:
        with open(f, "rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            for window in (1 << 20, size):  # the tail is almost always enough
                fh.seek(max(0, size - window))
                hits = re.findall(rb'"permissionMode":"([A-Za-z]+)"', fh.read())
                if hits:
                    return hits[-1].decode()
    return None


def check_not_wider(child: str, caller: str | None) -> None:
    if caller is None:
        raise SystemExit("ccherd: cannot tell this session's permission mode (no permissionMode in its transcript) "
                         "- refusing rather than guessing; run it from inside a Claude session")
    if MODE_RANK.get(child, 99) > MODE_RANK.get(caller, -1):
        raise SystemExit(f"ccherd: this session runs in {caller}; a subagent in {child} would be allowed more than "
                         f"its caller. Use {caller} or a narrower mode.")


def mode_class(permission_mode: str | None) -> str | None:
    """The receiver of a message compares permission CLASSES: "bypass" or "prompting"."""
    if permission_mode is None:
        return None
    return "bypass" if permission_mode == "bypassPermissions" else "prompting"
