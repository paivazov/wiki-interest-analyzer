"""SQLite cache.

pageviews: one row per (project, article, agent, access, granularity, month).
Only months that are complete AND published by Wikimedia are stored, so a stored
row never changes and is never refetched. article='' holds the project total.

http_json: raw API responses (Wikidata, wiki API) with a TTL.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from common import cache_dir

PROJECT_TOTAL = ""  # article key used for the whole-edition aggregate


class Cache:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else cache_dir() / "cache.sqlite"
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS pageviews (
                project TEXT, article TEXT, agent TEXT, access TEXT, granularity TEXT,
                month TEXT, views INTEGER, fetched_at REAL,
                PRIMARY KEY (project, article, agent, access, granularity, month));
            CREATE TABLE IF NOT EXISTS http_json (
                url TEXT PRIMARY KEY, fetched_at REAL, status INTEGER, body TEXT);
            """
        )

    # ------------------------------------------------------------ pageviews
    def get_months(self, project, article, agent, access, granularity, months) -> dict[str, int]:
        if not months:
            return {}
        q = (
            "SELECT month, views FROM pageviews WHERE project=? AND article=? AND agent=? "
            "AND access=? AND granularity=? AND month BETWEEN ? AND ?"
        )
        rows = self.db.execute(q, (project, article, agent, access, granularity, min(months), max(months)))
        wanted = set(months)
        return {m: v for m, v in rows if m in wanted}

    def put_months(self, project, article, agent, access, granularity, values: dict[str, int]) -> None:
        now = time.time()
        self.db.executemany(
            "INSERT OR REPLACE INTO pageviews VALUES (?,?,?,?,?,?,?,?)",
            [(project, article, agent, access, granularity, m, int(v), now) for m, v in values.items()],
        )
        self.db.commit()

    # ------------------------------------------------------------ http json
    def get_json(self, url: str, ttl_days: float):
        row = self.db.execute("SELECT fetched_at, status, body FROM http_json WHERE url=?", (url,)).fetchone()
        if not row:
            return None
        fetched_at, status, body = row
        if ttl_days is not None and time.time() - fetched_at > ttl_days * 86400:
            return None
        return status, (json.loads(body) if body else None)

    def put_json(self, url: str, status: int, body) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO http_json VALUES (?,?,?,?)",
            (url, time.time(), status, json.dumps(body, ensure_ascii=False) if body is not None else None),
        )
        self.db.commit()

    def info(self) -> dict:
        n_pv = self.db.execute("SELECT COUNT(*) FROM pageviews").fetchone()[0]
        n_series = self.db.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT project, article, agent, access FROM pageviews)"
        ).fetchone()[0]
        n_json = self.db.execute("SELECT COUNT(*) FROM http_json").fetchone()[0]
        size = self.path.stat().st_size if self.path.exists() else 0
        return {"path": str(self.path), "pageview_rows": n_pv, "series": n_series, "api_responses": n_json,
                "size_mb": round(size / 1e6, 2)}

    def clear(self) -> None:
        self.db.executescript("DELETE FROM pageviews; DELETE FROM http_json;")
        self.db.commit()
