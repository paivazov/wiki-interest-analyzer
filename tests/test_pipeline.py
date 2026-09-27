"""End-to-end pipeline against FakeWiki: redirect summation, incomplete month,
unpublished month, no-article languages, renames, cache hits and misses."""
import numpy as np
import pytest

import analyze as an
from conftest import months_list, synth


def spec_for(langs, articles, redirects=None, minor=None, start="2023-09", end="2026-08", missing=None):
    return {"topic_qid": "Q1", "topic_label": "topic", "cluster": [{"qid": "Q1", "label": "topic"}],
            "langs": langs, "start": start, "end": end, "agent": "user", "access": "all-access",
            "report_lang": "en", "articles": articles, "redirects": redirects or {}, "redirects_minor": minor or {},
            "missing": missing or {}}


def load(fake, project, title, values, months):
    fake.pages[(project, title)] = {m: int(v) for m, v in zip(months, values)}


@pytest.fixture
def world(fake):
    months = months_list("2023-09", 37)  # through 2026-09 (current, incomplete)
    for lang in ("pl", "cs", "sk"):
        fake.totals[f"{lang}.wikipedia"] = {m: 50_000_000 for m in months}
    load(fake, "pl.wikipedia", "Main", synth(37, 10_000, 0.2, seed=1), months)
    load(fake, "pl.wikipedia", "Old name", np.full(37, 1_000), months)
    load(fake, "cs.wikipedia", "Hlavni", synth(37, 5_000, -0.1, seed=2), months)
    load(fake, "sk.wikipedia", "Hlavne", synth(37, 2_000, 0.0, seed=3), months)
    return months


def test_redirect_views_are_summed_into_the_article(http, fake, world):
    spec = spec_for(["pl"], {"pl": ["Main"]}, {"pl": {"Main": ["Old name"]}})
    a = an.run_analysis(spec, http)
    r = a["results"]["pl"]
    main_total = sum(v for m, v in fake.pages[("pl.wikipedia", "Main")].items() if m <= "2026-08")
    assert r["articles"]["Main"] == main_total + 1_000 * 36
    assert r["topic_views"][0] == fake.pages[("pl.wikipedia", "Main")]["2023-09"] + 1_000


def test_incomplete_current_month_is_excluded(http, fake, world):
    spec = spec_for(["pl"], {"pl": ["Main"]}, end="2026-09")  # today is 2026-09-26
    a = an.run_analysis(spec, http)
    assert a["months"][-1] == "2026-08"
    assert ("incomplete_month_excluded", {"month": "2026-09"}) in a["global_caveats"]
    assert not any("2026090100" in c for c in fake.pageview_calls())


def test_unpublished_last_month_is_not_cached_as_zero(http, fake, world, monkeypatch):
    for t in fake.totals.values():
        t.pop("2026-08")  # Wikimedia has not published August yet
    spec = spec_for(["pl"], {"pl": ["Main"]})
    a = an.run_analysis(spec, http)
    assert a["months"][-1] == "2026-07"
    assert any(c == "latest_month_unpublished" for c, _ in a["global_caveats"])
    # once August is published, it is fetched (not served as a cached zero)
    fake.totals["pl.wikipedia"]["2026-08"] = 50_000_000
    a2 = an.run_analysis(spec, http)
    assert a2["months"][-1] == "2026-08"


def test_language_without_article_is_a_gap_not_zero(http, fake, world):
    spec = spec_for(["pl", "cs"], {"pl": [], "cs": ["Hlavni"]}, missing={"pl": [{"qid": "Q1", "label": "topic"}]})
    a = an.run_analysis(spec, http)
    assert a["results"]["pl"]["status"] == "no_article"
    assert a["results"]["pl"].get("level_per_million") is None
    assert not fake.pageview_calls("pl.wikipedia")
    ranking = {e["lang"]: e for e in a["ranking"]}
    assert ranking["pl"]["why"] == "content_gap" and ranking["pl"]["rank"] is None
    assert "no article" in an.answer_text(a)


def test_second_run_is_fully_cached(http, fake, world):
    spec = spec_for(["pl", "cs"], {"pl": ["Main"], "cs": ["Hlavni"]}, {"pl": {"Main": ["Old name"]}})
    an.run_analysis(spec, http)
    before = len(fake.calls)
    an.run_analysis(spec, http)
    assert len(fake.calls) == before


