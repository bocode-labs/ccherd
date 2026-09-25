"""Is a pid still the process we think it is?

A pid alone can be reused, so Claude Code records each session's process start
time next to its pid ("procStart"), in the platform's own notation:

- Linux: field 22 of /proc/<pid>/stat (clock ticks since boot).
- macOS: `ps -o lstart=` in UTC and the C locale, e.g. "Fri Sep 25 08:23:22 2026".

ccherd records its own supervisors the same way, so one comparison covers both.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def proc_start(pid: int) -> str | None:
    if pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return None
        return stat.rsplit(")", 1)[1].split()[19]  # field 22 overall; comm may contain spaces
    r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, check=False, text=True,
                       env={**os.environ, "TZ": "UTC", "LC_ALL": "C"})
    out = r.stdout.strip()
    return out or None


def pid_alive(pid: int, start: str | None = None) -> bool:
    cur = proc_start(pid)
    return cur is not None and (start is None or cur == str(start))
