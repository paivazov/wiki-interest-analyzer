"""Analysis pipeline and the deterministic trust verdict.

fetch -> sum each article with its redirects -> sum the cluster -> divide by the
edition's total views (share, views per million) -> stats -> verdict + caveats.
All thresholds live below so methodology.md can cite them.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

import i18n
from common import (DATA_START, VERSION, WiaError, add_months, current_month, last_complete_month, month_range,
                    progress, rnd, today)
from stats import (annual_rate, hampel, log_series, mann_kendall, recurring_months, slope_ci_block_bootstrap, theil_sen,
                   yoy_change)

MATERIAL_PCT = 5.0         # |trend| below 5 %/yr counts as "flat"
LOW_VOLUME = 300           # median monthly views below this -> confidence low
SMALL_VOLUME = 1000        # below this -> confidence <= medium
SPIKE_SHARE_LOW = 0.30     # >30% of the growth explained by spikes -> confidence low
WIDE_CI_FLAT = 15.0        # no clear trend, CI reaches beyond +-15 %/yr -> confidence medium
VERY_WIDE_CI = 40.0        # ... beyond +-40 %/yr -> confidence low (we can't even say it's flat)
MIN_MONTHS = 12            # fewer -> insufficient_data
YOY_MONTHS = 24            # fewer -> no YoY, confidence <= medium
LATE_ARTICLE_MONTHS = 2    # >=2 leading zero months = article created inside the window
BOT_PAGE_SHARE = 0.8       # one page carries >=80% of a spike's excess ...
BOT_RATIO = 3.0            # ... and the spike is >=3x normal -> possible bot traffic
LEVELS = ("low", "medium", "high")
# which cap explains the confidence first (most informative for the user)
REASON_PRIORITY = ("spike_driven_growth", "low_volume", "wide_interval", "signals_disagree", "articles_disagree",
                   "weak_significance", "small_volume", "no_yoy_short_window", "uk_2022_shift")


def sig(x, digits=3):
    """Round to significant digits (levels span 0.01 .. 10,000 per million)."""
    if x is None or x != x:
        return None
    if x == 0:
        return 0.0
    from math import floor, log10
    return round(float(x), max(0, digits - 1 - int(floor(log10(abs(x))))))


def direction(pct: float | None) -> str:
    if pct is None:
        return "insufficient_data"
    if pct >= MATERIAL_PCT:
        return "growing"
    if pct <= -MATERIAL_PCT:
        return "declining"
    return "flat"


def growth_metric(y) -> float | None:
    """YoY when >= 24 months, otherwise last half vs first half. Mean-based on
    purpose: unlike Theil-Sen it reacts to spikes, which is what we want to measure."""
    y = np.asarray(y, dtype=float)
    g = yoy_change(y)
    if g is not None:
        return g
    h = len(y) // 2
    if h < 3 or y[:h].mean() <= 0:
        return None
    return float(y[-h:].mean() / y[:h].mean() - 1)


def _spike_attribution(k: int, pages: dict[str, np.ndarray]):
    """Which single page (article or redirect) carries the excess views in month k."""
    excess = {}
    for name, v in pages.items():
        lo, hi = max(0, k - 3), min(len(v), k + 4)
        excess[name] = v[k] - np.median(v[lo:hi])
    pos = {n: e for n, e in excess.items() if e > 0}
    if not pos:
        return None, 0.0
    top = max(pos, key=pos.get)
    return top, pos[top] / sum(pos.values())


def analyze_lang(months: list[str], topic_views, edition_views, pages: dict[str, np.ndarray] | None = None,
                 articles: dict[str, np.ndarray] | None = None, lang: str = "") -> dict:
    """Pure function: aligned monthly arrays -> metrics, verdict, caveats.

    caveats are (code, params) tuples; texts are rendered later in the report language."""
    topic = np.asarray(topic_views, dtype=float)
    ed = np.asarray(edition_views, dtype=float)
    n = len(months)
    share = np.divide(topic, ed, out=np.zeros_like(topic), where=ed > 0) * 1e6
    last = slice(-12, None)
    res = {
        "n_months": n, "start": months[0] if n else None, "end": months[-1] if n else None,
        "level_per_million": sig(share[last].mean()) if n else None,
        "avg_monthly_views": int(round(topic[last].mean())) if n else None,
        "median_monthly_views": int(round(float(np.median(topic)))) if n else None,
    }
    caveats: list[tuple[str, dict]] = []
    if n < MIN_MONTHS:
        caveats.append(("insufficient_data", {"lang": lang, "n": n}))
        res.update(trend="insufficient_data", confidence="low", caps=["insufficient_data"], caveats=caveats,
                   spikes=[], share=share.tolist(), share_clean=share.tolist(), spike_idx=[])
        return res

    out_idx, med, clean_all = hampel(share)
    ratios = {k: max(share[k], 1e-12) / max(med[k], 1e-12) for k in out_idx}
    ratios = {k: max(r, 1 / r) for k, r in ratios.items()}
    recurring = recurring_months(months, out_idx, ratios)
    oneoff = [k for k in out_idx if k not in recurring]
    # trend: robust fit on the log scale with every outlier replaced by the local median
    log_clean = log_series(clean_all)
    slope_log, _ = theil_sen(log_clean)
    ci_lo, ci_hi = slope_ci_block_bootstrap(log_clean)
    tau, p = mann_kendall(clean_all)
    s_pct = annual_rate(slope_log)
    s_raw_pct = annual_rate(theil_sen(log_series(share))[0])
    ci = (annual_rate(ci_lo), annual_rate(ci_hi))
    # spike influence: compare mean-based growth with vs without ONE-OFF spikes only
    # (recurring seasonal peaks are present in both years, so they cancel)
    clean_oneoff = share.copy()
    clean_oneoff[oneoff] = med[oneoff]
    yoy_raw, yoy_clean = yoy_change(share), yoy_change(clean_oneoff)
    abs_pct = annual_rate(theil_sen(log_series(topic))[0])
    base_pct = annual_rate(theil_sen(log_series(ed))[0])

    pages = pages or {}
    spikes = []
    for k in out_idx:
        up = share[k] > med[k]
        top, top_share = _spike_attribution(k, pages) if up and pages else (None, 0.0)
        spikes.append({"month": months[k], "dir": "up" if up else "down",
                       "kind": "recurring" if k in recurring else "one-off",
                       "x_normal": rnd(share[k] / med[k], 1) if med[k] > 0 else None,
                       "top_page": top, "top_page_share": rnd(top_share, 2)})
    up_spikes = [s for s in spikes if s["dir"] == "up" and s["kind"] == "one-off"]

    g_raw, g_clean = growth_metric(share), growth_metric(clean_oneoff)
    spike_share = 0.0
    if up_spikes and g_raw is not None and g_raw * 100 > MATERIAL_PCT:
        spike_share = float(np.clip((g_raw - max(g_clean or 0.0, 0.0)) / g_raw, 0, 1))

    # do the individual articles of the cluster tell the same story?
    art_dirs = {}
    for a, v in (articles or {}).items():
        if np.median(v) >= 50:
            a_share = np.divide(v, ed, out=np.zeros_like(v), where=ed > 0) * 1e6
            art_dirs[a] = direction(annual_rate(theil_sen(log_series(hampel(a_share)[2]))[0]))

    # a direction is claimed only when the 95% CI excludes zero; otherwise "flat" = no clear trend
    trend = direction(s_pct)
    if trend != "flat" and ci[0] is not None and ci[0] <= 0 <= ci[1]:
        trend = "flat"
    caps: list[tuple[str, str]] = []  # (max level, code)
    if n < YOY_MONTHS:
        caps.append(("medium", "no_yoy_short_window"))
        caveats.append(("no_yoy_short_window", {"n": n}))
    fmt = lambda x: i18n.pct(x, 0)  # noqa: E731
    if trend in ("growing", "declining") and p >= 0.05:
        caps.append(("medium", "weak_significance"))
        caveats.append(("weak_significance", {"lang": lang, "p": rnd(p, 2)}))
    elif trend == "flat" and ci[0] is not None and (ci[0] < -WIDE_CI_FLAT or ci[1] > WIDE_CI_FLAT):
        very = ci[0] < -VERY_WIDE_CI or ci[1] > VERY_WIDE_CI
        caps.append(("low" if very else "medium", "wide_interval"))
        caveats.append(("wide_interval", {"lang": lang, "lo": fmt(ci[0]), "hi": fmt(ci[1]), "p": rnd(p, 2)}))
    if spike_share > SPIKE_SHARE_LOW:
        caps.append(("low", "spike_driven_growth"))
        caveats.append(("spike_driven_growth", {
            "lang": lang, "share": int(round(spike_share * 100)), "months": ", ".join(s["month"] for s in up_spikes),
            "raw": fmt(g_raw * 100), "clean": fmt((g_clean or 0) * 100)}))
    elif any(s["kind"] == "one-off" for s in spikes):
        caveats.append(("spikes_present", {"lang": lang, "months": ", ".join(
            s["month"] for s in spikes if s["kind"] == "one-off")}))
    if recurring:
        cal = sorted({months[k][5:7] for k in recurring})
        caveats.append(("seasonal_peaks", {"lang": lang, "cal_months": cal}))
    if len(art_dirs) >= 3:
        agree = sum(1 for d in art_dirs.values() if d == trend)
        if agree / len(art_dirs) < 0.6:
            caps.append(("medium", "articles_disagree"))
            caveats.append(("articles_disagree", {"lang": lang, "agree": agree, "total": len(art_dirs),
                                                  "dirs": art_dirs}))
    if res["median_monthly_views"] < LOW_VOLUME:
        caps.append(("low", "low_volume"))
        caveats.append(("low_volume", {"lang": lang, "views": res["median_monthly_views"]}))
    elif res["median_monthly_views"] < SMALL_VOLUME:
        caps.append(("medium", "small_volume"))
        caveats.append(("small_volume", {"lang": lang, "views": res["median_monthly_views"]}))
    if yoy_clean is not None and s_pct is not None:
        yd = direction(yoy_clean * 100)
        if yd != trend and abs(yoy_clean * 100 - s_pct) >= 2 * MATERIAL_PCT:
            opposite = {yd, trend} == {"growing", "declining"}
            caps.append(("low" if opposite else "medium", "signals_disagree"))
            caveats.append(("signals_disagree", {"lang": lang, "slope": fmt(s_pct), "yoy": fmt(yoy_clean * 100)}))
    if len([v for v in pages.values() if v.sum() > 0]) >= 2:
        for s in up_spikes:
            if s["top_page_share"] and s["top_page_share"] >= BOT_PAGE_SHARE and (s["x_normal"] or 0) >= BOT_RATIO:
                caveats.append(("possible_bot_spike", {"lang": lang, "month": s["month"], "ratio": s["x_normal"],
                                                       "article": s["top_page"]}))
    if abs_pct is not None and abs_pct >= MATERIAL_PCT and (s_pct or 0) < MATERIAL_PCT:
        caveats.append(("edition_wide_growth", {"lang": lang, "abs": fmt(abs_pct), "share": fmt(s_pct)}))
    if base_pct is not None and base_pct <= -MATERIAL_PCT:
        caveats.append(("edition_traffic_declining", {"lang": lang, "base": fmt(base_pct)}))
    if lang == "uk":
        spans = months[0] <= "2022-12"
        caveats.append(("uk_2022_shift", {"lang": lang, "spans_2022": spans}))
        if spans:
            caps.append(("medium", "uk_2022_shift"))

    confidence = "high"
    for level, _ in caps:
        if LEVELS.index(level) < LEVELS.index(confidence):
            confidence = level
    caps_sorted = [c for lvl, c in sorted(caps, key=lambda c: (LEVELS.index(c[0]), REASON_PRIORITY.index(c[1])))]
    res.update({
        "trend": trend, "confidence": confidence, "caps": caps_sorted,
        "slope_pct_per_year": rnd(s_pct), "slope_ci_pct": [rnd(ci[0]), rnd(ci[1])],
        "slope_pct_per_year_raw": rnd(s_raw_pct), "mk_p": rnd(p, 4), "mk_tau": rnd(tau, 2),
        "articles_agree": f"{sum(1 for d in art_dirs.values() if d == trend)}/{len(art_dirs)}" if art_dirs else None,
        "article_trends": art_dirs,
        "yoy_pct": rnd(yoy_clean * 100) if yoy_clean is not None else None,
        "yoy_pct_raw": rnd(yoy_raw * 100) if yoy_raw is not None else None,
        "spike_share_of_growth": rnd(spike_share, 2),
        "abs_views_pct_per_year": rnd(abs_pct), "edition_pct_per_year": rnd(base_pct),
        "spikes": spikes, "caveats": caveats,
        "share": share.tolist(), "share_clean": clean_all.tolist(), "spike_idx": out_idx,
    })
    return res


# ---------------------------------------------------------------- ranking

def rank_langs(results: dict[str, dict]) -> list[dict]:
    ok = [l for l, r in results.items() if r["status"] == "ok" and r["trend"] != "insufficient_data"]
    by_level = sorted(ok, key=lambda l: -results[l]["level_per_million"])
    by_trend = sorted(ok, key=lambda l: -(results[l]["slope_pct_per_year"] or 0))
    ranked = []
    for l in ok:
        rl, rt = by_level.index(l) + 1, by_trend.index(l) + 1
        ranked.append({"lang": l, "level_rank": rl, "trend_rank": rt, "score": (rl + rt) / 2})
    ranked.sort(key=lambda e: (e["score"], e["level_rank"]))
    out = []
    for i, e in enumerate(ranked, 1):
        r = results[e["lang"]]
        out.append({"lang": e["lang"], "rank": i, "level_rank": e["level_rank"], "trend_rank": e["trend_rank"],
                    "trend": r["trend"], "confidence": r["confidence"]})
    for l, r in results.items():
        if l not in ok:
            out.append({"lang": l, "rank": None, "why": "content_gap" if r["status"] == "no_article" else
                        "insufficient_data"})
    return out


def explore_next(ranking: list[dict]) -> list[str]:
    """Top-ranked languages whose verdict is at least medium and not declining."""
    good = [e["lang"] for e in ranking if e["rank"] and e["confidence"] != "low" and e["trend"] != "declining"]
    return good[:2]


# ---------------------------------------------------------------- pipeline

def _window(spec) -> tuple[list[str], list[tuple[str, dict]]]:
    caveats = []
    start, end = spec["start"], spec["end"]
    lcm = last_complete_month()
    if end == "latest" or end > lcm:
        if end == "latest" or end >= current_month():
            caveats.append(("incomplete_month_excluded", {"month": current_month()}))
        end = lcm
    if start < DATA_START:
        start = DATA_START
        caveats.append(("window_clamped", {}))
    if start > end:
        raise WiaError(f"Empty window {start}..{end}", hint="Check start/end in the spec (spec set <spec> months=24).")
    return month_range(start, end), caveats


def run_analysis(spec: dict, http) -> dict:
    rl = i18n.rl_of(spec.get("report_lang"))
    agent, access = spec.get("agent", "user"), spec.get("access", "all-access")
    months, global_cav = _window(spec)
    langs = spec["langs"]
    articles = spec.get("articles", {})
    redirects = spec.get("redirects", {})
    with_articles = [l for l in langs if articles.get(l)]

    totals = {l: http.project_totals(f"{l}.wikipedia", agent, access, months) for l in with_articles}
    for l, tot in totals.items():
        if not tot:
            raise WiaError(f"No pageview data for {l}.wikipedia", hint="Is the language code a Wikipedia edition?")
    if totals:
        published = min(max(t) for t in totals.values())
        if published < months[-1]:
            global_cav.append(("latest_month_unpublished", {"month": add_months(published, 1), "end": published}))
            months = [m for m in months if m <= published]

    # redirects that get a series: 'major' ones always; 'minor' ones only for articles
    # that look newly created, because a rename leaves the old history on a redirect
    rd_used = {l: {a: list(redirects.get(l, {}).get(a, [])) for a in articles[l]} for l in with_articles}
    minor = spec.get("redirects_minor", {})

    def fetch(pairs):
        return http.article_series(list(dict.fromkeys(pairs)), agent, access, months)

    series = fetch([(f"{l}.wikipedia", t) for l in with_articles for a in articles[l] for t in [a] + rd_used[l][a]])

    def summed(l, a):
        proj = f"{l}.wikipedia"
        vec = lambda t: np.array([series[(proj, t)].get(m, 0) for m in months], dtype=float)  # noqa: E731
        pages = {t: vec(t) for t in [a] + rd_used[l][a]}
        return sum(pages.values()), pages

    extra = []
    for l in with_articles:
        for a in articles[l]:
            v, _ = summed(l, a)
            nz = np.flatnonzero(v)
            if len(nz) and nz[0] >= LATE_ARTICLE_MONTHS and minor.get(l, {}).get(a):
                rd_used[l][a] += minor[l][a]
                extra += [(f"{l}.wikipedia", t) for t in minor[l][a]]
    if extra:
        progress(f"possible renames: checking {len(extra)} more redirects")
        series.update(fetch(extra))

    results: dict[str, dict] = {}
    for l in langs:
        missing = spec.get("missing", {}).get(l, [])
        if l not in with_articles:
            results[l] = {"status": "no_article", "trend": None, "confidence": None,
                          "caveats": [("no_article", {"lang": l})], "missing": [m.get("label") for m in missing]}
            continue
        pages, art_views = {}, {}
        for a in articles[l]:
            art_views[a], p_ = summed(l, a)
            pages.update(p_)
        # articles created inside the window would look like growth
        first_nz = {a: (int(np.flatnonzero(v)[0]) if v.any() else None) for a, v in art_views.items()}
        late = [a for a, f in first_nz.items() if f is not None and f >= LATE_ARTICLE_MONTHS]
        live = [a for a, f in first_nz.items() if f is not None]
        lang_months, extra_cav, keep = months, [], list(articles[l])
        if late:
            if len(late) == len(live):
                cut = min(first_nz[a] for a in late)
                lang_months = months[cut:]
                extra_cav.append(("article_created_in_window",
                                  {"lang": l, "articles": ", ".join(late), "month": months[cut], "trimmed": True}))
            else:
                keep = [a for a in keep if a not in late]
                extra_cav.append(("article_created_in_window", {
                    "lang": l, "articles": ", ".join(late), "month": months[min(first_nz[a] for a in late)],
                    "trimmed": False}))
        off = len(months) - len(lang_months)
        topic = sum(art_views[a][off:] for a in keep)
        ed = np.array([totals[l].get(m, 0) for m in lang_months], dtype=float)
        kept_pages = {p: v[off:] for p, v in pages.items() if p in keep or any(p in rd_used[l][a] for a in keep)}
        r = analyze_lang(lang_months, topic, ed, kept_pages, {a: art_views[a][off:] for a in keep}, lang=l)
        r["caveats"] = extra_cav + r["caveats"]
        for t in spec.get("truncated", {}).get(l, []):
            r["caveats"].append(("redirects_truncated", {"lang": l, "n": 200, "article": t}))
        n_total = len(articles[l]) + len(missing)
        if missing and n_total > 1:
            names = ", ".join(m.get("label") or m["qid"] for m in missing)
            r["caveats"].append(("partial_cluster", {"lang": l, "present": len(articles[l]), "total": n_total,
                                                     "missing": names}))
        r.update(status="ok", months=lang_months, topic_views=topic.tolist(), edition_views=ed.tolist(),
                 articles={a: int(art_views[a][off:].sum()) for a in articles[l]},
                 n_pages=sum(1 + len(rd_used[l][a]) for a in articles[l]))
        results[l] = r

    # one merged caveat for editions whose own traffic is shrinking
    falling = []
    for l, r in results.items():
        keep_c = []
        for code, params in r["caveats"]:
            if code == "edition_traffic_declining":
                falling.append((l, params["base"]))
            else:
                keep_c.append((code, params))
        r["caveats"] = keep_c
    if falling:
        global_cav.append(("edition_traffic_declining", {"items": ", ".join(f"{l} {b}" for l, b in falling)}))
    n_ok = sum(1 for r in results.values() if r["status"] == "ok")
    if n_ok > 1:
        global_cav.append(("edition_sizes_differ", {}))
    global_cav.append(("curiosity_not_demand", {}))
    ranking = rank_langs(results) if len(langs) > 1 else []
    return {"spec": spec, "months": months, "results": results, "ranking": ranking,
            "explore_next": explore_next(ranking) if ranking else [], "global_caveats": global_cav, "rl": rl,
            "generated": today().isoformat(), "version": VERSION}


# ---------------------------------------------------------------- rendering

def topic_name(spec: dict, rl: str) -> str:
    return (spec.get("topic_labels") or {}).get(rl) or spec.get("topic_label") or spec.get("topic_qid", "?")


def _reason(code: str, params: dict, rl: str) -> str:
    """Caveat text without the '{Wiki}: ' prefix, for use after 'Confidence: x —'."""
    p = dict(params, wiki_cap="\x00", wiki_subj="\x00")
    txt = i18n.caveat_text(code, rl, p)
    txt = txt.replace("\x00: ", "").replace("\x00 ", "").replace("\x00", "")
    return txt[0].lower() + txt[1:] if txt and code != "uk_2022_shift" else txt


def confidence_reason(r: dict, rl: str) -> str:
    if not r.get("caps"):
        return i18n.t("reason.all_checks_passed", rl)
    top = r["caps"][0]
    for code, params in r["caveats"]:
        if code == top:
            return _reason(code, params, rl)
    return top


def all_caveats(a: dict) -> list[dict]:
    """Every caveat rendered in the report language, most severe first."""
    rl = a["rl"]
    out = []
    for l, r in a["results"].items():
        for code, params in r.get("caveats", []):
            out.append({"code": code, "severity": i18n.severity(code), "lang": l,
                        "text": i18n.caveat_text(code, rl, params)})
    for code, params in a["global_caveats"]:
        out.append({"code": code, "severity": i18n.severity(code), "lang": None,
                    "text": i18n.caveat_text(code, rl, params)})
    seen, uniq = set(), []
    for c in sorted(out, key=lambda c: i18n.SEVERITY_ORDER[c["severity"]]):
        if c["text"] not in seen:
            seen.add(c["text"])
            uniq.append(c)
    return uniq


def must_relay(a: dict, k: int = 3) -> list[dict]:
    """The caveats a reply must carry word for word: the k most severe (one per kind first, so different
    limitations are covered), then curiosity != demand. The code picks them so the agent only copies."""
    cav = all_caveats(a)
    serious = [c for c in cav if c["severity"] != "info"]
    picked = []
    for c in serious:
        if len(picked) < k and c["code"] not in {p["code"] for p in picked}:
            picked.append(c)
    picked += [c for c in serious if c not in picked][:k - len(picked)]
    return picked + [c for c in cav if c["code"] == "curiosity_not_demand"]


def log_summary(a: dict) -> dict:
    """Verdict fields for the invocation log, so evals can grade what each run told the agent."""
    return {"report_lang": a["rl"],
            "confidence": {l: r["confidence"] for l, r in a["results"].items() if r["status"] == "ok"},
            "caveat_codes": sorted({c["code"] for c in all_caveats(a) if c["severity"] != "info"}),
            "must_relay_codes": [c["code"] for c in must_relay(a)]}


def answer_text(a: dict) -> str:
    rl, spec, res = a["rl"], a["spec"], a["results"]
    topic = topic_name(spec, rl)
    conf = lambda r: i18n.t(f"conf.{r['confidence']}", rl)  # noqa: E731
    if len(spec["langs"]) == 1:
        l = spec["langs"][0]
        r = res[l]
        if r["status"] == "no_article":
            where = i18n.wiki_in(l, rl)
            return i18n.t("ans.no_article_only", rl, topic=topic, where=where, where_cap=where[0].upper() + where[1:])
        if r["trend"] == "insufficient_data":
            return i18n.caveat_text("insufficient_data", rl, {"lang": l, "n": r["n_months"]})
        where = i18n.wiki_in(l, rl)
        yoy = ""
        if r.get("yoy_pct") is not None:
            raw = r.get("yoy_pct_raw")
            spiky = raw is not None and abs(raw - r["yoy_pct"]) >= 10
            yoy = i18n.t("ans.yoy_spiky" if spiky else "ans.yoy", rl, yoy=i18n.pct(r["yoy_pct"]),
                         **({"raw": i18n.pct(raw)} if spiky else {}))
        return i18n.t("ans.single", rl, where=where[0].upper() + where[1:], topic=topic,
                      trend=i18n.t(f"trend.{r['trend']}", rl), slope=i18n.pct(r["slope_pct_per_year"]),
                      n=r["n_months"], yoy=yoy, conf=conf(r), reason=confidence_reason(r, rl))
    parts = []
    ranked = [e for e in a["ranking"] if e["rank"]]
    if len(ranked) == 1:
        one = dict(a, spec=dict(spec, langs=[ranked[0]["lang"]]))
        parts.append(answer_text(one))
        for e in a["ranking"]:
            if not e["rank"]:
                r = res[e["lang"]]
                where = i18n.wiki_in(e["lang"], rl)
                parts.append(i18n.t("ans.no_article_only", rl, topic=topic, where=where,
                                    where_cap=where[0].upper() + where[1:]) if r["status"] == "no_article" else
                             i18n.caveat_text("insufficient_data", rl, {"lang": e["lang"], "n": r.get("n_months", 0)}))
        return " ".join(parts)
    if ranked:
        lead = res[ranked[0]["lang"]]
        parts.append(i18n.t("ans.lead", rl, wiki=i18n.wiki_subject(ranked[0]["lang"], rl),
                            level=lead["level_per_million"], trend=i18n.t(f"trend_short.{lead['trend']}", rl),
                            slope=i18n.pct(lead["slope_pct_per_year"]), conf=conf(lead)))
    items = []
    for e in a["ranking"][1:] if ranked else a["ranking"]:
        r = res[e["lang"]]
        name = i18n.lang_name(e["lang"], rl)
        if r["status"] == "no_article":
            items.append(i18n.t("ans.other_gap", rl, lang=name))
        elif e["rank"]:
            items.append(i18n.t("ans.other_item", rl, lang=name, trend=i18n.t(f"trend_short.{r['trend']}", rl),
                                slope=i18n.pct(r["slope_pct_per_year"]), conf=conf(r)))
    if items:
        parts.append(i18n.t("ans.others", rl, items="; ".join(items)))
    if a.get("explore_next"):
        parts.append(i18n.t("ans.explore", rl, langs=", ".join(i18n.lang_name(l, rl) for l in a["explore_next"])))
    else:
        parts.append(i18n.t("ans.explore_none", rl))
    parts.append(i18n.t("ans.sizes", rl))
    return " ".join(parts)


def rank_why(e: dict, a: dict) -> str:
    """Code-written reason for a ranking position, so the agent never has to invent one."""
    rl, r = a["rl"], a["results"][e["lang"]]
    if not e.get("rank"):
        return i18n.t("rank.gap" if r["status"] == "no_article" else "rank.insufficient", rl)
    n = sum(1 for x in a["ranking"] if x.get("rank"))
    return i18n.t("rank.why", rl, level=r["level_per_million"], lvl_rank=e["level_rank"],
                  trend_rank=e["trend_rank"], n=n,
                  trend=i18n.t(f"trend_short.{r['trend']}", rl), slope=i18n.pct(r["slope_pct_per_year"]),
                  conf=i18n.t(f"conf.{r['confidence']}", rl))


def compact(a: dict, files: dict) -> dict:
    """What the agent sees on stdout: small, decision-ready."""
    langs = {}
    for l, r in a["results"].items():
        if r["status"] != "ok":
            langs[l] = {"status": r["status"], "missing_topics": r.get("missing")}
            continue
        langs[l] = {k: r.get(k) for k in (
            "status", "trend", "confidence", "level_per_million", "avg_monthly_views", "slope_pct_per_year",
            "slope_ci_pct", "mk_p", "yoy_pct", "yoy_pct_raw", "abs_views_pct_per_year", "edition_pct_per_year",
            "n_months", "articles_agree")}
        langs[l]["confidence_reason"] = confidence_reason(r, a["rl"])
        langs[l]["spikes"] = [{k: s[k] for k in ("month", "dir", "kind", "x_normal", "top_page")} for s in r["spikes"]]
        langs[l]["caveat_codes"] = [c for c, _ in r["caveats"]]
    cav = all_caveats(a)
    out = {
        "status": "ok", "topic": f"{topic_name(a['spec'], a['rl'])} ({a['spec'].get('topic_qid')})",
        "period": {"start": a["months"][0], "end": a["months"][-1], "months": len(a["months"])},
        "answer": answer_text(a), "must_relay": [c["text"] for c in must_relay(a)], "langs": langs,
        "caveats": [c["text"] for c in cav if c["severity"] != "info"]
        + [c["text"] for c in cav if c["code"] == "curiosity_not_demand"],
        "info_caveats": sorted({c["code"] for c in cav if c["severity"] == "info"} - {"curiosity_not_demand"}),
        "files": files,
    }
    if a["ranking"]:
        out["ranking"] = [dict({k: v for k, v in e.items() if v is not None}, why=rank_why(e, a))
                          for e in a["ranking"]]
        out["explore_next"] = a["explore_next"]
    return out


def write_outputs(a: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path, json_path = out_dir / "series.csv", out_dir / "analysis.json"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["month", "lang", "edition_views", "topic_views", "share_per_million",
                    "share_clean_per_million", "spike"])
        for l, r in a["results"].items():
            if r["status"] != "ok":
                continue
            spike = set(r["spike_idx"])
            for i, m in enumerate(r["months"]):
                w.writerow([m, l, int(r["edition_views"][i]), int(r["topic_views"][i]), sig(r["share"][i], 5),
                            sig(r["share_clean"][i], 5), int(i in spike)])
    full = dict(a)
    full["caveats"] = all_caveats(a)
    full["answer"] = answer_text(a)
    full["must_relay"] = must_relay(a)
    full["results"] = {l: {k: (v if k != "caveats" else [c for c, _ in v]) for k, v in r.items()}
                       for l, r in a["results"].items()}
    json_path.write_text(json.dumps(full, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return {"analysis": str(json_path), "series_csv": str(csv_path)}
