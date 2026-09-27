"""HTTP client for Wikimedia APIs (retry, backoff, User-Agent, cache).

Pageview series are cached per month in SQLite (see cache.py). A month is stored
only once Wikimedia has published it, so cached months are never requested again.
"""
from __future__ import annotations

import os
import random
import sqlite3
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from cache import PROJECT_TOTAL, Cache
from common import OPERATOR_CONTACT, USER_AGENT, WiaError, api_ts_end, api_ts_start, progress

PAGEVIEWS_API = "https://wikimedia.org/api/rest_v1/metrics/pageviews"
MAX_WORKERS = 3        # Wikimedia asks for at most 3 concurrent requests
# Wikimedia rate limits (mediawiki.org/wiki/Wikimedia_APIs/Rate_limits): 200 req/min for a
# User-Agent with contact info (the project URL always counts), 10 req/min without. Stay below.
PER_MIN = int(os.environ.get("WIA_REQ_PER_MIN") or 150)
SHARED_BUDGET_HINT = ("Wikimedia is rate-limiting. Without WIA_CONTACT every user of this skill shares one "
                      "budget; set WIA_CONTACT=<your e-mail> to get your own.")
MAX_ATTEMPTS = 5
TIMEOUT_S = (5, 20)  # connect, read
DEBUG = os.environ.get("WIA_DEBUG") == "1"


