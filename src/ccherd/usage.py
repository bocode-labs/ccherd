"""Usage per account, and which account a new subagent should run on.

Included weekly quota that is still unused expires at the weekly reset; anything
above it is billed as extra usage. So the account that most needs to be used is
the one with the highest remaining weekly percent PER HOUR until its reset: the
rate at which it must be used to waste nothing. That rate falls as the account is
used, so work spreads across accounts in proportion to how urgently each expires,
instead of draining the earliest-resetting account first. The free share of the
5-hour window weights the score, and a nearly full window or week excludes the
account.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
import urllib.error

from . import api, config
from .config import Account
from .credentials import fresh_oauth
from .fmt import fmt_ts

USAGE_TTL_S = 60
FIVE_HOUR_GATE = 90.0  # an account at or above this 5h utilization is not picked
WEEKLY_GATE = 98.0  # ... nor one at or above this weekly utilization


def fetch_usage(account: Account, *, refresh: bool = False) -> dict:
    """Usage for one account, cached for USAGE_TTL_S across all ccherd calls of this user."""
    cache = config.CACHE_DIR / f"usage-{account.label}.json"
    now = time.time()
    if not refresh and cache.is_file():
        try:
            cached = json.loads(cache.read_text())
            if now - cached["fetched_at"] < USAGE_TTL_S:
                return cached
        except (OSError, ValueError, KeyError):
            pass

    oauth = fresh_oauth(account.dir)
    base = {"fetched_at": now, "tier": oauth.get("rateLimitTier"), "usage": None, "error": None}
    if not oauth.get("accessToken"):
        return {**base, "error": "not logged in"}
    if oauth.get("expiresAt", 0) / 1000 <= now:
        # No refresh (see credentials.py). Fall back to the last good reading, marked
        # stale, so an idle account does not vanish from the table.
        old = json.loads(cache.read_text()) if cache.is_file() else {}
        return {**base, "usage": old.get("usage"), "stale_since": old.get("fetched_at"),
                "error": "access token expired, a session on this account renews it on its next request"}

    try:
        result = {**base, "usage": api.get("usage", oauth["accessToken"])}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {**base, "error": f"usage request failed: {e}"}
    api.write_cache(cache, result)
    return result


def _parse_ts(s: str | None) -> float | None:
    if not s:
        return None
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def model_family(model: str | None) -> str | None:
    m = (model or "").lower()
    return next((fam for fam in ("opus", "sonnet", "haiku", "fable") if fam in m), None)


def model_limit(usage: dict, model: str | None) -> dict | None:
    """The weekly limit that applies to this model only, if the plan has one.

    Newer plans report it in `limits` (kind "weekly_scoped", with the model's display
    name); older readings used seven_day_<family>.
    """
    fam = model_family(model)
    if not fam:
        return None
    for lim in usage.get("limits") or []:
        name = (((lim.get("scope") or {}).get("model") or {}).get("display_name") or "").lower()
        if lim.get("kind") == "weekly_scoped" and fam in name:
            return {"utilization": lim.get("percent"), "resets_at": lim.get("resets_at")}
    return usage.get(f"seven_day_{fam}")


def score(usage: dict | None, model: str | None, now: float,
          five_gate: float = FIVE_HOUR_GATE, week_gate: float = WEEKLY_GATE) -> dict:
    """How urgently this account should be used for `model`. Higher = pick first.

    Returns {"score": float | None, "reason": str, ...}; score None means excluded.
    """
    if not usage:
        return {"score": None, "reason": "no usage data"}
    five = usage.get("five_hour") or {}
    week = usage.get("seven_day") or {}
    per_model = model_limit(usage, model)

    five_u = float(five.get("utilization") or 0.0)
    five_reset = _parse_ts(five.get("resets_at"))
    if five_reset is not None and five_reset <= now:
        five_u = 0.0  # the window has rolled over since the reading

    week_u = float(week.get("utilization") or 0.0)
    week_reset = _parse_ts(week.get("resets_at"))
    if per_model and per_model.get("utilization") is not None and float(per_model["utilization"]) > week_u:
        week_u = float(per_model["utilization"])
        week_reset = _parse_ts(per_model.get("resets_at")) or week_reset
    if week_reset is not None and week_reset <= now:
        week_u = 0.0

    hours = max(((week_reset or now + 7 * 86400) - now) / 3600, 0.5)
    rate = (100.0 - week_u) / hours
    info = {"five_hour": five_u, "five_hour_reset": five_reset, "weekly": week_u,
            "weekly_reset": week_reset, "hours_to_weekly_reset": hours, "rate": rate,
            "extra_usage": bool((usage.get("extra_usage") or {}).get("is_enabled"))}
    if five_u >= five_gate:
        return {**info, "score": None, "reason": f"5h window at {five_u:.0f}%"}
    if week_u >= week_gate:
        return {**info, "score": None, "reason": f"weekly at {week_u:.0f}%"}
    return {**info, "score": rate * (1.0 - five_u / 100.0),
            "reason": f"{100 - week_u:.0f}% weekly left over {hours:.0f}h"}


def rank_accounts(model: str | None, *, refresh: bool = False) -> list[dict]:
    """Every configured account with its score, best first. Assumes all accounts are the same tier."""
    now = time.time()
    pol = config.policy()
    rows = []
    for account in config.load_accounts():
        u = fetch_usage(account, refresh=refresh)
        s = score(u.get("usage"), model, now, pol["five-hour-limit"], pol["weekly-limit"])
        if u.get("error") and u.get("usage"):
            # Expired token: nothing on this machine used the account since the
            # reading, so the old numbers are a fair estimate, and an idle account is
            # exactly the one to pick. Use elsewhere (another machine) is not visible.
            s = {**s, "reason": f"stale reading from {fmt_ts(u.get('stale_since'))}; " + s["reason"]}
        elif u.get("error"):
            s = {**s, "score": None, "reason": u["error"]}
        rows.append({"account": account.label, "dir": str(account.dir), "tier": u.get("tier"), **s})
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    return rows


def pick_when_saturated(rows: list[dict]) -> dict | None:
    """With every account past its limits: the one that can still run - extra usage on - and
    of those the least loaded. None when no account has usage data."""
    known = [r for r in rows if "five_hour" in r]
    if not known:
        return None
    return min(known, key=lambda r: (not r.get("extra_usage"), r["five_hour"] >= 100, r["weekly"], r["five_hour"]))


def pick_account(model: str | None) -> str:
    rows = rank_accounts(model)
    if rows and rows[0]["score"] is not None:
        return rows[0]["account"]
    if config.policy()["when-saturated"] == "use" and (pick := pick_when_saturated(rows)):
        how = "extra usage" if pick.get("extra_usage") else "no extra usage enabled, so it may hit its limit"
        print(f"ccherd: every account is past its limits; using {pick['account']} anyway ({how}) "
              "because when-saturated is 'use'", file=sys.stderr)
        return pick["account"]
    lines = [f"  {r['account']}: {r['reason']}" for r in rows]
    frees = [r["five_hour_reset"] for r in rows if r.get("five_hour_reset")]
    hint = f"\n  next 5h window frees at {fmt_ts(min(frees))}" if frees else ""
    raise SystemExit("ccherd: every account is saturated - using one now would be extra usage.\n"
                     + "\n".join(lines) + hint + "\n  pass --account LABEL to use one anyway, or allow it for good: "
                     "ccherd config when-saturated use")
