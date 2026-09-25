"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, agents, commands, doctor, setup
from .permissions import MODES


class FullHelp(argparse.Action):
    """`ccherd --help`: every command with all its arguments, generated from the parsers themselves."""

    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings, dest=dest, default=default, nargs=0, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        print(full_help(parser))
        parser.exit()


def full_help(p: argparse.ArgumentParser) -> str:
    sub = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
    summaries = {c.dest: c.help for c in sub._choices_actions}
    out = [p.description, "", "Usage: ccherd COMMAND [ARGS]    (ccherd COMMAND --help shows one command)", ""]
    for name, sp in sub.choices.items():
        if name.startswith("_"):
            continue
        out.append(f"{name} - {summaries.get(name, '')}")
        body = sp.format_help().split("\n\n", 1)[1]  # drop sp's own usage block, keep the rest
        out.append("  " + sp.format_usage().removeprefix("usage: ").strip())
        lines = [line for line in body.strip("\n").splitlines()
                 if "show this help message" not in line and line.strip() != "-h, --help"]
        while lines and lines[-1].strip() in ("", "options:"):  # a command whose only option is -h
            lines.pop()
        out.extend("  " + line if line.strip() else "" for line in lines)
        out.append("")
    out.append("Setting up through an agent: https://raw.githubusercontent.com/bocode-labs/ccherd/main/docs/agent-setup.md")
    return "\n".join(out)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ccherd", add_help=False,
                                description="ccherd - Claude Code sessions and subagents across several Claude accounts.")
    p.add_argument("-h", "--help", action=FullHelp, help="every command and all its arguments")
    p.add_argument("--version", action="version", version=f"ccherd {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    s = sub.add_parser("setup", help="choose accounts, install the skill (run inside your repo)",
                       epilog="Without a terminal (an agent's shell) setup asks nothing: answer with the flags "
                              "above. Start with --list to see what there is. Full walkthrough: "
                              "https://raw.githubusercontent.com/bocode-labs/ccherd/main/docs/agent-setup.md")
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
    s.add_argument("--json", action="store_true", help="machine-readable")
    s.set_defaults(fn=commands.accounts)

    s = sub.add_parser("sessions", help="live sessions of all accounts")
    s.add_argument("--json", action="store_true", help="machine-readable")
    s.set_defaults(fn=commands.sessions)

    s = sub.add_parser("spawn", help="start a background subagent on the best account")
    s.add_argument("name", help="a name for the agent, unique within this session")
    s.add_argument("task", help="what it should do - it starts without any of your context")
    s.add_argument("--model", required=True, help="e.g. opus, sonnet, haiku")
    s.add_argument("--effort", choices=agents.EFFORTS, help="claude's --effort for every turn of this agent")
    s.add_argument("--account", default="auto", help="auto (default) or a label from `ccherd accounts`")
    s.add_argument("--cwd", default=".", help="directory it works in (default: here)")
    s.add_argument("--permission-mode", choices=MODES,
                   help="default: the calling session's current mode; only narrower modes are accepted")
    s.set_defaults(fn=commands.spawn)

    s = sub.add_parser("agents", help="this session's subagents")
    s.add_argument("--all", action="store_true", help="every session's subagents")
    s.add_argument("--json", action="store_true", help="machine-readable")
    s.set_defaults(fn=commands.list_agents)

    s = sub.add_parser("send", help="message a subagent or any live session")
    s.add_argument("target", nargs="?", help="agent name, session name or uds: address")
    s.add_argument("text", help="the message")
    s.add_argument("--parent", action="store_true", help="from inside a subagent: message the session that started it")
    s.set_defaults(fn=commands.send)

    s = sub.add_parser("result", help="a subagent's last answer in full")
    s.add_argument("name", help="the agent's name")
    s.set_defaults(fn=commands.result)

    s = sub.add_parser("log", help="a subagent's transcript")
    s.add_argument("name", help="the agent's name")
    s.add_argument("--tail", type=int, default=0, metavar="N", help="only the last N lines")
    s.set_defaults(fn=commands.log)

    s = sub.add_parser("kill", help="stop a subagent and everything it started")
    s.add_argument("name", help="the agent's name")
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
