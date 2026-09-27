"""Verdict rules on synthetic series with a known answer."""
import numpy as np
import pytest

from analyze import analyze_lang
from conftest import months_list, synth

EDITION = 50_000_000.0


def run(topic, months=None, lang="pl", edition=None, articles=None, pages=None):
    n = len(topic)
    months = months or months_list("2023-09", n)
    ed = np.full(n, EDITION) if edition is None else edition
    return analyze_lang(months, np.asarray(topic, float), ed, pages=pages, articles=articles, lang=lang)


def codes(r):
    return [c for c, _ in r["caveats"]]


def test_growing_with_seasonality_is_high_confidence():
    y = synth(36, 20_000, growth_per_year=0.25, season=0.2, noise=0.03, seed=1)
    r = run(y)
    assert r["trend"] == "growing"
    assert r["confidence"] == "high"
    assert r["slope_pct_per_year"] == pytest.approx(25, abs=6)
    assert 0 < r["slope_ci_pct"][0] < 25 < r["slope_ci_pct"][1]


def test_declining():
    r = run(synth(36, 20_000, growth_per_year=-0.3, noise=0.04, seed=2))
    assert r["trend"] == "declining"
    assert r["confidence"] == "high"


def test_flat_noise_is_flat():
    r = run(synth(36, 20_000, growth_per_year=0.0, season=0.1, noise=0.05, seed=3))
    assert r["trend"] == "flat"


def test_spike_driven_growth_is_low_confidence_and_names_the_spike():
    y = synth(24, 20_000, growth_per_year=0.0, noise=0.03, seed=4)
    y[20] *= 6  # one news event in the last year
    r = run(y, months=months_list("2024-09", 24))
    assert "spike_driven_growth" in codes(r)
    assert r["confidence"] == "low"
    assert r["trend"] == "flat"  # the durable trend, spike removed
    assert [s["month"] for s in r["spikes"] if s["dir"] == "up"] == ["2026-05"]
    assert r["yoy_pct_raw"] > 30 and abs(r["yoy_pct"]) < 10


def test_recurring_school_year_peaks_are_seasonal_not_news():
    y = synth(36, 20_000, noise=0.03, seed=5)
    for k in (0, 12, 24):  # every September
        y[k] *= 3
    r = run(y, months=months_list("2023-09", 36))
    assert "seasonal_peaks" in codes(r)
    assert "spike_driven_growth" not in codes(r)
    assert all(s["kind"] == "recurring" for s in r["spikes"])


def test_short_window_has_no_yoy_and_caps_confidence():
    r = run(synth(12, 20_000, growth_per_year=0.4, noise=0.02, seed=6))
    assert r["yoy_pct"] is None
    assert "no_yoy_short_window" in codes(r)
    assert r["confidence"] in ("medium", "low")


def test_too_few_months_is_insufficient():
    r = run(synth(8, 20_000))
    assert r["trend"] == "insufficient_data"
    assert r["confidence"] == "low"


def test_low_volume_is_low_confidence():
    r = run(synth(36, 120, growth_per_year=0.3, noise=0.02, seed=7))
    assert "low_volume" in codes(r)
    assert r["confidence"] == "low"


def test_growth_of_whole_edition_is_not_topic_growth():
    edition = synth(36, EDITION, growth_per_year=0.3, noise=0.0)
    topic = edition * 4e-4 * np.exp(np.random.default_rng(8).normal(0, 0.02, 36))  # constant share
    r = run(topic, edition=edition)
    assert r["trend"] == "flat"
    assert r["abs_views_pct_per_year"] > 20
    assert "edition_wide_growth" in codes(r)


def test_ukrainian_always_gets_structural_shift_caveat():
    y = synth(36, 20_000, growth_per_year=0.2, noise=0.02, seed=9)
    r = run(y, lang="uk", months=months_list("2023-09", 36))
    assert "uk_2022_shift" in codes(r)
    assert r["confidence"] == "high"  # window after 2022: caveat only
    r22 = run(y, lang="uk", months=months_list("2021-09", 36))
    assert r22["confidence"] == "medium"  # window spans 2022: capped


def test_cluster_articles_disagreeing_caps_confidence():
    a = synth(36, 10_000, growth_per_year=0.6, noise=0.02, seed=10)
    b = synth(36, 6_000, growth_per_year=-0.3, noise=0.02, seed=11)
    c = synth(36, 5_000, growth_per_year=-0.3, noise=0.02, seed=12)
    r = run(a + b + c, articles={"A": a, "B": b, "C": c})
    assert r["articles_agree"] in ("1/3", "2/3")
    if r["articles_agree"] == "1/3":
        assert "articles_disagree" in codes(r)
        assert r["confidence"] != "high"


def test_bot_like_spike_on_single_redirect_is_flagged():
    main = synth(24, 20_000, noise=0.02, seed=13)
    redirect = np.full(24, 50.0)
    redirect[18] = 200_000  # one page, one month
    r = run(main + redirect, months=months_list("2024-09", 24), pages={"Main": main, "Old title": redirect})
    assert "possible_bot_spike" in codes(r)
