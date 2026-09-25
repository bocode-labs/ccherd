"""The subcommands, one function each. Argument parsing lives in cli.py."""

from __future__ import annotations

import datetime as dt
import json
import os
import signal
import sys
import time
from argparse import Namespace

from . import agents, config
from .agents import TERMINAL, locked
from .config import tilde
from .fmt import fmt_age, fmt_ts
from .permissions import caller_mode, check_not_wider, mode_class
from .profile import cached_profile, organization
from .sessions import deliver, find_session, live_sessions, session_by_socket
from .tui import RESET
from .usage import fetch_usage, pick_account, rank_accounts


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


LIMIT_NAMES = {"session": "5 hours", "weekly_all": "week"}
SEVERITY_COLOR = {"warning": "\x1b[33m", "critical": "\x1b[31m"}


def usage(a: Namespace) -> int:
    """Per account: every limit as a bar, and the extra usage."""
    rows = [(acct, fetch_usage(acct, refresh=a.refresh)) for acct in config.load_accounts()]
    if a.json:
        print(json.dumps([{"account": acct.label, **u} for acct, u in rows], indent=1))
        return 0
    for acct, u in rows:
        org = organization(cached_profile(acct.dir))
        print(f"\n{acct.label}  {tilde(acct.dir)}" + (f"  {org['name']}" if org else ""))
        data = u.get("usage")
        if u.get("error"):
            print(f"  {u['error']}" + (f" - last reading {fmt_ts(u.get('stale_since'))}" if data else ""))
        if not data:
            continue
        for name, percent, severity, resets in limit_rows(data):
            color = SEVERITY_COLOR.get(severity, "") if sys.stdout.isatty() else ""
            reset = f"resets {fmt_ts(resets)}" if resets else ""
            print(f"  {name:13} {color}{bar(percent)} {percent:>3.0f}%{RESET if color else ''}  {reset}")
        print(f"  {'extra usage':13} {extra_usage(data)}")
    return 0


def limit_rows(data: dict) -> list[tuple[str, float, str, float | None]]:
    """(name, percent, severity, reset time) for each limit the plan has."""
    out = []
    for lim in data.get("limits") or []:
        model = (((lim.get("scope") or {}).get("model") or {}).get("display_name"))
        name = LIMIT_NAMES.get(lim.get("kind")) or (f"week {model}" if model else lim.get("kind", "?"))
        out.append((name, float(lim.get("percent") or 0), lim.get("severity") or "", _ts(lim.get("resets_at"))))
    if out:
        return out
    for key, name in (("five_hour", "5 hours"), ("seven_day", "week")):  # readings without `limits`
        if data.get(key):
            out.append((name, float(data[key].get("utilization") or 0), "", _ts(data[key].get("resets_at"))))
    return out


def extra_usage(data: dict) -> str:
    extra, spend = data.get("extra_usage") or {}, data.get("spend") or {}
    if not extra.get("is_enabled"):
        return "off"
    used = spend.get("used") or {}
    if used.get("amount_minor") is None:
        return "on"
    amount = used["amount_minor"] / 10 ** (used.get("exponent") or 0)
    return f"on - {amount:,.2f} {used.get('currency', '')} used so far"


def bar(percent: float, width: int = 20) -> str:
    filled = round(min(max(percent, 0), 100) / 100 * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def _ts(s: str | None) -> float | None:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None


def settings(a: Namespace) -> int:
    """`ccherd config [KEY [VALUE]]`: show or change a setting."""
    config.load_settings()  # not set up -> exit 3 like every other command
    if a.key and a.value is not None:
        print(f"{a.key} = {config.set_policy(a.key, a.value)}")
        return 0
    pol = config.policy()
    for key in ([a.key] if a.key else config.POLICY_DEFAULTS):
        if key not in pol:
            raise SystemExit(f"ccherd: unknown setting {key!r} (have: {', '.join(config.POLICY_DEFAULTS)})")
        mark = "" if pol[key] == config.POLICY_DEFAULTS[key] else "   (changed)"
        print(f"{key:16} {pol[key]!s:8} {config.POLICY_HELP[key]}{mark}")
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
