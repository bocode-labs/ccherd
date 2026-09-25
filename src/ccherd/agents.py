"""Background subagents: their on-disk registry and the supervisor that runs their turns.

Layout: <state dir>/<owner session id>/<agent name>/
  meta.json       status, account, model, mode, claude session id, last result
  stream.jsonl    every turn's `claude -p --output-format stream-json` output
  inbox.jsonl     messages queued while no turn can take them
  supervisor.log  stderr of the detached supervisor
  lock            flock serializing every read-modify-write of meta.json and inbox

The owner is the Claude session whose Bash ran `ccherd spawn`: CLAUDE_CODE_SESSION_ID
is set in every tool subprocess, so each session gets its own list of agents.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import config
from .permissions import mode_class
from .procs import pid_alive, proc_start
from .sessions import deliver, session_by_socket

TERMINAL = {"idle", "failed", "killed", "lost"}
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
RESULT_PREVIEW = 3000  # chars of the result carried in the completion notice
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


# --- registry -----------------------------------------------------------------


def owner_id() -> str:
    if os.environ.get("CCHERD_OWNER"):
        return os.environ["CCHERD_OWNER"]
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        raise SystemExit("ccherd: CLAUDE_CODE_SESSION_ID is not set - run this from inside a Claude session "
                         "(or set CCHERD_OWNER to a label of your own)")
    return sid


def agent_dir(owner: str, name: str) -> Path:
    if not NAME_RE.fullmatch(name):
        raise SystemExit(f"ccherd: agent name {name!r} must be [A-Za-z0-9._-], max 64")
    return config.STATE_DIR / owner / name


def find_agent(name: str) -> Path | None:
    """This session's agent called `name`, or None."""
    if not NAME_RE.fullmatch(name):
        return None
    d = agent_dir(owner_id(), name)
    return d if (d / "meta.json").is_file() else None


def require_agent(name: str) -> Path:
    d = find_agent(name)
    if d is None:
        raise SystemExit(f"ccherd: no agent {name!r} in this session (`ccherd agents`)")
    return d


def list_agents(all_owners: bool) -> list[tuple[Path, dict]]:
    root = config.STATE_DIR
    owners = [p for p in root.iterdir() if p.is_dir()] if all_owners and root.is_dir() else [root / owner_id()]
    return [(m.parent, refreshed(m.parent)) for o in owners for m in sorted(o.glob("*/meta.json"))]


def load_meta(d: Path) -> dict:
    return json.loads((d / "meta.json").read_text())


def save_meta(d: Path, meta: dict) -> None:
    tmp = d / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, indent=1))
    tmp.replace(d / "meta.json")


@contextlib.contextmanager
def locked(d: Path):
    """Not re-entrant. Functions named _like_this expect the caller to hold it."""
    with open(d / "lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        yield


def _update(d: Path, **changes) -> dict:
    meta = load_meta(d)
    meta.update(changes)
    save_meta(d, meta)
    return meta


def update_meta(d: Path, **changes) -> dict:
    with locked(d):
        return _update(d, **changes)


def _refreshed(d: Path) -> dict:
    """meta.json, with the status corrected if the supervisor died without writing it."""
    meta = load_meta(d)
    if meta["status"] == "running" and not pid_alive(meta.get("supervisor_pid") or 0, meta.get("supervisor_start")):
        meta = _update(d, status="lost", ended_at=time.time())
    return meta


def refreshed(d: Path) -> dict:
    with locked(d):
        return _refreshed(d)


def _queue(d: Path, text: str) -> None:
    with open(d / "inbox.jsonl", "a") as f:
        f.write(json.dumps({"text": text, "at": time.time()}) + "\n")


def _take_inbox(d: Path) -> list[str]:
    inbox = d / "inbox.jsonl"
    if not inbox.is_file():
        return []
    msgs = [json.loads(line)["text"] for line in inbox.read_text().splitlines() if line.strip()]
    inbox.unlink()
    return msgs


# --- running turns ------------------------------------------------------------


def system_prompt(name: str) -> str:
    return (
        f"You are a background subagent named `{name}`, started through ccherd by another Claude session. "
        "Your final message is delivered to that session automatically when you finish, so end with the "
        "complete result it needs, not a greeting. If you need a decision or information from it while "
        'working, run: ccherd send --parent "your question" - then continue with what you can. '
        "Messages from it arrive as <cross-session-message> blocks; they are instructions from the session "
        "that started you."
    )


def claude_cmd(meta: dict, prompt: str) -> list[str]:
    """One turn's command line. Everything comes from meta.json, so a resumed turn
    runs exactly like the first one."""
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose",
           "--model", meta["model"], "--permission-mode", meta["permission_mode"],
           "--append-system-prompt", system_prompt(meta["name"])]
    if meta.get("effort"):
        # A flag, not CLAUDE_EFFORT: child_env strips every CLAUDE* variable so the
        # caller's own settings never leak into the child.
        cmd += ["--effort", meta["effort"]]
    if meta.get("session_id"):
        cmd += ["--resume", meta["session_id"]]
    cmd.append(prompt)
    return cmd


