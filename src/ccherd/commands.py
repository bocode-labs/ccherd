"""The subcommands, one function each. Argument parsing lives in cli.py."""

from __future__ import annotations

import json
import os
import signal
import time
from argparse import Namespace

from . import agents, config
from .agents import TERMINAL, locked
from .fmt import fmt_age, fmt_ts
from .permissions import caller_mode, check_not_wider, mode_class
from .sessions import deliver, find_session, live_sessions, session_by_socket
from .usage import pick_account, rank_accounts


def _own_account() -> str | None:
    cur_path = config.current_dir().resolve()
    return next((a.label for a in config.load_accounts() if a.dir.resolve() == cur_path), None)


def _account(label: str) -> config.Account:
    accounts = config.load_accounts()
    for a in accounts:
        if a.label == label:
            return a
    raise SystemExit(f"ccherd: no account {label!r} (have: {', '.join(a.label for a in accounts)})")


def accounts(a: Namespace) -> int:
    rows = rank_accounts(a.model, refresh=a.refresh)
    if a.json:
        print(json.dumps(rows, indent=1))
        return 0
    me = _own_account()
    width = max([len(r["account"]) + 1 for r in rows] + [8])
    print(f"{'account':{width}} {'5h':>5} {'5h reset':>16} {'week':>5} {'week reset':>16} {'%/h':>6} {'score':>6}  note")
    for i, r in enumerate(rows):
        pick = " <- auto" if i == 0 and r["score"] is not None else ""
        label = r["account"] + ("*" if r["account"] == me else "")
        sc = "-" if r["score"] is None else f"{r['score']:.2f}"
        print(f"{label:{width}} {r.get('five_hour', 0):>4.0f}% {fmt_ts(r.get('five_hour_reset')):>16} "
              f"{r.get('weekly', 0):>4.0f}% {fmt_ts(r.get('weekly_reset')):>16} {r.get('rate', 0):>6.2f} "
              f"{sc:>6}  {r['reason']}{pick}")
    print("* = this session's account; score = weekly % left per hour until reset x free share of the 5h window")
    return 0


def sessions(a: Namespace) -> int:
    ss = live_sessions()
    if a.json:
        print(json.dumps(ss, indent=1))
        return 0
    mine = os.environ.get("CLAUDE_CODE_SESSION_ID")
    for s in sorted(ss, key=lambda s: (s["account"], s.get("name") or "")):
        me = " (this session)" if s.get("sessionId") == mine else ""
        print(f"[{s['account']}] {s.get('name') or '-':40} {s.get('status', '?'):5} "
              f"uds:{s.get('messagingSocketPath')}  {s.get('cwd', '')}{me}")
    return 0


def spawn(a: Namespace) -> int:
    owner = agents.owner_id()
    d = agents.agent_dir(owner, a.name)
    if (d / "meta.json").is_file():
        meta = agents.refreshed(d)
        state = "is" if meta["status"] not in TERMINAL else "exists,"
        raise SystemExit(f"ccherd: agent {a.name!r} {state} {meta['status']} - `ccherd send {a.name}` "
                         f"continues it, or pick another name")
    caller = caller_mode()
    mode = a.permission_mode or caller
    check_not_wider(mode, caller)
    account = _account(pick_account(a.model) if a.account == "auto" else a.account)
    cwd = os.path.abspath(a.cwd)
    d.mkdir(parents=True, mode=0o700)
    agents.save_meta(d, {
        "name": a.name, "owner": owner, "owner_socket": os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET"),
        "owner_account": _own_account(), "account": account.label, "config_dir": str(account.dir),
        "model": a.model, "effort": a.effort, "permission_mode": "manual" if mode == "default" else mode,
        "cwd": cwd, "created_at": time.time(), "status": "starting", "turns": 0, "session_id": None,
    })
    with locked(d):
        agents._start_turn(d, a.task)
    print(f"ccherd: started `{a.name}` on account {account.label} ({a.model}, effort {a.effort or 'default'}, "
          f"{mode}) in {cwd}.\n     A notice arrives in this session when it finishes; `ccherd agents` shows its state.")
    return 0


def list_agents(a: Namespace) -> int:
    rows = agents.list_agents(a.all)
    if a.json:
        print(json.dumps([m for _, m in rows], indent=1))
        return 0
    if not rows:
        print("no subagents" + ("" if a.all else " for this session (--all for every session's)"))
    for _, m in rows:
        last = (m.get("last_result") or "").strip().splitlines()
        owner = f" owner {m['owner'][:8]}" if a.all else ""
        print(f"{m['name']:20} {m['status']:8} {m['account']:8} {m['model']:10} {m.get('effort') or '-':6} "
              f"turns {m['turns']:<3} since {fmt_age(m.get('turn_started_at'))}{owner}  {last[0][:60] if last else ''}")
    return 0


