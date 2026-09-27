"""Static PNG charts (matplotlib, Agg). Used on their own and inside the PDF report.

Design rules: colour follows the language (its position in spec.langs, never its
rank), 2px lines, recessive hairline grid, text in ink colours not series colours,
direct labels at line ends plus a legend, one y-axis only.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402
import numpy as np  # noqa: E402

import i18n  # noqa: E402

# Validated categorical order (adjacent-pair CVD dE >= 9.1, normal-vision >= 19.6).
# Aqua/yellow/magenta are < 3:1 on white, so every chart carries direct labels.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SURFACE, INK, INK_2, GRID = "#ffffff", "#0b0b0b", "#52514e", "#e4e3df"
FONT = "DejaVu Sans"  # ships with matplotlib; covers Cyrillic, Vietnamese, Turkish


def color_for(lang: str, langs: list[str]) -> str:
    i = langs.index(lang) if lang in langs else len(langs)
    return PALETTE[i % len(PALETTE)]


def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=8, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _dates(months):
    return np.array([np.datetime64(m + "-15") for m in months])


def _fmt(v: float) -> str:
    if v >= 10000:
        return f"{v / 1000:,.0f}k"
    if v >= 100:
        return f"{v:,.0f}"
    if v >= 10:
        return f"{v:.1f}"
    return f"{v:.2f}"


def render(a: dict, kind: str, out: Path, size=(7.2, 3.1), dpi=200) -> Path:
    rl = a["rl"]
    langs = a["spec"]["langs"]
    res = a["results"]
    ok = [l for l in langs if res[l]["status"] == "ok"]
    plt.rcParams.update({"font.family": FONT, "font.size": 8})
    fig, ax = plt.subplots(figsize=size, dpi=dpi)
    fig.patch.set_facecolor(SURFACE)
    _style(ax)

    if kind in ("share", "absolute"):
        key = "share" if kind == "share" else "topic_views"
        ends = []
        all_vals = []
        for l in ok:
            r = res[l]
            y = np.asarray(r[key], dtype=float)
            x = _dates(r["months"])
            c = color_for(l, langs)
            ax.plot(x, y, color=c, linewidth=2, solid_joinstyle="round", solid_capstyle="round",
                    label=i18n.lang_name(l, rl))
            ax.scatter(x[-1:], y[-1:], s=36, color=c, edgecolors=SURFACE, linewidths=2, zorder=4)
            oneoff = [i for i, s in zip(r["spike_idx"], r["spikes"]) if s["kind"] == "one-off" and s["dir"] == "up"]
            if oneoff:
                ax.scatter(x[oneoff], y[oneoff], s=70, facecolors="none", edgecolors=INK, linewidths=1.2, zorder=5)
                for i in oneoff:
                    ax.annotate(r["months"][i], (x[i], y[i]), xytext=(0, 7), textcoords="offset points",
                                ha="center", fontsize=7, color=INK_2)
            ends.append((y[-1], l, x[-1]))
            all_vals += [v for v in y if v > 0]
        if all_vals and max(all_vals) / max(min(all_vals), 1e-9) > 12:
            ax.set_yscale("log")
            ax.yaxis.set_major_locator(mticker.LogLocator(base=10, subs=(1.0, 2.0, 5.0)))
            ax.yaxis.set_minor_locator(mticker.NullLocator())
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}" if v >= 100 else f"{v:g}"))
        # direct labels at line ends; a label that would collide is dropped (the legend
        # still identifies the line) rather than nudged away from its line
        ax.margins(x=0.08)
        fig.canvas.draw()
        placed = []
        for v, l, x in sorted(ends):
            ypx = ax.transData.transform((mdates.date2num(x), v))[1]
            if any(abs(ypx - p) < 11 * dpi / 100 for p in placed):
                continue
            placed.append(ypx)
            ax.annotate(f"{l} {_fmt(v)}", (x, v), xytext=(6, 0), textcoords="offset points", va="center",
                        fontsize=7.5, color=INK)
        ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 7)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ylabel = {"share": {"en": "views per million", "uk": "переглядів на мільйон"},
                  "absolute": {"en": "views / month (not comparable across languages)",
                               "uk": "переглядів / міс. (між мовами не порівнюються)"}}[kind]
        ax.set_ylabel(ylabel.get(rl, ylabel["en"]), color=INK_2, fontsize=8)
        if len(ok) >= 2:
            ax.legend(frameon=False, fontsize=7.5, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=min(len(ok), 6),
                      labelcolor=INK, handlelength=1.5, borderaxespad=0.2)
    elif kind == "yoy":
        rows = [(l, res[l].get("yoy_pct")) for l in ok if res[l].get("yoy_pct") is not None]
        if not rows:
            ax.text(0.5, 0.5, "YoY needs >= 24 months" if rl != "uk" else "Для «рік до року» потрібно ≥ 24 міс.",
                    ha="center", va="center", color=INK_2, transform=ax.transAxes)
        else:
            ys = np.arange(len(rows))
            vals = [v for _, v in rows]
            ax.barh(ys, vals, height=0.5, color=[color_for(l, langs) for l, _ in rows])
            ax.axvline(0, color=INK_2, linewidth=0.8)
            ax.set_yticks(ys, [i18n.lang_name(l, rl) for l, _ in rows])
            ax.invert_yaxis()
            for yv, v in zip(ys, vals):
                ax.annotate(i18n.pct(v), (v, yv), xytext=(4 if v >= 0 else -4, 0), textcoords="offset points",
                            ha="left" if v >= 0 else "right", va="center", fontsize=7.5, color=INK)
            ax.grid(axis="y", visible=False)
            ax.grid(axis="x", color=GRID, linewidth=0.8)
            ax.set_xlabel({"en": "last 12 months vs previous 12 (share of views, one-off spikes removed)",
                           "uk": "останні 12 міс. проти попередніх 12 (частка переглядів, без разових сплесків)"}
                          .get(rl, "YoY"), color=INK_2, fontsize=8)
            lim = max(abs(v) for v in vals) * 1.3 + 1
            ax.set_xlim(-lim, lim)
    else:
        raise ValueError(kind)

    gaps = [l for l in langs if res[l]["status"] == "no_article"]
    if gaps and kind != "yoy":
        ax.text(1.0, 1.02, f"{i18n.t('r.no_article_legend', rl)}: {', '.join(gaps)}", transform=ax.transAxes,
                ha="right", va="bottom", fontsize=7, color=INK_2)
    fig.tight_layout()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)
    return out
