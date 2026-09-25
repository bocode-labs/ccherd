"""`ccherd setup`: pick the accounts, install the skill, optionally point CLAUDE.local.md at it.

Run inside the repo where ccherd should be used. Every step can be answered by a flag,
so setup also runs unattended (`--yes` takes the default for anything not given).
"""

from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from . import config, doctor, tui
from .config import Settings, schema_members, schema_of, tilde
from .credentials import is_logged_in
from .profile import fetch_profile, organization

SKILL_NAME = "ccherd"
BEGIN, END = "<!-- ccherd:begin -->", "<!-- ccherd:end -->"


def run(a: Namespace) -> int:
    if a.list:
        return list_candidates(a.json)
    missing = missing_answers(a)
    if missing and not tui.has_terminal():
        raise SystemExit("ccherd: no terminal to ask questions in, and these are not answered by flags: "
                         + ", ".join(missing) + ". Pass them, or --yes for the defaults. "
                         "`ccherd setup --list` shows what there is to choose from.")
    root = config.repo_root()
    if a.new or (_interactive(a) and not _has_dirs_already()):
        return _prepare_new_accounts(a.new or _ask_count())
    settings = _choose_accounts(a)
    config.save_settings(settings)
    accounts = settings.accounts()
    print(f"saved {tilde(config.CONFIG_FILE)}: {', '.join(a.label for a in accounts) or 'no accounts'}")

    for path in install_skill(_choose_skill_target(a, accounts), root, accounts):
        print(f"wrote {tilde(path)}")

    if _choose_claude_local(a):
        path, ignored_now = write_claude_local(root)
        print(f"wrote {tilde(path)}" + (" and added it to .gitignore" if ignored_now else ""))

    print()
    if not doctor.report(root):
        print("Fix the problems above, then `ccherd doctor` checks again.")
        return 1
    return 0


def missing_answers(a: Namespace) -> list[str]:
    """Flags that would otherwise become questions. With --new, setup stops after step 0."""
    if a.yes or a.new:
        return []
    missing = [] if (a.dir or a.schema) else ["--dir/--schema (or --new N)"]
    if a.skill is None:
        missing.append("--skill repo|home|none")
    if a.claude_local is None:
        missing.append("--claude-local/--no-claude-local")
    return missing


def candidates_info() -> list[dict]:
    """Every found config dir: login, organization, numbered family, and whether it is the current one."""
    current = config.current_dir().resolve()
    out = []
    for p in config.discover_dirs():
        logged_in = is_logged_in(p)
        org = organization(fetch_profile(p)) if logged_in else None
        family = schema_of(p)
        out.append({"dir": tilde(p), "logged_in": logged_in, "organization": org["name"] if org else None,
                    "family": tilde(family) + "*" if len(schema_members(family)) > 1 else None,
                    "current": p.resolve() == current})
    return out


def list_candidates(as_json: bool) -> int:
    rows = candidates_info()
    if as_json:
        print(json.dumps(rows, indent=1))
        return 0
    for r in rows:
        print(f"{r['dir']:22} {'logged in' if r['logged_in'] else 'not logged in':14} "
              f"{r['organization'] or '-':36} {r['family'] or '':18}{'  <- current' if r['current'] else ''}")
    return 0


def _interactive(a: Namespace) -> bool:
    return not (a.dir or a.schema or a.yes)


# --- step 0: accounts that do not exist yet -------------------------------------


def _has_dirs_already() -> bool:
    return tui.select("Do you already have a Claude config dir for each of your subscriptions?", ["yes", "no"],
                      flag="--new N (no dirs yet) or --dir/--schema") == 0


def _ask_count() -> int:
    while True:
        raw = tui.ask("How many Claude subscriptions do you have?", flag="--new N")
        if raw.isdigit() and int(raw) >= 1:
            return int(raw)
        print("please enter a whole number, 1 or more")


def new_account_dirs(count: int, base: Path) -> list[Path]:
    """`count` dirs numbered after `base`: the base itself first, then base-2, base-3, ..."""
    family = schema_of(base)
    return [family] + [family.with_name(f"{family.name}-{n}") for n in range(2, count + 1)]


def _prepare_new_accounts(count: int) -> int:
    dirs = new_account_dirs(count, config.current_dir())
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    config.save_settings(Settings(dirs=[], schemas=[dirs[0]]))
    print(f"saved {tilde(config.CONFIG_FILE)}: {', '.join(tilde(d) for d in dirs)}")
    todo = [d for d in dirs if not is_logged_in(d)]
    if not todo:
        print("All of them are logged in already. Run `ccherd setup` again to finish.")
        return 0
    print("\nLog in to each subscription with its own dir - one terminal command each, then /login if asked:\n")
    for d in todo:
        print(f"  {doctor.login_command(d)}")
    print("\nThen run `ccherd setup` again.")
    return 0


