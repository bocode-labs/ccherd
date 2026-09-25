"""`ccherd doctor [--fix]`: is everything in place for ccherd to work?

Prints a general block (claude CLI, config, organization, skill), then one block
per account with a line per check: login, organization, access token, seat, and
whether each shared item links to the primary account. `--fix` links what is not
linked yet, merging an account's own copy into the primary first.

Exit code 0 when all is well, 1 when something needs fixing, 3 when setup has
not run.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from argparse import Namespace
from dataclasses import dataclass, field
from pathlib import Path

from . import config, links, tui
from . import version as ccherd_version
from .config import Account, tilde
from .credentials import read_oauth
from .profile import fetch_profile, organization
from .sessions import sessions_in

OK, WARN, FAIL, SKIP = "ok", "warn", "FAIL", "skip"


@dataclass
class Check:
    status: str
    what: str
    detail: str


@dataclass
class AccountReport:
    account: Account
    checks: list[Check] = field(default_factory=list)
    seat: str | None = None

    def add(self, status: str, what: str, detail: str) -> None:
        self.checks.append(Check(status, what, detail))


def login_command(cfg: Path) -> str:
    return f"CLAUDE_CONFIG_DIR={tilde(cfg)} claude"


def check_account(account: Account) -> AccountReport:
    r = AccountReport(account)
    if not account.dir.is_dir():
        r.add(FAIL, "dir", f"missing - log in with: {login_command(account.dir)}")
        return r
    oauth = read_oauth(account.dir)
    if not oauth.get("accessToken"):
        r.add(FAIL, "login", f"not logged in - run: {login_command(account.dir)}")
        return r
    if oauth.get("refreshTokenExpiresAt") and oauth["refreshTokenExpiresAt"] / 1000 < time.time():
        r.add(FAIL, "login", f"expired - run: {login_command(account.dir)}")
        return r

    profile = fetch_profile(account.dir)  # renews an expired access token when it is safe to
    oauth = read_oauth(account.dir)
    left = oauth.get("expiresAt", 0) / 1000 - time.time()
    r.add(OK, "login", (profile or {}).get("account", {}).get("email") or f"yes ({oauth.get('subscriptionType')})")
    r.add(OK, "access token", f"valid for {left / 3600:.1f}h" if left > 0 else
          "expired - a session on this account renews it with its next request")
    org = organization(profile)
    if org:
        r.add(OK, "organization", org["name"])
        # Quota belongs to a seat: one user in one organization.
        r.seat = f"{profile['account'].get('uuid')}/{org['uuid']}"
    else:
        r.add(WARN, "organization", "unknown so far - checked once the access token is fresh")
    return r


def mark_duplicate_seats(reports: list[AccountReport]) -> None:
    seen: dict[str, str] = {}
    for r in reports:
        if not r.seat:
            continue
        if r.seat in seen:
            r.add(WARN, "seat", f"same seat as {seen[r.seat]} - its quota would count twice")
        else:
            seen[r.seat] = r.account.label


def add_link_checks(reports: list[AccountReport], main: Account, items: list[links.Item]) -> None:
    by_account = {r.account: r for r in reports}
    by_account[main].add(OK, "shared config", "primary - the other accounts link here")
    for it in items:
        target = tilde(main.dir / it.name)
        if it.state == links.LINKED:
            by_account[it.account].add(OK, it.name, f"-> {target}")
        else:
            by_account[it.account].add(WARN, it.name, f"{it.state}, not linked to {target} - `ccherd doctor --fix`")


def skill_locations(root: Path, accounts: list[Account]) -> list[Path]:
    places = [root / ".claude", *(a.dir for a in accounts)]
    return sorted({(p / "skills" / "ccherd" / "SKILL.md").resolve() for p in places
                   if (p / "skills" / "ccherd" / "SKILL.md").is_file()})


def skill_check(root: Path, accounts: list[Account]) -> Check:
    repo_skill = (root / ".claude" / "skills" / "ccherd" / "SKILL.md").resolve()
    found = skill_locations(root, accounts)
    if not found:
        return Check(WARN, "skill", "not installed - `ccherd setup`")
    where = [f"{tilde(p.parent)} ({'this repo' if p == repo_skill else 'all repos'})" for p in found]
    if len(found) > 1:
        return Check(WARN, "skill", "installed twice, one is enough: " + ", ".join(where))
    return Check(OK, "skill", where[0])


def claude_version() -> str | None:
    exe = shutil.which("claude")
    if not exe:
        return None
    r = subprocess.run([exe, "--version"], capture_output=True, check=False, text=True)
    return r.stdout.strip() or exe


def report(root: Path) -> bool:
    """Print every check; True when nothing needs fixing."""
    settings = config.load_settings()
    accounts = settings.accounts()

    general = []
    current, detail = ccherd_version.status()
    general.append(Check(WARN if current is False else OK, "ccherd", detail))
    version = claude_version()
    general.append(Check(OK if version else FAIL, "claude CLI", version or "not on PATH - install Claude Code"))
    general.append(Check(OK, "config", tilde(config.CONFIG_FILE)))
    org = settings.organization
    general.append(Check(OK if org else WARN, "organization",
                         org["name"] if org else "none chosen - every listed dir counts (`ccherd setup` asks)"))
    general.append(skill_check(root, accounts))
    if len(accounts) == 1:
        general.append(Check(WARN, "accounts", "only one - nothing to spread quota across"))
    if not accounts:
        general.append(Check(FAIL, "accounts", "none - run `ccherd setup`"))
    _block("general", general)
    if not accounts:
        return False

    reports = [check_account(a) for a in accounts]
    mark_duplicate_seats(reports)
    if len(accounts) > 1:
        add_link_checks(reports, *links.plan(accounts))
    for r in reports:
        _block(f"{r.account.label}  {tilde(r.account.dir)}", r.checks)
    for a in settings.all_accounts():
        if a not in accounts:
            print(f"\n{SKIP:4}  {tilde(a.dir)} belongs to another organization and is not used")

    checks = general + [c for r in reports for c in r.checks]
    fails, warns = sum(c.status == FAIL for c in checks), sum(c.status == WARN for c in checks)
    print(f"\n{fails} problem(s), {warns} warning(s)." if fails or warns else "\nAll good.")
    return fails == 0


def _block(title: str, checks: list[Check]) -> None:
    print(f"\n{tui.BOLD}{title}{tui.RESET}" if tui.color() else f"\n{title}")
    for c in checks:
        print(f"  {c.status:4}  {c.what:14} {c.detail}")


# --- --fix ------------------------------------------------------------------------


def fix(assume_yes: bool) -> int:
    accounts = config.load_accounts()
    if len(accounts) < 2:
        print("ccherd: nothing to link - fewer than two accounts")
        return 0
    main, items = links.plan(accounts)
    todo = [it for it in items if it.state != links.LINKED]
    busy = {it.account for it in todo if sessions_in(it.account.dir)}
    if busy:
        for a in sorted(busy, key=lambda a: a.label):
            print(f"skipped {a.label}: a Claude session is running on it - close it and run `ccherd doctor --fix` again")
        todo = [it for it in todo if it.account not in busy]
    if sessions_in(main.dir) and any(it.state == links.OWN_COPY for it in todo):
        print(f"skipped merging: a Claude session is running on the primary {main.label} - close it first")
        todo = [it for it in todo if it.state != links.OWN_COPY]
    if not todo:
        print("Nothing to fix." if not busy else "Nothing else to fix.")
        return 0

    print(f"Primary: {main.label} ({tilde(main.dir)}). Planned:")
    for it in todo:
        how = {links.MISSING: "link", links.WRONG_LINK: "relink",
               links.OWN_COPY: f"merge into {main.label}, then link"}[it.state]
        print(f"  {it.account.label}/{it.name}: {how}")
    if not assume_yes and not tui.confirm("Go ahead?", default=False, flag="--yes"):
        return 1
    links.apply(main, todo)
    return 0


def run(a: Namespace) -> int:
    if a.fix:
        return fix(a.yes)
    return 0 if report(config.repo_root()) else 1