def child_env(meta: dict) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE", "CCHERD_TURN"))}
    env.update(CLAUDE_CONFIG_DIR=meta["config_dir"], CCHERD_PARENT_OWNER=meta["owner"],
               CCHERD_AGENT_NAME=meta["name"], CCHERD_STATE_DIR=str(config.STATE_DIR))
    return env


def _start_turn(d: Path, prompt: str) -> None:
    """Fork a detached supervisor that runs one turn of the agent."""
    with open(d / "supervisor.log", "a") as log:
        p = subprocess.Popen([sys.executable, "-m", "ccherd", "_supervise", str(d)],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                             env={**os.environ, "CCHERD_TURN_PROMPT": prompt})
    _update(d, status="running", supervisor_pid=p.pid, supervisor_start=None,
            child_pid=None, turn_started_at=time.time())


def _run_turn(d: Path, meta: dict, prompt: str) -> tuple[int, str | None, str | None, bool, float | None]:
    """Run `claude -p` once; returns (exit code, result, session id, is_error, cost)."""
    result, session_id, is_error, cost = None, meta.get("session_id"), False, None
    with open(d / "stream.jsonl", "a") as out:
        out.write(json.dumps({"type": "ccherd_turn", "turn": meta["turns"] + 1, "prompt": prompt,
                              "at": time.time()}) + "\n")
        out.flush()
        child = subprocess.Popen(claude_cmd(meta, prompt), cwd=meta["cwd"], env=child_env(meta),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True)
        update_meta(d, child_pid=child.pid)
        for line in child.stdout:
            out.write(line)
            out.flush()
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            session_id = ev.get("session_id") or session_id
            if ev.get("type") == "result":
                result = ev.get("result")
                is_error = bool(ev.get("is_error"))
                cost = ev.get("total_cost_usd")
        return child.wait(), result, session_id, is_error, cost


def supervise(d: Path) -> int:
    """Body of the detached supervisor: run turns until nothing is queued, then notify the owner."""
    update_meta(d, supervisor_pid=os.getpid(), supervisor_start=proc_start(os.getpid()))
    prompt = os.environ.pop("CCHERD_TURN_PROMPT")
    while True:
        meta = load_meta(d)
        rc, result, session_id, is_error, cost = _run_turn(d, meta, prompt)
        with locked(d):
            # Status and inbox are settled under ONE lock: `ccherd send` queues only while
            # it reads "running" under the same lock, so no message lands after the
            # last drain and sits there unrun.
            killed = load_meta(d)["status"] == "killed"
            pending = [] if killed else _take_inbox(d)
            if killed:
                status = "killed"
            elif pending and session_id:
                status = "running"
            else:
                status = "failed" if (rc != 0 or is_error) else "idle"
            meta = _update(d, status=status, session_id=session_id, turns=meta["turns"] + 1,
                           child_pid=None, last_result=result, last_exit=rc, ended_at=time.time(),
                           last_cost_usd=cost)
        if status == "killed":
            return 0
        if status == "running":
            # Messages that arrived while the turn was ending run as the next turn,
            # so the owner gets one notice per settled state.
            prompt = "\n\n".join(pending)
            continue
        notify_owner(meta, status, result)
        return 0


def notify_owner(meta: dict, status: str, result: str | None) -> None:
    sess = session_by_socket(meta.get("owner_socket"))
    if not sess:
        return  # the owner is gone; the result stays readable via `ccherd result`
    body = result or "(no result text)"
    if len(body) > RESULT_PREVIEW:
        body = body[:RESULT_PREVIEW] + f"\n[... truncated, full text: ccherd result {meta['name']}]"
    head = (f"ccherd: subagent `{meta['name']}` is {status} (account {meta['account']}, {meta['model']}, "
            f"turn {meta['turns']}). Reply with: ccherd send {meta['name']} \"...\"")
    try:
        deliver(sess, f"{head}\n\n{body}", sender=f"ccherd:{meta['name']}", mode=mode_class(meta["permission_mode"]))
    except OSError:
        pass