# --- step 1: accounts ---------------------------------------------------------


@dataclass
class Choice:
    """One line of the account picker: a single dir, or a numbered family saved as a schema."""

    path: Path
    is_schema: bool
    indent: bool = False

    def members(self) -> list[Path]:
        return schema_members(self.path) if self.is_schema else [self.path]

    def short(self) -> str:
        return tilde(self.path) + ("*" if self.is_schema else "")

    def describe(self) -> str:
        if self.is_schema:
            return f"{self.short()}  all of them, and numbered dirs added later"
        status = "" if is_logged_in(self.path) else "  (not logged in)"
        return ("  " if self.indent else "") + self.short() + status


def candidates(found: list[Path]) -> list[Choice]:
    """Found dirs. A family of two or more numbered dirs gets a schema line with its members below."""
    families: dict[Path, list[Path]] = {}
    for p in found:
        families.setdefault(schema_of(p), []).append(p)
    out = []
    for schema, members in families.items():
        if len(members) > 1:
            out.append(Choice(schema, is_schema=True))
            out.extend(Choice(p, is_schema=False, indent=True) for p in members)
        else:
            out.append(Choice(members[0], is_schema=False))
    return out


OTHER = "Other"


def _choose_accounts(a: Namespace) -> Settings:
    found = config.discover_dirs()
    orgs, orgs_by_dir = _choose_organizations(a, found)
    if a.dir or a.schema:
        return Settings(dirs=[Path(d).expanduser() for d in a.dir or []],
                        schemas=[Path(s).expanduser() for s in a.schema or []], organizations=orgs)
    if orgs:
        keep = {o["uuid"] for o in orgs}
        other = [p for p in found if orgs_by_dir.get(p) and orgs_by_dir[p]["uuid"] not in keep]
        for p in other:
            print(f"not listed: {tilde(p)} ({orgs_by_dir[p]['name']})")
        found = [p for p in found if p not in other]
    choices = candidates(found)
    previous = config.load_settings() if config.is_configured() else None
    checked = [_was_chosen(c, previous) for c in choices]
    if a.yes:
        picked, typed = [i for i, on in enumerate(checked) if on], ""
    else:
        picked, typed = tui.checkbox("Your Claude accounts", [c.describe() for c in choices], checked,
                                     short=[c.short() for c in choices], other=OTHER, flag="--dir/--schema")
    settings = Settings(dirs=[choices[i].path for i in picked if not choices[i].is_schema],
                        schemas=[choices[i].path for i in picked if choices[i].is_schema], organizations=orgs)
    for item in typed.split():
        path = Path(item.rstrip("*")).expanduser()
        if not path.is_dir():
            print(f"skipped {item}: no such directory")
            continue
        (settings.schemas if item.endswith("*") else settings.dirs).append(path)
    return settings


def _choose_organizations(a: Namespace, found: list[Path]) -> tuple[list[dict] | None, dict[Path, dict | None]]:
    """The organizations whose seats ccherd uses (None: any), and the organization of every found dir.

    A private plan next to a company team usually has other skills and memories,
    so ccherd should not mix them. But several private plans are several
    organizations too (each "<name>'s Organization"), so the choice is a set.
    """
    orgs_by_dir = {p: organization(fetch_profile(p)) for p in found if is_logged_in(p)}
    orgs = {o["uuid"]: o for o in orgs_by_dir.values() if o}
    if a.organization:
        names = set(a.organization)
        match = [o for o in orgs.values() if o["name"] in names]
        unknown = names - {o["name"] for o in match}
        if unknown:
            have = ", ".join(sorted(o["name"] for o in orgs.values())) or "none"
            raise SystemExit(f"ccherd: no logged-in dir belongs to {', '.join(sorted(unknown))} (found: {have})")
        return match, orgs_by_dir
    if a.dir or a.schema:
        return None, orgs_by_dir  # dirs chosen by hand: no filter on top
    if len(orgs) <= 1:
        return (list(orgs.values()) or None), orgs_by_dir
    ordered = list(orgs.values())
    current = orgs_by_dir.get(config.current_dir())
    checked = [bool(current) and o["uuid"] == current["uuid"] for o in ordered]
    if not any(checked):
        checked[0] = True
    if not _interactive(a):
        return [o for o, on in zip(ordered, checked) if on], orgs_by_dir
    labels = [f"{o['name']}  ({', '.join(tilde(p) for p, x in orgs_by_dir.items() if x and x['uuid'] == o['uuid'])})"
              for o in ordered]
    picked, _ = tui.checkbox("Which organizations should ccherd use? (several private plans: tick each)", labels,
                             checked, short=[o["name"] for o in ordered], flag="--organization NAME (repeatable)")
    if not picked:
        raise SystemExit("ccherd: no organization chosen - nothing to set up")
    return [ordered[i] for i in picked], orgs_by_dir


