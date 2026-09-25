"""Shared config between accounts: one primary dir, the others symlink into it.

A subagent should behave like its caller: same skills, memories, agents, plugins
and settings. `projects` holds the memories and every conversation, so sharing it
also lets an agent be resumed from any account.

`plan` says what is out of place; `apply` fixes it. Nothing is deleted: an
account's own copy is merged into the primary, and whatever could not be merged
(two different files with the same name) stays in a backup next to it.
"""

from __future__ import annotations

import filecmp
import json
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .config import Account, tilde

SHARED = ["skills", "projects", "agents", "plugins", "settings.json", "CLAUDE.md"]

LINKED, MISSING, OWN_COPY, WRONG_LINK = "linked", "missing", "own copy", "links elsewhere"


@dataclass
class Item:
    account: Account
    name: str
    state: str

    @property
    def path(self) -> Path:
        return self.account.dir / self.name


def primary(accounts: list[Account]) -> Account:
    """The account the others already link to most, else the first one."""
    votes: Counter[Path] = Counter()
    for a in accounts:
        for name in SHARED:
            p = a.dir / name
            if p.is_symlink():
                votes[p.resolve().parent] += 1
    for target, _ in votes.most_common():
        for a in accounts:
            if a.dir.resolve() == target:
                return a
    return accounts[0]


def state(item_path: Path, target: Path) -> str | None:
    """How `item_path` relates to the primary's `target`; None when neither exists."""
    if item_path.is_symlink():
        return LINKED if item_path.resolve() == target.resolve() else WRONG_LINK
    if item_path.exists():
        return OWN_COPY
    return MISSING if target.exists() else None


def plan(accounts: list[Account]) -> tuple[Account, list[Item]]:
    """The primary, and every item of every other account with its state."""
    main = primary(accounts)
    items = []
    for a in accounts:
        if a == main:
            continue
        for name in SHARED:
            s = state(a.dir / name, main.dir / name)
            if s:
                items.append(Item(a, name, s))
    return main, items


# --- fixing ---------------------------------------------------------------------


def apply(main: Account, items: list[Item], log=print) -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for it in items:
        if it.state == LINKED:
            continue
        target = main.dir / it.name
        if it.state == OWN_COPY:
            backup = it.path.with_name(f"{it.name}.ccherd-backup-{stamp}")
            it.path.rename(backup)
            left = merge(backup, target)
            if left:
                log(f"  {it.account.label}/{it.name}: {left} differing file(s) kept in {tilde(backup)}")
        elif it.state == WRONG_LINK:
            it.path.unlink()
        it.path.symlink_to(target)
        log(f"  {it.account.label}/{it.name} -> {tilde(target)}")


def merge(src: Path, dst: Path) -> int:
    """Move what `src` has and `dst` lacks into `dst`. Returns how many items stay in
    `src` because `dst` has a different version; identical duplicates are removed."""
    if not dst.exists() and not dst.is_symlink():
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        return 0
    if src.is_dir() and dst.is_dir():
        left = 0
        for child in list(src.iterdir()):
            left += merge(child, dst / child.name)
        if left == 0:
            src.rmdir()
        return left
    if src.is_file() and dst.is_file():
        if filecmp.cmp(src, dst, shallow=False):
            src.unlink()
            return 0
        if dst.name == "settings.json" and _merge_json(src, dst):
            src.unlink()
            return 0
    return 1


def _merge_json(src: Path, dst: Path) -> bool:
    """Fold src's settings into dst: dicts merge, lists are united, dst wins on conflicts."""
    try:
        a, b = json.loads(dst.read_text()), json.loads(src.read_text())
    except ValueError:
        return False
    dst.write_text(json.dumps(_deep_merge(a, b), indent=2) + "\n")
    return True


def _deep_merge(keep, add):
    if isinstance(keep, dict) and isinstance(add, dict):
        return {**{k: v for k, v in add.items() if k not in keep},
                **{k: _deep_merge(v, add[k]) if k in add else v for k, v in keep.items()}}
    if isinstance(keep, list) and isinstance(add, list):
        return keep + [x for x in add if x not in keep]
    return keep