def _requests_transport():
    import requests

    local = threading.local()

    def transport(url: str):
        s = getattr(local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
            local.session = s
        r = s.get(url, timeout=TIMEOUT_S)
        try:
            body = r.json()
        except ValueError:
            body = None
        return r.status_code, body, r.headers

    return transport


class Pacer:
    """Cross-process pacing: one shared 'next free slot' timestamp in SQLite, so
    parallel runs on one machine share a single request budget."""

    def __init__(self, path, per_min: int = PER_MIN):
        self.path = str(path)
        self.interval = 60.0 / max(1, per_min)

    def _slot(self, push_to: float | None = None) -> float:
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            con.execute("CREATE TABLE IF NOT EXISTS pace (id INTEGER PRIMARY KEY CHECK (id = 1), next_ts REAL)")
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT next_ts FROM pace WHERE id = 1").fetchone()
            now = time.time()
            nxt = row[0] if row else 0.0
            if push_to is not None:  # a 429 told us to back off: everyone waits
                slot = max(nxt, push_to)
                con.execute("INSERT OR REPLACE INTO pace VALUES (1, ?)", (slot,))
            else:
                slot = max(now, nxt)
                con.execute("INSERT OR REPLACE INTO pace VALUES (1, ?)", (slot + self.interval,))
            con.execute("COMMIT")
            return slot - now
        finally:
            con.close()

    def wait(self, sleep=time.sleep) -> None:
        delay = self._slot()
        if delay > 0:
            sleep(delay)

    def back_off(self, seconds: float) -> None:
        self._slot(push_to=time.time() + seconds)


class Http:
    """transport(url) -> (status, json_body, headers). Injectable for tests."""

    def __init__(self, cache: Cache, transport=None, sleep=time.sleep, pacer: Pacer | None = None):
        self.cache = cache
        self.pacer = pacer
        self.transport = transport or _requests_transport()
        self.sleep = sleep
        self.requests = 0
        self.cache_hits = 0       # API JSON responses served from cache
        self.cached_months = 0    # pageview months served from cache
        self.pv_requests: dict[str, int] = {}  # per project, pageview API calls only
        self.rate_limited = 0     # HTTP 429 responses
        self._lock = threading.Lock()

    # ------------------------------------------------------------ raw
    def _get(self, url: str):
        last = None
        for attempt in range(MAX_ATTEMPTS):
            with self._lock:
                self.requests += 1
            if self.pacer:
                self.pacer.wait(self.sleep)
            t0 = time.time()
            try:
                status, body, headers = self.transport(url)
            except Exception as e:  # connection errors, timeouts
                last = f"{type(e).__name__}: {e}"
                progress(f"retrying after {last} ({time.time() - t0:.1f}s)")
                self.sleep(min(30, 2 ** attempt) + random.random())
                continue
            if DEBUG:
                progress(f"[http] {status} {time.time() - t0:.1f}s {url[:100]}")
            if status == 429 or status >= 500:
                last = f"HTTP {status}"
                progress(f"retrying after {last}")
                if status == 429:
                    with self._lock:
                        self.rate_limited += 1
                        first = self.rate_limited == 1
                    if first and not OPERATOR_CONTACT:
                        progress(SHARED_BUDGET_HINT)
                retry_after = (headers or {}).get("Retry-After")
                wait = float(retry_after) if retry_after and str(retry_after).isdigit() else max(5, 2 ** attempt)
                if self.pacer:
                    self.pacer.back_off(wait)
                self.sleep(wait + random.random())
                continue
            return status, body
        raise WiaError(f"Network error after {MAX_ATTEMPTS} attempts: {last} ({url[:120]})",
                       hint="Check internet access to wikimedia.org / wikidata.org and retry.", code="network")

    def get_json(self, url: str, params: dict | None = None, ttl_days: float | None = 30):
        if params:
            url = url + "?" + urllib.parse.urlencode(sorted(params.items()))
        if ttl_days is not None:
            hit = self.cache.get_json(url, ttl_days)
            if hit is not None:
                self.cache_hits += 1
                return hit[1]
        status, body = self._get(url)
        if status >= 400 and status != 404:
            raise WiaError(f"HTTP {status} from {url[:120]}", code="network")
        if ttl_days is not None:
            self.cache.put_json(url, status, body)
        return body

    # ------------------------------------------------------------ pageviews
    @staticmethod
    def _pv_url(project, article, agent, access, start, end):
        if article == PROJECT_TOTAL:
            return (f"{PAGEVIEWS_API}/aggregate/{project}/{access}/{agent}/monthly/"
                    f"{api_ts_start(start)}/{api_ts_end(end)}")
        title = urllib.parse.quote(article.replace(" ", "_"), safe="")
        return (f"{PAGEVIEWS_API}/per-article/{project}/{access}/{agent}/{title}/monthly/"
                f"{api_ts_start(start)}/{api_ts_end(end)}")

    def _fetch_pv(self, project, article, agent, access, start, end) -> dict[str, int]:
        status, body = self._get(self._pv_url(project, article, agent, access, start, end))
        with self._lock:
            self.pv_requests[project] = self.pv_requests.get(project, 0) + 1
        if status == 404:
            return {}  # no views recorded in the whole range
        if status >= 400:
            raise WiaError(f"Pageviews API HTTP {status} for {project}/{article}", code="network")
        out = {}
        for it in (body or {}).get("items", []):
            ts = it["timestamp"]
            out[f"{ts[:4]}-{ts[4:6]}"] = int(it["views"])
        return out

    def project_totals(self, project, agent, access, months) -> dict[str, int]:
        """Whole-edition monthly views. Missing months in the result = not yet published."""
        cached = self.cache.get_months(project, PROJECT_TOTAL, agent, access, "monthly", months)
        missing = [m for m in months if m not in cached]
        if missing:
            got = self._fetch_pv(project, PROJECT_TOTAL, agent, access, min(missing), max(missing))
            got = {m: v for m, v in got.items() if m in set(missing)}
            self.cache.put_months(project, PROJECT_TOTAL, agent, access, "monthly", got)
            cached.update(got)
        self.cached_months += len(months) - len(missing)
        return cached

    def article_series(self, jobs: list[tuple[str, str]], agent, access, months) -> dict[tuple[str, str], dict]:
        """jobs: [(project, article)]; months must all be published.
        Returns {(project, article): {month: views}} with zeros filled in."""
        result, todo = {}, []
        for project, article in jobs:
            cached = self.cache.get_months(project, article, agent, access, "monthly", months)
            missing = [m for m in months if m not in cached]
            self.cached_months += len(months) - len(missing)
            result[(project, article)] = cached
            if missing:
                todo.append((project, article, min(missing), max(missing), missing))
        if todo:
            progress(f"fetching {len(todo)} pageview series ({len(jobs) - len(todo)} fully cached)")

            def work(t):
                project, article, lo, hi, missing = t
                return t, self._fetch_pv(project, article, agent, access, lo, hi)

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                for (project, article, lo, hi, missing), got in ex.map(work, todo):
                    filled = {m: got.get(m, 0) for m in missing}
                    self.cache.put_months(project, article, agent, access, "monthly", filled)
                    result[(project, article)].update(filled)
        return result

    def stats(self) -> dict:
        return {"http_requests": self.requests, "pageview_requests_by_project": dict(self.pv_requests),
                "cached_pageview_months": self.cached_months, "cached_api_responses": self.cache_hits,
                "rate_limited": self.rate_limited}