def _was_chosen(c: Choice, previous: Settings | None) -> bool:
    """First run: every family and every lone dir that is logged in; members stay unticked.
    Later runs: whatever was saved."""
    if previous is None:
        return not c.indent and any(is_logged_in(m) for m in c.members())
    pool = previous.schemas if c.is_schema else previous.dirs
    return any(p.resolve() == c.path.resolve() for p in pool)


# --- step 2: the skill ----------------------------------------------------------


SKILL_TARGETS = ["repo", "home", "none"]


def _choose_skill_target(a: Namespace, accounts: list[config.Account]) -> str:
    if a.skill:
        return a.skill
    if a.yes:
        return "repo"
    labels = ["this repo  (.claude/skills/ccherd, shared via git)",
              f"my accounts  ({describe_skill_homes(accounts)}; all repos, only me)",
              "nowhere"]
    return SKILL_TARGETS[tui.select("Install the Claude skill in", labels, flag="--skill repo|home|none")]


def skill_homes(accounts: list[config.Account]) -> dict[Path, list[str]]:
    """Each distinct skills dir the accounts use (symlinks followed), with the accounts using it."""
    homes: dict[Path, list[str]] = {}
    for a in accounts:
        homes.setdefault((a.dir / "skills").resolve(), []).append(a.label)
    return homes


def describe_skill_homes(accounts: list[config.Account]) -> str:
    parts = []
    for home, labels in skill_homes(accounts).items():
        owner = next((a.label for a in accounts if (a.dir / "skills").resolve() == home
                      and not (a.dir / "skills").is_symlink()), labels[0])
        others = [label for label in labels if label != owner]
        parts.append(f"{tilde(home / SKILL_NAME)}" + (f", {', '.join(others)} link to it" if others else ""))
    return "; ".join(parts)


def skill_text() -> str:
    return files("ccherd").joinpath("data/SKILL.md").read_text()


def install_skill(target: str, root: Path, accounts: list[config.Account]) -> list[Path]:
    if target == "none":
        return []
    dirs = [root / ".claude" / "skills"] if target == "repo" else list(skill_homes(accounts))
    written = []
    for skills in dirs:
        path = skills / SKILL_NAME / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(skill_text())
        written.append(path)
    return written


# --- step 3: CLAUDE.local.md --------------------------------------------------


def _choose_claude_local(a: Namespace) -> bool:
    if a.claude_local is not None:
        return a.claude_local
    if a.yes:
        return False
    return tui.confirm("Add a ccherd note to CLAUDE.local.md (git-ignored)?", flag="--claude-local/--no-claude-local")


def claude_local_block() -> str:
    body = files("ccherd").joinpath("data/claude-local.md").read_text().strip()
    return f"{BEGIN}\n{body}\n{END}\n"


def write_claude_local(root: Path) -> tuple[Path, bool]:
    """Insert or replace the ccherd block; returns (path, whether .gitignore was changed)."""
    path = root / "CLAUDE.local.md"
    text = path.read_text() if path.is_file() else ""
    block = claude_local_block()
    if BEGIN in text and END in text:
        head, rest = text.split(BEGIN, 1)
        text = head + block + rest.split(END, 1)[1].lstrip("\n")
    else:
        text = (text.rstrip() + "\n\n" if text.strip() else "") + block
    path.write_text(text)
    return path, ensure_ignored(root, "CLAUDE.local.md")


def ensure_ignored(root: Path, name: str) -> bool:
    """Append `name` to .gitignore unless git already ignores it. No-op outside a git repo."""
    check = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", name], capture_output=True, check=False)
    if check.returncode != 1:  # 0: already ignored, 128: not a git repo
        return False
    gitignore = root / ".gitignore"
    text = gitignore.read_text() if gitignore.is_file() else ""
    gitignore.write_text(text + ("" if not text or text.endswith("\n") else "\n") + name + "\n")
    return True
