"""Shared fixtures. No test touches the network: FakeWiki stands in for every
Wikimedia endpoint and counts calls, so cache behaviour can be asserted."""
from __future__ import annotations

import json
import os
import urllib.parse

import numpy as np
import pytest

os.environ["WIA_QUIET"] = "1"


def months_list(start: str, n: int) -> list[str]:
    y, m = int(start[:4]), int(start[5:7])
    out = []
    for i in range(n):
        idx = y * 12 + m - 1 + i
        out.append(f"{idx // 12:04d}-{idx % 12 + 1:02d}")
    return out


def synth(n: int, level: float, growth_per_year: float = 0.0, season: float = 0.0, noise: float = 0.0,
          seed: int = 0, start_month: int = 1) -> np.ndarray:
    """Multiplicative series: level * (1+g)^(t/12) * (1 + season*sin) * lognormal noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    trend = (1 + growth_per_year) ** (t / 12)
    seas = 1 + season * np.sin(2 * np.pi * (t + start_month - 1) / 12)
    return level * trend * seas * np.exp(rng.normal(0, noise, n))


class FakeWiki:
    """Callable transport: url -> (status, json, headers)."""

    def __init__(self):
        self.totals: dict[str, dict[str, int]] = {}
        self.pages: dict[tuple[str, str], dict[str, int]] = {}
        self.sitelinks: dict[str, dict[str, str]] = {}     # qid -> {lang: title}
        self.labels: dict[str, str] = {}
        self.redirects: dict[tuple[str, str], list[str]] = {}  # (lang, title) -> redirect titles
        self.recent: dict[tuple[str, str], int] = {}
        self.calls: list[str] = []

    # ------------------------------------------------------------------ helpers
    def pageview_calls(self, project: str | None = None) -> list[str]:
        return [c for c in self.calls if "/metrics/pageviews/" in c and (project is None or f"/{project}/" in c)]

    @staticmethod
    def _items(series: dict[str, int], start: str, end: str) -> list[dict]:
        lo, hi = f"{start[:4]}-{start[4:6]}", f"{end[:4]}-{end[4:6]}"
        return [{"timestamp": m.replace("-", "") + "0100", "views": v} for m, v in sorted(series.items())
                if lo <= m <= hi and v > 0]

    def __call__(self, url: str):
        self.calls.append(url)
        if "/metrics/pageviews/aggregate/" in url:
            parts = url.split("/aggregate/")[1].split("/")
            project, start, end = parts[0], parts[4], parts[5]
            items = self._items(self.totals.get(project, {}), start, end)
            return (200, {"items": items}, {}) if items else (404, {"title": "Not Found"}, {})
        if "/metrics/pageviews/per-article/" in url:
            parts = url.split("/per-article/")[1].split("/")
            project, title, start, end = parts[0], urllib.parse.unquote(parts[3]).replace("_", " "), parts[5], parts[6]
            items = self._items(self.pages.get((project, title), {}), start, end)
            return (200, {"items": items}, {}) if items else (404, {"title": "Not Found"}, {})
        host = urllib.parse.urlparse(url).netloc
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        if host == "www.wikidata.org":
            return 200, self._wikidata(params), {}
        lang = host.split(".")[0]
        return 200, self._wiki(lang, params), {}

    def _wikidata(self, p):
        if p.get("action") == "wbgetentities":
            ents = {}
            for q in p["ids"].split("|"):
                if q not in self.sitelinks:
                    ents[q] = {"id": q, "missing": ""}
                    continue
                ents[q] = {"id": q, "labels": {"en": {"value": self.labels.get(q, q)}},
                           "descriptions": {"en": {"value": "test item"}},
                           "sitelinks": {f"{l}wiki": {"title": t} for l, t in self.sitelinks[q].items()}}
            return {"entities": ents}
        if p.get("action") == "wbsearchentities":
            return {"search": []}
        return {}

    def _wiki(self, lang, p):
        titles = p.get("titles", "").split("|")
        if p.get("prop") == "redirects":
            pages = []
            for t in titles:
                if not any(l == lang and tt == t for (l, tt) in self.redirects) and \
                        not any(v.get(lang) == t for v in self.sitelinks.values()):
                    pages.append({"title": t, "missing": True})
                else:
                    pages.append({"title": t, "redirects": [{"title": r} for r in self.redirects.get((lang, t), [])]})
            return {"query": {"pages": pages}}
        if p.get("prop") == "pageviews":
            return {"query": {"pages": [{"title": t, "pageviews": {"2026-08-01": self.recent.get((lang, t), 0)}}
                                        for t in titles]}}
        if p.get("list") == "search":
            return {"query": {"search": [{"title": "Nearest article"}]}}
        return {}


@pytest.fixture
def fake():
    return FakeWiki()


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("WIA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("WIA_TODAY", "2026-09-26")
    monkeypatch.setenv("WIA_LOG", str(tmp_path / "log.jsonl"))
    yield


@pytest.fixture
def http(fake, tmp_path):
    from cache import Cache
    from fetch import Http

    return Http(Cache(tmp_path / "cache.sqlite"), transport=fake, sleep=lambda s: None)


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
