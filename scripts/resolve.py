"""Topic -> Wikidata QID -> article title per language (+ redirects).

Titles differ per language ("intermittent fasting" is "Přerušovaný půst" in cs),
so we never guess titles: we follow Wikidata sitelinks. Ambiguous topics are
returned to the agent instead of being guessed.
"""
from __future__ import annotations

import re
import unicodedata

WD_API = "https://www.wikidata.org/w/api.php"
NON_WIKIPEDIA = {"commons", "species", "meta", "wikidata", "mediawiki", "sources", "outreach", "incubator",
                 "wikimania", "foundation", "wikifunctions", "beta"}
INTERNAL_DESC = ("wikimedia disambiguation page", "wikimedia category", "wikimedia list article",
                 "wikimedia template", "wikimedia project page", "wikimedia module", "wikimedia portal",
                 "wikimedia set index article", "wikimedia internal item", "scholarly article", "clinical trial")
NOISE_TITLE = re.compile(r"^(list|outline|glossary|timeline|index|lists) of ", re.I)
AMBIGUITY_RATIO = 0.25   # 2nd sense has >= 25% of the top sense's wiki count ...
AMBIGUITY_MIN_WIKIS = 10  # ... and at least 10 language editions -> ask the user
MAX_REDIRECTS = 20        # redirects per article that get their own monthly series
MAX_REDIRECTS_SCAN = 200  # redirects per article considered at all


def wiki_api(lang: str) -> str:
    return f"https://{lang}.wikipedia.org/w/api.php"


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).casefold().strip()
    return re.sub(r"\s+", " ", s)


def _sitelink_lang(key: str) -> str | None:
    if not key.endswith("wiki"):
        return None
    lang = key[:-4].replace("_", "-")
    return None if lang in NON_WIKIPEDIA else lang


def entities(http, qids: list[str], label_langs=("en",)) -> dict[str, dict]:
    out = {}
    qids = list(dict.fromkeys(qids))
    for i in range(0, len(qids), 50):
        body = http.get_json(WD_API, {
            "action": "wbgetentities", "ids": "|".join(qids[i:i + 50]), "props": "sitelinks|labels|descriptions",
            "languages": "|".join(dict.fromkeys(("en", "mul") + tuple(label_langs))), "format": "json"})
        for qid, e in (body or {}).get("entities", {}).items():
            if "missing" in e:
                continue
            sl = {}
            for key, v in e.get("sitelinks", {}).items():
                lang = _sitelink_lang(key)
                if lang:
                    sl[lang] = v["title"]
            labels = {k: v["value"] for k, v in e.get("labels", {}).items()}
            descs = {k: v["value"] for k, v in e.get("descriptions", {}).items()}
            # since 2024 many items keep their default name under "mul" (multiple languages)
            for lang in label_langs:
                labels.setdefault(lang, labels.get("mul")) if labels.get("mul") else None
            labels = {k: v for k, v in labels.items() if v}
            out[qid] = {"qid": qid, "labels": labels,
                        "label": labels.get("en") or labels.get("mul") or next(iter(labels.values()), qid),
                        "description": descs.get("en") or next(iter(descs.values()), ""), "descriptions": descs,
                        "sitelinks": sl, "wikis": len(sl)}
    return out


def _is_internal(ent: dict) -> bool:
    d = ent.get("descriptions", {}).get("en", "").lower()
    return any(d.startswith(x) for x in INTERNAL_DESC) or NOISE_TITLE.match(ent.get("label", "") or "") is not None


def search(http, topic: str, lang_hint: str, langs: list[str]) -> tuple[list[dict], dict]:
    """wbsearchentities in the hint language, then English. Returns (hits, entities)."""
    hits, seen = [], set()
    for lang in dict.fromkeys([lang_hint, "en"]):
        body = http.get_json(WD_API, {"action": "wbsearchentities", "search": topic, "language": lang,
                                      "uselang": lang, "type": "item", "limit": 12, "format": "json"}, ttl_days=7)
        for h in (body or {}).get("search", []):
            if h["id"] not in seen:
                seen.add(h["id"])
                hits.append(h)
        if any(_norm(h.get("match", {}).get("text", "")) == _norm(topic) for h in hits):
            break
    ents = entities(http, [h["id"] for h in hits], label_langs=(lang_hint,)) if hits else {}
    return hits, ents


