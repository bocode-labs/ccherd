"""Live Claude Code sessions of every account, and writing into their inboxes.

Each session registers itself as <config dir>/sessions/<pid>.json and listens on
a unix socket (messagingSocketPath). The inbox protocol is one JSON object per
line: first {"type":"auth","token":<peerToken>}, the token read from
<config dir>/sessions/<pid>.<hash>.key, then a {"type":"user",...} frame.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from pathlib import Path

from . import config
from .procs import pid_alive


def sessions_in(cfg: Path) -> list[dict]:
    """Live sessions registered in one config dir."""
    out = []
    for f in (cfg / "sessions").glob("*.json"):
        try:
            s = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if pid_alive(int(s.get("pid", 0)), s.get("procStart")):
            out.append(s)
    return out


def live_sessions() -> list[dict]:
    return [{**s, "account": a.label, "config_dir": str(a.dir)}
            for a in config.load_accounts() for s in sessions_in(a.dir)]


def session_by_socket(path: str | None) -> dict | None:
    if not path:
        return None
    return next((s for s in live_sessions() if s.get("messagingSocketPath") == path), None)


def find_session(target: str) -> dict:
    """A live session by uds: address, exact name, sessionId, pid or unique name prefix."""
    sessions = live_sessions()
    if target.startswith("uds:"):
        # The address a received message names in its from= attribute, so a reply
        # reaches exactly the session that wrote.
        hit = [s for s in sessions if s.get("messagingSocketPath") == target[4:]]
        if hit:
            return hit[0]
        raise SystemExit(f"ccherd: no live session behind {target} - it has ended (see `ccherd sessions`)")
    for key in ("name", "sessionId"):
        hit = [s for s in sessions if s.get(key) == target]
        if len(hit) == 1:
            return hit[0]
    if target.isdigit():
        hit = [s for s in sessions if str(s["pid"]) == target]
        if hit:
            return hit[0]
    hit = [s for s in sessions if (s.get("name") or "").startswith(target)]
    if len(hit) == 1:
        return hit[0]
    raise SystemExit(f"ccherd: no unique live session {target!r} ({len(hit)} match) - see `ccherd sessions`")


def own_address() -> str:
    """The address a receiver should reply to: this session's own inbox socket."""
    sock = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")
    if sock:
        return f"uds:{sock}"
    return f"ccherd:{os.environ.get('CCHERD_AGENT_NAME') or os.getpid()}"


def _token(cfg: str | Path, pid: int) -> str | None:
    for key in (Path(cfg) / "sessions").glob(f"{pid}.*.key"):
        try:
            return json.loads(key.read_text())["peerToken"]
        except (OSError, ValueError, KeyError):
            continue
    return None


def wrap(text: str, sender: str, mode: str | None = None) -> str:
    # Attribute order is fixed: the receiver re-renders the envelope from its parsed
    # fields and ignores the attributes unless the result is byte-identical.
    attrs = f' from="{sender}"' + (f' from-mode="{mode}"' if mode else "")
    return f"<cross-session-message{attrs}>\n{text}\n</cross-session-message>"


def deliver(session: dict, text: str, sender: str | None = None, mode: str | None = None) -> None:
    """Write one message into a live session's inbox.

    `mode` is the permission class the SENDER asserts. A receiver that bypasses
    permissions holds a message asserting none (or another class) for its user's
    approval, and a headless `claude -p` has no user, so it drops it. Assert only a
    class the sender really has.
    """
    sender = sender or own_address()
    token = _token(session["config_dir"], int(session["pid"]))
    if not token:
        raise RuntimeError(f"no inbox key for pid {session['pid']}")
    frame = {"msg_id": str(uuid.uuid4()), "type": "user",
             "message": {"role": "user", "content": wrap(text, sender, mode)},
             "priority": "next", "from": sender}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect(session["messagingSocketPath"])
        s.sendall((json.dumps({"type": "auth", "token": token}) + "\n").encode())
        s.sendall((json.dumps(frame) + "\n").encode())
