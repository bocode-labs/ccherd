"""Small interactive prompts for `ccherd setup`, stdlib only.

On a terminal the lists are arrow-key menus drawn in place below the cursor
(no full-screen mode, so they behave the same in tmux, IDE terminals and SSH).
Without a terminal they fall back to numbered prompts on stdin.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
import termios
import tty

UP = {b"\x1b[A", b"\x1bOA", b"k"}
DOWN = {b"\x1b[B", b"\x1bOB", b"j"}
ENTER = {b"\r", b"\n"}
ABORT = {b"\x03", b"\x1b", b"q"}

BOLD, DIM, REVERSE, CYAN, RESET = "\x1b[1m", "\x1b[2m", "\x1b[7m", "\x1b[36m", "\x1b[0m"


def _tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def color() -> bool:
    return sys.stdout.isatty()


def checkbox(title: str, options: list[str], checked: list[bool], short: list[str] | None = None,
             other: str | None = None) -> tuple[list[int], str]:
    """Indexes of the options the user ticked, and the text typed into the `other` line.

    With `other` set, the list ends in a line the user types into directly; typing
    ticks it. `short` names the options in the one-line summary.
    """
    if not _tty():
        return _checkbox_plain(title, options, checked, other)
    checked = list(checked) + ([False] if other else [])
    text = ""
    last = len(options)  # index of the `other` line, if any

    def lines(cursor: int) -> list[str]:
        rows = [f"{'>' if i == cursor else ' '} [{'x' if on else ' '}] {opt}"
                for i, (opt, on) in enumerate(zip(options, checked))]
        if other:
            field = text + ("_" if cursor == last else "")
            rows.append(f"{'>' if cursor == last else ' '} [{'x' if checked[last] else ' '}] {other}: {field}")
        return rows

    def on_key(key: bytes, cursor: int) -> str | None:
        nonlocal text
        if other and cursor == last:
            if key in (b"\x7f", b"\x08"):
                text = text[:-1]
            elif key.isascii() and key.decode().isprintable():
                text += key.decode()
            else:
                return "done" if key in ENTER else None
            checked[last] = bool(text.strip())
            return "handled"
        if key == b" ":
            checked[cursor] = not checked[cursor]
        elif key == b"a":
            checked[:last] = [not all(checked[:last])] * last
        return "done" if key in ENTER else "handled" if key in (b" ", b"a") else None

    hint = "up/down move, space toggle, a all, enter confirm" + (
        f"; {other}: type paths, trailing * = numbered family" if other else "")
    _menu(title, len(checked), lines, on_key, hint)
    picked = [i for i, on in enumerate(checked[:last]) if on]
    typed = text.strip() if other and checked[last] else ""
    names = [(short or options)[i] for i in picked] + ([typed] if typed else [])
    _answer(title, ", ".join(names) or "none")
    return picked, typed


def select(title: str, options: list[str], default: int = 0) -> int:
    """Index of the one option the user picked."""
    if not _tty():
        return _select_plain(title, options, default)

    def lines(cursor: int) -> list[str]:
        return [f"{'>' if i == cursor else ' '} {opt}" for i, opt in enumerate(options)]

    cursor = _menu(title, len(options), lines, lambda key, _: "done" if key in ENTER else None,
                   "up/down move, enter confirm", default)
    _answer(title, options[cursor])
    return cursor


def confirm(question: str, default: bool = True) -> bool:
    raw = input(f"{BOLD}{question}{RESET} [{'Y/n' if default else 'y/N'}] " if _tty() else
                f"{question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    return default if not raw else raw.startswith("y")


def ask(question: str) -> str:
    return input(f"{BOLD}{question}{RESET} " if _tty() else f"{question} ").strip()


# --- drawing --------------------------------------------------------------------


def _menu(title, count, lines, on_key, hint, cursor=0) -> int:
    """Draw title + lines + hint, redraw on every key, return the cursor at confirm.

    `on_key(key, cursor)` sees every key first: "done" confirms, "handled" swallows
    the key, None leaves it to the menu (arrows, abort).
    """
    drawn = 0
    with _raw_input(), _hidden_cursor():
        while True:
            rows = [f"{BOLD}{title}{RESET}"]
            for i, line in enumerate(lines(cursor)):
                line = _fit(line)
                rows.append(f"{CYAN}{line}{RESET}" if i == cursor else line)
            rows.append(f"{DIM}{_fit(hint)}{RESET}")
            _redraw(rows, drawn)
            drawn = len(rows)
            for key in _keys(os.read(sys.stdin.fileno(), 64)):
                result = on_key(key, cursor)
                if result == "done":
                    _redraw([], drawn)
                    return cursor
                if result == "handled":
                    continue
                if key in ABORT:
                    _redraw([], drawn)
                    raise KeyboardInterrupt
                if key in UP:
                    cursor = (cursor - 1) % count
                elif key in DOWN:
                    cursor = (cursor + 1) % count


def _keys(data: bytes) -> list[bytes]:
    """Split one read into keys: fast typing or a held arrow key arrives as several at once."""
    keys, i = [], 0
    while i < len(data):
        if data[i:i + 1] == b"\x1b" and data[i + 1:i + 2] in (b"[", b"O") and i + 2 < len(data):
            keys.append(data[i:i + 3])
            i += 3
        else:
            keys.append(data[i:i + 1])
            i += 1
    return keys


def _redraw(rows: list[str], previous: int) -> None:
    """Replace the `previous` rows drawn last time with `rows`."""
    out = sys.stdout
    if previous:
        out.write(f"\x1b[{previous}A\r")
    out.write("\x1b[J")  # clear from here to the end of the screen
    for row in rows:
        out.write(row + "\n")
    out.flush()


def _answer(title: str, value: str) -> None:
    """What stays on screen once a menu is confirmed: the question and the answer, one line."""
    print(f"{BOLD}{title}{RESET} {CYAN}{_fit(value, len(title) + 1)}{RESET}")


def _fit(text: str, used: int = 0) -> str:
    """Cut plain text to the terminal width, so it never wraps (a wrapped line breaks the redraw)."""
    width = shutil.get_terminal_size().columns - 1 - used
    return text if len(text) <= width else text[: max(width - 1, 0)] + "…"


@contextlib.contextmanager
def _raw_input():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)  # keys arrive one by one, unechoed; output still translates \n
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


@contextlib.contextmanager
def _hidden_cursor():
    sys.stdout.write("\x1b[?25l")
    try:
        yield
    finally:
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


# --- without a terminal -----------------------------------------------------------


def _checkbox_plain(title: str, options: list[str], checked: list[bool], other: str | None) -> tuple[list[int], str]:
    print(title)
    for i, (opt, on) in enumerate(zip(options, checked), 1):
        print(f"  {i}) [{'x' if on else ' '}] {opt}")
    raw = input("Numbers to use, separated by spaces (empty = keep marked): ").split()
    if raw:
        picked = [int(n) - 1 for n in raw if n.isdigit() and 0 < int(n) <= len(options)]
    else:
        picked = [i for i, on in enumerate(checked) if on]
    typed = input(f"{other} (empty = none): ").strip() if other else ""
    return picked, typed


def _select_plain(title: str, options: list[str], default: int) -> int:
    print(title)
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")
    raw = input(f"Choice [{default + 1}]: ").strip()
    return int(raw) - 1 if raw.isdigit() and 0 < int(raw) <= len(options) else default