def choose(topic: str, hits: list[dict], ents: dict) -> tuple[str, str | None, list[dict]]:
    """Deterministic decision. Returns (status, qid, candidates) where status is
    resolved | ambiguous | no_exact_match | not_found."""
    viable = []
    for h in hits:
        e = ents.get(h["id"])
        if not e or e["wikis"] == 0 or _is_internal(e):
            continue
        exact = _norm(h.get("match", {}).get("text", "")) == _norm(topic) or _norm(h.get("label", "")) == _norm(topic)
        viable.append({"qid": e["qid"], "label": h.get("label") or e["label"],
                       "description": h.get("description") or e["description"], "wikis": e["wikis"], "exact": exact})
    if not viable:
        return "not_found", None, []
    exact = sorted([c for c in viable if c["exact"]], key=lambda c: -c["wikis"])
    strip = lambda cs: [{k: c[k] for k in ("qid", "label", "description", "wikis")} for c in cs]  # noqa: E731
    if not exact:
        return "no_exact_match", None, strip(sorted(viable, key=lambda c: -c["wikis"])[:6])
    top = exact[0]
    rivals = [c for c in exact[1:]
              if c["wikis"] >= AMBIGUITY_RATIO * top["wikis"] and c["wikis"] >= AMBIGUITY_MIN_WIKIS]
    if rivals:
        senses = [c for c in exact if c["wikis"] >= 0.1 * top["wikis"]][:6]
        return "ambiguous", None, strip(senses)
    return "resolved", top["qid"], strip(exact[1:4])


def expand(http, qid: str, langs: list[str], k: int = 5) -> tuple[list[dict], list[dict]]:
    """Related items for a topic cluster. Structural Wikidata relations first
    (subclass of / part of the topic, its 'has part'), ranked by how many
    Wikipedias cover them; then 'morelike' search neighbours. Only items with an
    article in EVERY requested language are eligible, so languages stay comparable."""
    structural = []
    for prop in ("P279", "P361"):
        body = http.get_json(WD_API, {"action": "query", "list": "search", "srsearch": f"haswbstatement:{prop}={qid}",
                                      "srlimit": 50, "srnamespace": 0, "srprop": "", "format": "json"})
        structural += [x["title"] for x in (body or {}).get("query", {}).get("search", [])]
    body = http.get_json(WD_API, {"action": "wbgetclaims", "entity": qid, "property": "P527", "format": "json"})
    for c in (body or {}).get("claims", {}).get("P527", []):
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if isinstance(v, dict) and v.get("id"):
            structural.append(v["id"])
    topic = entities(http, [qid]).get(qid, {"sitelinks": {}})
    pivot = "en" if "en" in topic["sitelinks"] else next((l for l in langs if l in topic["sitelinks"]), None)
    similar = []
    if pivot:
        body = http.get_json(wiki_api(pivot), {
            "action": "query", "generator": "search", "gsrsearch": f"morelike:{topic['sitelinks'][pivot]}",
            "gsrlimit": 20, "gsrnamespace": 0, "prop": "pageprops", "ppprop": "wikibase_item", "format": "json",
            "formatversion": 2})
        pages = sorted((body or {}).get("query", {}).get("pages", []), key=lambda p: p.get("index", 0))
        similar = [p["pageprops"]["wikibase_item"] for p in pages if p.get("pageprops", {}).get("wikibase_item")]
    ents = entities(http, structural + similar)
    ok = lambda e: (e["qid"] != qid and not _is_internal(e)  # noqa: E731
                    and all(l in e["sitelinks"] for l in langs))
    s_rank = sorted([ents[q] for q in dict.fromkeys(structural) if q in ents and ok(ents[q])],
                    key=lambda e: -e["wikis"])
    m_rank = [ents[q] for q in dict.fromkeys(similar) if q in ents and ok(ents[q])]
    ordered = list({e["qid"]: e for e in s_rank + m_rank}.values())
    brief = lambda e: {"qid": e["qid"], "label": e["label"], "description": e["description"][:80]}  # noqa: E731
    return [brief(e) for e in ordered[:k]], [brief(e) for e in ordered[k:k + 6]]


