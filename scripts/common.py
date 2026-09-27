"""Shared helpers: month arithmetic, paths, JSON output, errors."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

VERSION = "0.1.0"
SKILL_DIR = Path(__file__).resolve().parent.parent

# Wikimedia's User-Agent policy asks for a descriptive agent with contact info; an e-mail or a
# full URL both count (mediawiki.org/wiki/Wikimedia_APIs/Rate_limits). The project URL is always
# sent. Operators can add their own contact via WIA_CONTACT: Wikimedia keys the rate-limit counter
# by the contact and prefers an e-mail over a URL, so an e-mail there gets its own budget.
PROJECT_URL = "https://github.com/paivazov/wiki-interest-analyzer"


def operator_contact(environ=os.environ) -> str:
    return environ.get("WIA_CONTACT", "").strip()


def user_agent(contact: str = "") -> str:
    contacts = "; ".join(c for c in (PROJECT_URL, contact) if c)
    return f"wiki-interest-analyzer/{VERSION} ({contacts}) python-requests"


OPERATOR_CONTACT = operator_contact()
USER_AGENT = user_agent(OPERATOR_CONTACT)

# Per-article pageview data starts in July 2015.
DATA_START = "2015-07"
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
LANG_RE = re.compile(r"^[a-z][a-z0-9-]{1,15}$")
QID_RE = re.compile(r"^Q\d+$")


class WiaError(Exception):
    """User-facing error. Printed as JSON with a hint; exit code 2."""

    def __init__(self, message: str, hint: str | None = None, code: str = "error"):
        super().__init__(message)
        self.hint = hint
        self.code = code


# ---------------------------------------------------------------- months

def parse_month(s: str) -> str:
    s = s.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        s = s[:7]
    if not MONTH_RE.match(s):
        raise WiaError(f"Bad month '{s}'", hint="Use YYYY-MM, e.g. 2024-09")
    return s


def add_months(month: str, n: int) -> str:
    y, m = int(month[:4]), int(month[5:7])
    idx = y * 12 + (m - 1) + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def month_range(start: str, end: str) -> list[str]:
    out, cur = [], start
    while cur <= end:
        out.append(cur)
        cur = add_months(cur, 1)
    return out


def months_between(start: str, end: str) -> int:
    """Inclusive count of months from start to end."""
    return (int(end[:4]) * 12 + int(end[5:7])) - (int(start[:4]) * 12 + int(start[5:7])) + 1


def today() -> dt.date:
    # WIA_TODAY lets tests and evals pin "now" (YYYY-MM-DD).
    forced = os.environ.get("WIA_TODAY")
    if forced:
        return dt.date.fromisoformat(forced)
    return dt.datetime.now(dt.timezone.utc).date()


def current_month() -> str:
    t = today()
    return f"{t.year:04d}-{t.month:02d}"


def last_complete_month() -> str:
    return add_months(current_month(), -1)


def api_ts_start(month: str) -> str:
    return month.replace("-", "") + "0100"


def api_ts_end(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    last = nxt - dt.timedelta(days=1)
    return last.strftime("%Y%m%d") + "00"


# ---------------------------------------------------------------- paths

def cache_dir() -> Path:
    base = os.environ.get("WIA_CACHE_DIR")
    if base:
        p = Path(base)
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        p = Path(xdg) / "wiki-interest-analyzer" if xdg else Path.home() / ".cache" / "wiki-interest-analyzer"
    p.mkdir(parents=True, exist_ok=True)
    return p


def slugify(text: str) -> str:
    s = re.sub(r"[^\w]+", "-", text.lower(), flags=re.UNICODE).strip("-")
    return s[:48] or "topic"


# ---------------------------------------------------------------- output

def emit(obj) -> None:
    """Compact JSON on stdout (the agent reads this)."""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def progress(msg: str) -> None:
    if os.environ.get("WIA_QUIET") != "1":
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()


def rnd(x, nd=1):
    """Round floats for compact output; pass through None."""
    if x is None:
        return None
    try:
        if x != x:  # NaN
            return None
    except TypeError:
        return x
    return round(float(x), nd)
