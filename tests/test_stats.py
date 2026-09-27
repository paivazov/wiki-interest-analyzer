"""The statistics are hand-written (to keep runtime deps small), so each one is
cross-checked against scipy or a textbook value."""
import math

import numpy as np
import pytest
from scipy import stats as sps

from conftest import months_list
from stats import (annual_rate, hampel, log_series, mann_kendall, recurring_months, slope_ci_block_bootstrap,
                   theil_sen, yoy_change)


@pytest.mark.parametrize("seed", range(5))
def test_theil_sen_matches_scipy(seed):
    rng = np.random.default_rng(seed)
    y = rng.normal(0, 1, 40).cumsum() + rng.standard_t(2, 40)  # heavy tails
    ours = theil_sen(y)
    ref = sps.theilslopes(y, np.arange(40))
    assert ours[0] == pytest.approx(ref.slope)
    assert ours[1] == pytest.approx(ref.intercept)


def test_mann_kendall_textbook_value():
    # y = 1..5: S = 10, Var(S) = 5*4*15/18, Z = (S-1)/sqrt(Var) -> p = 0.0275
    tau, p = mann_kendall([1, 2, 3, 4, 5])
    assert tau == 1.0
    assert p == pytest.approx(2 * (1 - 0.5 * (1 + math.erf((9 / math.sqrt(50 / 3)) / math.sqrt(2)))), rel=1e-9)
    assert p == pytest.approx(0.0275, abs=1e-3)


def test_mann_kendall_matches_scipy_kendalltau_on_ties():
    rng = np.random.default_rng(3)
    y = rng.integers(0, 6, 30).astype(float)  # many ties
    tau_ours, p_ours = mann_kendall(y)
    ref = sps.kendalltau(np.arange(30), y, variant="b", method="asymptotic")
    # MK uses a continuity correction and tau-a, so compare loosely
    assert np.sign(tau_ours) == np.sign(ref.statistic)
    assert p_ours == pytest.approx(ref.pvalue, abs=0.06)


def test_mann_kendall_no_trend_in_noise():
    rng = np.random.default_rng(11)
    _, p = mann_kendall(rng.normal(0, 1, 36))
    assert p > 0.05


def test_hampel_flags_injected_spike_only():
    y = np.full(36, 100.0) + np.random.default_rng(0).normal(0, 3, 36)
    y[20] = 400
    idx, med, clean = hampel(y)
    assert idx == [20]
    assert clean[20] == pytest.approx(med[20])
    assert 90 < clean[20] < 110


def test_hampel_ignores_small_wiggles_in_smooth_series():
    # MAD is tiny in a very smooth series; the 30% relative floor stops false alarms
    y = 100 + np.linspace(0, 1, 30)
    y[10] += 5  # +5%: not a spike
    assert hampel(y)[0] == []


def test_recurring_months_marks_same_calendar_month():
    months = months_list("2023-09", 36)
    assert recurring_months(months, [0, 12, 24, 30]) == {0, 12, 24}


def test_annual_rate_and_log_slope():
    y = 50 * 1.2 ** (np.arange(36) / 12)  # +20%/yr compounding
    slope, _ = theil_sen(log_series(y))
    assert annual_rate(slope) == pytest.approx(20, abs=0.5)
    assert annual_rate(math.log(0.5) / 12) == pytest.approx(-50)
    assert annual_rate(-10) >= -100  # bounded below by -100%, unlike a linear slope / level


def test_bootstrap_ci_covers_true_slope():
    rng = np.random.default_rng(5)
    t = np.arange(36)
    y = np.log(100) + math.log(1.15) / 12 * t + rng.normal(0, 0.05, 36)
    lo, hi = slope_ci_block_bootstrap(y)
    assert lo < math.log(1.15) / 12 < hi
    assert annual_rate(lo) > 0  # a clear +15%/yr trend excludes zero


def test_bootstrap_is_deterministic():
    y = np.random.default_rng(1).normal(0, 1, 30).cumsum()
    assert slope_ci_block_bootstrap(y) == slope_ci_block_bootstrap(y)


def test_yoy_change():
    y = np.r_[np.full(12, 100.0), np.full(12, 130.0)]
    assert yoy_change(y) == pytest.approx(0.30)
    assert yoy_change(y[:20]) is None
