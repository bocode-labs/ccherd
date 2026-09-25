"""Where ccherd keeps its files, and which Claude config dirs count as accounts."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HOME = Path.home()
STATE_DIR = Path(os.environ.get("CCHERD_STATE_DIR") or HOME / ".local/state/ccherd")
CACHE_DIR = Path(os.environ.get("CCHERD_CACHE_DIR") or HOME / ".cache/ccherd")
CONFIG_FILE = Path(
    os.environ.get("CCHERD_CONFIG")
    or Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "ccherd" / "config.json"
)
DEFAULT_CLAUDE_DIR = HOME / ".claude"

# Exit code of every command that needs `ccherd setup` first. The skill relies on it.
EXIT_NOT_SET_UP = 3


@dataclass(frozen=True)
class Account:
    label: str
    dir: Path


def label_for(path: Path) -> str:
    """~/.claude -> "default", ~/.claude-work -> "work", ~/.claude-2 -> "2", /x/foo -> "foo"."""
    name = path.name
    if name == ".claude":
        return "default"
    if name.startswith(".claude-"):
        return name[len(".claude-"):]
    return name.lstrip(".")


def discover_dirs() -> list[Path]:
    """Candidate config dirs in $HOME: ~/.claude and ~/.claude-*."""
    return [p for p in [DEFAULT_CLAUDE_DIR, *sorted(HOME.glob(".claude-*"))] if p.is_dir()]


def schema_of(path: Path) -> Path:
    """The numbered family a dir belongs to: ~/.claude-mpp3 -> ~/.claude-mpp."""
    return path.with_name(re.sub(r"-?\d+$", "", path.name) or path.name)


def schema_members(schema: Path) -> list[Path]:
    """The dir itself and every numbered sibling: ~/.claude-mpp, ~/.claude-mpp2, ~/.claude-mpp-3."""
    pattern = re.compile(re.escape(schema.name) + r"(-?\d+)?")
    parent = schema.parent
    found = [p for p in parent.glob(schema.name + "*") if p.is_dir() and pattern.fullmatch(p.name)]
    return sorted(found, key=lambda p: (len(p.name), p.name))


@dataclass
class Settings:
    """What `ccherd setup` saved: fixed dirs, schemas that pick up numbered dirs at run
    time, and the one organization ({"uuid", "name"}) whose seats count."""

    dirs: list[Path]
    schemas: list[Path]
    organization: dict | None = None

    def all_accounts(self) -> list[Account]:
        """Every listed dir, whichever organization it belongs to."""
        seen: dict[Path, Account] = {}
        for p in [*self.dirs, *(m for s in self.schemas for m in schema_members(s))]:
            p = p.expanduser()
            if p.is_dir() and p.resolve() not in seen:
                seen[p.resolve()] = Account(label_for(p), p)
        return list(seen.values())

    def accounts(self) -> list[Account]:
        """The listed dirs, minus those known to belong to another organization."""
        return [a for a in self.all_accounts() if self.belongs(a)]

    def belongs(self, account: Account) -> bool:
        from .profile import cached_profile, organization  # profile imports this module

        if not self.organization:
            return True
        org = organization(cached_profile(account.dir))
        return org is None or org["uuid"] == self.organization["uuid"]


def is_configured() -> bool:
    return CONFIG_FILE.is_file()


def load_settings() -> Settings:
    if not CONFIG_FILE.is_file():
        raise_not_set_up()
    data = json.loads(CONFIG_FILE.read_text())
    return Settings(dirs=[Path(d).expanduser() for d in data.get("dirs", [])],
                    schemas=[Path(s).expanduser() for s in data.get("schemas", [])],
                    organization=data.get("organization"))


def load_accounts() -> list[Account]:
    return load_settings().accounts()


def save_settings(settings: Settings) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {"dirs": [tilde(p) for p in settings.dirs], "schemas": [tilde(p) for p in settings.schemas],
            "organization": settings.organization}
    CONFIG_FILE.write_text(json.dumps(data, indent=2) + "\n")


def raise_not_set_up() -> None:
    print("ccherd: not set up - run `ccherd setup` first", file=sys.stderr)
    raise SystemExit(EXIT_NOT_SET_UP)


def tilde(p: Path) -> str:
    try:
        return "~/" + str(p.resolve().relative_to(HOME.resolve()))
    except ValueError:
        return str(p)


def current_dir() -> Path:
    """The config dir of the account this process runs under."""
    cur = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(cur).expanduser() if cur else DEFAULT_CLAUDE_DIR


def repo_root() -> Path:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, check=False, text=True)
    return Path(r.stdout.strip()) if r.returncode == 0 else Path.cwd()