def test_adding_a_language_fetches_only_that_language(http, fake, world):
    spec = spec_for(["pl", "cs"], {"pl": ["Main"], "cs": ["Hlavni"]})
    an.run_analysis(spec, http)
    before = len(fake.calls)
    spec["langs"].append("sk")
    spec["articles"]["sk"] = ["Hlavne"]
    an.run_analysis(spec, http)
    new = fake.calls[before:]
    assert new and all("/sk.wikipedia/" in c for c in new)


def test_shorter_window_needs_no_network(http, fake, world):
    spec = spec_for(["pl"], {"pl": ["Main"]})
    an.run_analysis(spec, http)
    before = len(fake.calls)
    spec["start"] = "2025-09"  # "only the last 12 months"
    a = an.run_analysis(spec, http)
    assert len(fake.calls) == before
    assert len(a["months"]) == 12
    assert a["results"]["pl"]["yoy_pct"] is None


def test_rename_is_recovered_from_minor_redirect(http, fake, world):
    months = world
    new = synth(37, 8_000, seed=4)
    new[:10] = 0  # article moved to "New title" in 2024-07 ...
    old = np.zeros(37)
    old[:10] = 8_000  # ... its history stayed on the old title, now a low-traffic redirect
    load(fake, "cs.wikipedia", "New title", new, months)
    load(fake, "cs.wikipedia", "Before rename", old, months)
    spec = spec_for(["cs"], {"cs": ["New title"]}, minor={"cs": {"New title": ["Before rename"]}})
    a = an.run_analysis(spec, http)
    r = a["results"]["cs"]
    assert "article_created_in_window" not in [c for c, _ in r["caveats"]]
    assert r["n_months"] == 36
    assert r["trend"] == "flat"


def test_genuinely_new_article_trims_window(http, fake, world):
    months = world
    y = synth(37, 3_000, seed=5)
    y[:14] = 0
    load(fake, "cs.wikipedia", "Brand new", y, months)
    spec = spec_for(["cs"], {"cs": ["Brand new"]})
    r = an.run_analysis(spec, http)["results"]["cs"]
    assert r["n_months"] == 36 - 14
    assert "article_created_in_window" in [c for c, _ in r["caveats"]]


def test_outputs_written(http, fake, world, tmp_path):
    spec = spec_for(["pl", "cs"], {"pl": ["Main"], "cs": ["Hlavni"]})
    a = an.run_analysis(spec, http)
    files = an.write_outputs(a, tmp_path / "run")
    assert (tmp_path / "run" / "series.csv").read_text().count("\n") == 1 + 2 * 36
    out = an.compact(a, files)
    assert set(out["langs"]) == {"pl", "cs"}
    assert out["caveats"][-1].startswith("Page views measure curiosity")
    assert {e["lang"] for e in out["ranking"]} == {"pl", "cs"}


# ------------------------------------------------------------------ User-Agent and rate limits
def test_default_user_agent_carries_project_url_and_no_email():
    import os
    import subprocess
    import sys

    import common

    assert not (common.SKILL_DIR / "contact.txt").exists()
    ua = common.user_agent(common.operator_contact({}))
    assert common.PROJECT_URL in ua and "@" not in ua
    # the constant the HTTP client actually sends, computed at import with WIA_CONTACT unset
    env = {k: v for k, v in os.environ.items() if k != "WIA_CONTACT"}
    out = subprocess.run([sys.executable, "-c", "import common; print(common.USER_AGENT)"], env=env,
                         cwd=common.SKILL_DIR / "scripts", capture_output=True, text=True, check=True).stdout
    assert common.PROJECT_URL in out and "@" not in out


def test_wia_contact_is_added_to_the_project_url():
    import common

    ua = common.user_agent(common.operator_contact({"WIA_CONTACT": " ops@example.org "}))
    assert f"({common.PROJECT_URL}; ops@example.org)" in ua


def test_rate_limited_responses_are_counted(tmp_path):
    from cache import Cache
    from fetch import Http

    replies = iter([(429, None, {"Retry-After": "1"}), (200, {"ok": 1}, {})])
    http = Http(Cache(tmp_path / "c.sqlite"), transport=lambda url: next(replies), sleep=lambda s: None)
    assert http.get_json("https://example.org/x", ttl_days=None) == {"ok": 1}
    assert http.stats()["rate_limited"] == 1
