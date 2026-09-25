"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, agents, commands, doctor, setup
from .permissions import MODES


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ccherd", description="Claude Code sessions and subagents across several Claude accounts.")
    p.add_argument("--version", action="version", version=f"ccherd {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    s = sub.add_parser("setup", help="choose accounts, install the skill (run inside your repo)",
                       epilog="Without a terminal (an agent's shell) setup asks nothing: answer with the flags "
                              "above. Start with --list to see what there is. Full walkthrough: "
                              "https://github.com/bocode-labs/ccherd/blob/main/docs/agent-setup.md")
    s.add_argument("--list", action="store_true", help="show found config dirs, logins and organizations; change nothing")
    s.add_argument("--json", action="store_true", help="with --list: machine-readable")
    s.add_argument("--dir", action="append", metavar="PATH", help="a config dir to use (repeatable; skips the picker)")
    s.add_argument("--schema", action="append", metavar="PATH",
                   help="use PATH and every numbered sibling, e.g. ~/.claude-work (repeatable; skips the picker)")
    s.add_argument("--organization", metavar="NAME",
                   help="use only accounts of this organization (as `ccherd doctor` names it)")
    s.add_argument("--skill", choices=setup.SKILL_TARGETS, help="where to install the skill")
    s.add_argument("--claude-local", action=argparse.BooleanOptionalAction, default=None,
                   help="add a ccherd note to CLAUDE.local.md")
    s.add_argument("--new", type=int, metavar="N",
                   help="you have N subscriptions but no config dirs yet: create them and print how to log in")
    s.add_argument("-y", "--yes", action="store_true", help="take the default for every question not answered by a flag")
    s.set_defaults(fn=setup.run)

    s = sub.add_parser("doctor", help="check claude CLI, logins, shared config and skill")
    s.add_argument("--fix", action="store_true", help="link each account's skills, memories etc. to the primary")
    s.add_argument("-y", "--yes", action="store_true", help="with --fix: do not ask")
    s.set_defaults(fn=doctor.run)

    s = sub.add_parser("accounts", help="usage and score per account")
    s.add_argument("--model", help="score against this model's weekly limit too")
    s.add_argument("--refresh", action="store_true", help="ignore the 60s cache")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=commands.accounts)

    s = sub.add_parser("sessions", help="live sessions of all accounts")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=commands.sessions)

    s = sub.add_parser("spawn", help="start a background subagent on the best account")
    s.add_argument("name")
    s.add_argument("task")
    s.add_argument("--model", required=True, help="e.g. opus, sonnet, haiku")
    s.add_argument("--effort", choices=agents.EFFORTS, help="claude's --effort for every turn of this agent")
    s.add_argument("--account", default="auto", help="auto (default) or a label from `ccherd accounts`")
    s.add_argument("--cwd", default=".")
    s.add_argument("--permission-mode", choices=MODES,
                   help="default: the calling session's current mode; only narrower modes are accepted")
    s.set_defaults(fn=commands.spawn)

    s = sub.add_parser("agents", help="this session's subagents")
    s.add_argument("--all", action="store_true", help="every session's subagents")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=commands.list_agents)

    s = sub.add_parser("send", help="message a subagent or any live session")
    s.add_argument("target", nargs="?", help="agent name, session name or uds: address")
    s.add_argument("text")
    s.add_argument("--parent", action="store_true", help="from inside a subagent: message the session that started it")
    s.set_defaults(fn=commands.send)

    s = sub.add_parser("result", help="a subagent's last answer in full")
    s.add_argument("name")
    s.set_defaults(fn=commands.result)

    s = sub.add_parser("log", help="a subagent's transcript")
    s.add_argument("name")
    s.add_argument("--tail", type=int, default=0)
    s.set_defaults(fn=commands.log)

    s = sub.add_parser("kill", help="stop a subagent and everything it started")
    s.add_argument("name")
    s.set_defaults(fn=commands.kill)

    s = sub.add_parser("_supervise")  # internal: the detached process that runs one agent's turns
    s.add_argument("dir")
    s.set_defaults(fn=lambda a: agents.supervise(Path(a.dir)))
    return p


def main(argv: list[str] | None = None) -> int:
    p = parser()
    a = p.parse_args(argv)
    if a.cmd == "send" and not a.parent and not a.target:
        p.error("send needs a TARGET (or --parent)")
    try:
        return a.fn(a)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