def redirects_batch(http, lang: str, titles: list[str]) -> dict[str, dict]:
    """One query for many titles. {input title: {"title": canonical|None, "redirects": [...]}}"""
    mapping = {t: t for t in titles}
    pages: dict[str, list[str]] = {}
    missing = set()
    cont: dict = {}
    for _ in range(10):  # continuation pages (rdlimit=max is 500 per page)
        body = http.get_json(wiki_api(lang), {
            "action": "query", "titles": "|".join(titles), "prop": "redirects", "rdnamespace": 0, "rdlimit": "max",
            "redirects": 1, "format": "json", "formatversion": 2, **cont})
        q = (body or {}).get("query", {})
        hops = {x["from"]: x["to"] for x in q.get("normalized", []) + q.get("redirects", [])}
        for t in titles:
            cur = t
            for _hop in range(3):
                cur = hops.get(cur, cur)
            mapping[t] = cur
        for pg in q.get("pages", []):
            if pg.get("missing") or pg.get("invalid"):
                missing.add(pg.get("title"))
                continue
            pages.setdefault(pg["title"], []).extend(r["title"] for r in pg.get("redirects", []))
        if "continue" not in (body or {}):
            break
        cont = {k: v for k, v in body["continue"].items()}
    return {t: {"title": None if mapping[t] in missing or mapping[t] not in pages else mapping[t],
                "redirects": pages.get(mapping[t], [])} for t in titles}


def recent_views(http, lang: str, titles: list[str]) -> dict[str, int]:
    """Views over the last 60 days (PageViewInfo), 50 titles per request. Used only to
    decide which redirects deserve a full monthly series."""
    out: dict[str, int] = {}
    for i in range(0, len(titles), 50):
        chunk = titles[i:i + 50]
        cont: dict = {}
        for _ in range(10):
            body = http.get_json(wiki_api(lang), {
                "action": "query", "titles": "|".join(chunk), "prop": "pageviews", "pvipdays": 60,
                "format": "json", "formatversion": 2, **cont}, ttl_days=7)
            for pg in (body or {}).get("query", {}).get("pages", []):
                pv = pg.get("pageviews")
                if pv is not None:
                    out[pg["title"]] = out.get(pg["title"], 0) + sum(v or 0 for v in pv.values())
            if "continue" not in (body or {}):
                break
            cont = dict(body["continue"])
    return out


def alternatives(http, lang: str, query: str, n: int = 3) -> list[str]:
    """Closest existing articles when the topic has no article in a language."""
    body = http.get_json(wiki_api(lang), {"action": "query", "list": "search", "srsearch": query, "srlimit": n,
                                          "srnamespace": 0, "srprop": "", "format": "json"})
    return [x["title"] for x in (body or {}).get("query", {}).get("search", [])]


def article_set(http, lang: str, titles: list[str]) -> dict:
    """Canonical titles + ranked redirects for titles in one language.

    Every redirect would cost one pageview request, so redirects are ranked by their
    views in the last 60 days: 'major' ones (>= 1% of the article, >= 10 views) are
    always counted, 'minor' ones only if the article looks renamed inside the window."""
    info = redirects_batch(http, lang, titles) if titles else {}
    canon, not_found = [], []
    for t in titles:
        c = info.get(t, {}).get("title")
        if c is None:
            not_found.append(t)
        elif c not in canon:
            canon.append(c)
    rd_by_title = {v["title"]: v["redirects"] for v in info.values() if v["title"]}
    truncated = [a for a in canon if len(rd_by_title.get(a, [])) > MAX_REDIRECTS_SCAN]
    all_rd = {a: rd_by_title.get(a, [])[:MAX_REDIRECTS_SCAN] for a in canon}
    views = recent_views(http, lang, canon + [r for rds in all_rd.values() for r in rds]) if canon else {}
    major, minor = {}, {}
    for a, rds in all_rd.items():
        floor = max(10, 0.01 * views.get(a, 0))
        ranked = sorted(rds, key=lambda r: -views.get(r, 0))
        major[a] = [r for r in ranked if views.get(r, 0) >= floor][:MAX_REDIRECTS]
        minor[a] = [r for r in ranked if r not in major[a]][:MAX_REDIRECTS]
    return {"articles": canon, "not_found": not_found, "redirects": major, "redirects_minor": minor,
            "truncated": truncated}


def resolve_lang(http, cluster: list[dict], lang: str, topic_label: str) -> dict:
    """Articles, redirects and gaps of one language for a cluster of QIDs."""
    ents = entities(http, [c["qid"] for c in cluster])
    wanted = [(c, ents.get(c["qid"], {}).get("sitelinks", {}).get(lang)) for c in cluster]
    out = article_set(http, lang, [t for _, t in wanted if t])
    out["missing"] = [{"qid": c["qid"], "label": c.get("label")} for c, t in wanted
                      if not t or t in out["not_found"]]
    if cluster and cluster[0]["qid"] in {m["qid"] for m in out["missing"]}:
        out["alternatives"] = alternatives(http, lang, topic_label)
    return out
