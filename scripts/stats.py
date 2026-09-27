"""Pure statistics on monthly series. No I/O. Deterministic (fixed seeds).

See references/methodology.md for why each method was chosen.
"""
from __future__ import annotations

import math

import numpy as np


def theil_sen(y) -> tuple[float, float]:
    """Median of pairwise slopes (per month). Intercept uses scipy's convention:
    median(y) - slope * median(t)."""
    y = np.asarray(y, dtype=float)
    n = len(y)
    if n < 2:
        return float("nan"), float("nan")
    t = np.arange(n, dtype=float)
    i, j = np.triu_indices(n, 1)
    slope = float(np.median((y[j] - y[i]) / (t[j] - t[i])))
    return slope, float(np.median(y) - slope * np.median(t))


def slope_ci_block_bootstrap(y, n_boot: int = 500, block: int = 3, seed: int = 42,
                             alpha: float = 0.05) -> tuple[float, float]:
    """Moving-block bootstrap of residuals around the Theil-Sen line.

    Blocks of 3 months keep short-range autocorrelation that an i.i.d. bootstrap
    would destroy (which would make the interval too narrow)."""
    y = np.asarray(y, dtype=float)
    n = len(y)
    if n < 6:
        return float("nan"), float("nan")
    slope, icpt = theil_sen(y)
    t = np.arange(n, dtype=float)
    fit = icpt + slope * t
    resid = y - fit
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(n / block)
    starts = rng.integers(0, n - block + 1, size=(n_boot, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)).reshape(n_boot, -1)[:, :n]
    y_star = fit + resid[idx]
    i, j = np.triu_indices(n, 1)
    slopes = np.median((y_star[:, j] - y_star[:, i]) / (j - i), axis=1)
    lo, hi = np.percentile(slopes, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def mann_kendall(y) -> tuple[float, float]:
    """Two-sided Mann-Kendall trend test with tie correction.
    Returns (Kendall's tau, p-value)."""
    y = np.asarray(y, dtype=float)
    n = len(y)
    if n < 4:
        return float("nan"), float("nan")
    i, j = np.triu_indices(n, 1)
    s = float(np.sign(y[j] - y[i]).sum())
    _, counts = np.unique(y, return_counts=True)
    var = (n * (n - 1) * (2 * n + 5) - float(np.sum(counts * (counts - 1) * (2 * counts + 5)))) / 18.0
    if var <= 0:
        return 0.0, 1.0
    if s > 0:
        z = (s - 1) / math.sqrt(var)
    elif s < 0:
        z = (s + 1) / math.sqrt(var)
    else:
        z = 0.0
    p = math.erfc(abs(z) / math.sqrt(2))
    return s / (n * (n - 1) / 2), p


def hampel(y, half_window: int = 3, n_sigmas: float = 3.0, min_rel: float = 0.3):
    """Hampel filter. A point is an outlier if it is more than n_sigmas robust SDs
    (1.4826*MAD) AND more than min_rel (30%) away from the rolling median.
    The relative floor stops tiny wiggles in very smooth series being flagged.

    Returns (outlier_indices, rolling_median, cleaned_series)."""
    y = np.asarray(y, dtype=float)
    n = len(y)
    med = np.empty(n)
    mad = np.empty(n)
    for k in range(n):
        w = y[max(0, k - half_window): min(n, k + half_window + 1)]
        med[k] = np.median(w)
        mad[k] = 1.4826 * np.median(np.abs(w - med[k]))
    dev = np.abs(y - med)
    out = (dev > n_sigmas * mad) & (dev > min_rel * np.abs(med))
    cleaned = np.where(out, med, y)
    return [int(k) for k in np.flatnonzero(out)], med, cleaned


def yoy_change(y) -> float | None:
    """Mean of the last 12 months vs the 12 months before (same calendar months,
    so seasonality cancels). Needs >= 24 points. Returns a fraction (0.1 = +10%)."""
    y = np.asarray(y, dtype=float)
    if len(y) < 24:
        return None
    prev, last = y[-24:-12].mean(), y[-12:].mean()
    if prev <= 0:
        return None
    return float(last / prev - 1)


def log_series(y) -> np.ndarray:
    """log(y + floor). Page views change multiplicatively, so trends are fitted on
    the log scale; the floor (1% of the median positive value) keeps zeros finite."""
    y = np.asarray(y, dtype=float)
    pos = y[y > 0]
    floor = 0.01 * float(np.median(pos)) if len(pos) else 1e-9
    return np.log(y + floor)


def annual_rate(slope_log_per_month: float) -> float | None:
    """Per-month slope on the log scale -> compound % change per year.
    Bounded below by -100%, unlike a linear slope divided by the level."""
    if slope_log_per_month is None or slope_log_per_month != slope_log_per_month:
        return None
    return (math.exp(12 * slope_log_per_month) - 1) * 100


def recurring_months(months: list[str], idx: list[int], ratios: dict[int, float] | None = None) -> set[int]:
    """Outliers whose calendar month is also an outlier in another year, with a
    similar size (within 3x), are seasonal (e.g. school-year start), not news.
    A 50x Olympic month next to a 2x bump a year earlier stays a one-off."""
    ratios = ratios or {}
    out = set()
    for k in idx:
        for j in idx:
            if j != k and months[j][5:7] == months[k][5:7] and months[j][:4] != months[k][:4]:
                if ratios.get(j, 1.0) >= ratios.get(k, 1.0) / 3:
                    out.add(k)
                    break
    return out