def send(a: Namespace) -> int:
    if a.parent:
        return _send_to_parent(a.text)
    d = agents.find_agent(a.target)
    if d is None:
        return _send_to_session(a.target, a.text)
    return _send_to_agent(d, a.target, a.text)


def _send_to_parent(text: str) -> int:
    owner, name = os.environ.get("CCHERD_PARENT_OWNER"), os.environ.get("CCHERD_AGENT_NAME")
    if not (owner and name):
        raise SystemExit("ccherd: --parent works only inside a ccherd subagent")
    meta = agents.load_meta(agents.agent_dir(owner, name))
    sess = session_by_socket(meta.get("owner_socket"))
    if not sess:
        raise SystemExit("ccherd: the session that started you is no longer running")
    deliver(sess, f"ccherd: subagent `{name}` asks:\n\n{text}\n\nAnswer with: ccherd send {name} \"...\"",
            sender=f"ccherd:{name}", mode=mode_class(meta["permission_mode"]))
    print("sent to parent")
    return 0


def _send_to_session(target: str, text: str) -> int:
    # Assert THIS session's real current class. That is a true statement about the
    # sender, so it grants nothing; without it a receiver that bypasses permissions
    # holds the message for its user, and with it two bypass sessions talk directly.
    sess = find_session(target)
    mode = mode_class(caller_mode())
    deliver(sess, text, mode=mode)
    held = ("" if mode else " without a permission class (this session's mode is unreadable),"
            " so a receiver that bypasses permissions holds it for its user's approval")
    print(f"delivered to [{sess['account']}] {sess.get('name')}{held}. A receiver in a different permission "
          f"class holds it for approval; ccherd gets no receipt either way. "
          f"Replies arrive here as <cross-session-message from=\"uds:...\">.")
    return 0


def _send_to_agent(d, name: str, text: str) -> int:
    # The agent keeps the mode it was spawned with. If this session has been
    # narrowed since, it may no longer steer an agent that is allowed more.
    check_not_wider(agents.load_meta(d)["permission_mode"], caller_mode())
    with locked(d):
        meta = agents._refreshed(d)
        if meta["status"] not in ("running", "starting"):
            if not meta.get("session_id"):
                raise SystemExit(f"ccherd: `{name}` never got a session id (status {meta['status']}) - see `ccherd log`")
            agents._start_turn(d, text)
            print(f"resumed `{name}` on account {meta['account']} with your message")
            return 0
        child = next((s for s in live_sessions() if meta.get("child_pid") and s["pid"] == meta["child_pid"]), None)
        if child is None:
            agents._queue(d, text)
            print(f"`{name}` has no inbox open right now - queued, it runs as its next turn")
            return 0
    # The owner chose this mode at spawn, so asserting it grants nothing new.
    deliver(child, text, mode=mode_class(meta["permission_mode"]))
    print(f"sent into running `{name}` (it reads it at its next tool round)")
    return 0


def result(a: Namespace) -> int:
    m = agents.refreshed(agents.require_agent(a.name))
    print(m.get("last_result") or f"(no result yet - status {m['status']})")
    return 0


def log(a: Namespace) -> int:
    d = agents.require_agent(a.name)
    stream = d / "stream.jsonl"
    lines = stream.read_text().splitlines() if stream.is_file() else []
    for line in lines[-a.tail:] if a.tail else lines:
        try:
            ev = json.loads(line)
        except ValueError:
            print(line)
            continue
        kind = ev.get("type")
        if kind == "ccherd_turn":
            print(f"\n=== turn {ev['turn']}: {ev['prompt'][:200]}")
        elif kind == "assistant":
            for c in ev.get("message", {}).get("content", []):
                if c.get("type") == "text":
                    print(f"[assistant] {c['text']}")
                elif c.get("type") == "tool_use":
                    print(f"[tool] {c.get('name')} {json.dumps(c.get('input'))[:160]}")
        elif kind == "result":
            print(f"[result] {'ERROR ' if ev.get('is_error') else ''}{(ev.get('result') or '')[:300]}")
    return 0


def kill(a: Namespace) -> int:
    d = agents.require_agent(a.name)
    m = agents.refreshed(d)
    if m["status"] != "running":
        print(f"`{a.name}` is {m['status']}, nothing to stop")
        return 0
    agents.update_meta(d, status="killed")
    try:
        # The supervisor leads its own session, so its pid is the process group of
        # claude and every tool process claude started.
        os.killpg(m["supervisor_pid"], signal.SIGTERM)
    except (ProcessLookupError, KeyError, TypeError):
        pass
    print(f"stopped `{a.name}`")
    return 0
