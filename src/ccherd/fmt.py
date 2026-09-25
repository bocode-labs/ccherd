"""Human-readable times for tables."""

from __future__ import annotations

import datetime as dt
import time


def fmt_ts(ts: float | None) -> str:
    if not ts:
        return "-"
    return dt.datetime.fromtimestamp(ts).astimezone().strftime("%a %d.%m. %H:%M")


def fmt_age(ts: float | None) -> str:
    if not ts:
        return "-"
    s = int(time.time() - ts)
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"
