"""The analysis spec (spec.json): the unit of state between turns.

Follow-ups edit the spec and re-run. Only languages whose articles are unknown
(new language, changed cluster) are resolved; everything else is reused.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import resolve as rs
from common import LANG_RE, QID_RE, WiaError, add_months, last_complete_month, parse_month

ACCESS = {"all-access", "desktop", "mobile-web", "mobile-app"}
AGENTS = {"user", "all-agents", "automated", "spider"}
SCALARS = {"start", "end", "months", "report_lang", "question", "access", "agent", "topic_label", "normalize",
           "granularity"}
LISTS = {"langs", "cluster"}
ASSIGN_RE = re.compile(r"^([a-z_]+(?:\.[a-z][a-z0-9-]*)?)\s*(\+=|-=|=)(.*)$", re.S)


def load(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise WiaError(f"Spec not found: {p}", hint="Create one with: wia resolve --topic ... --langs ...")
    return json.loads(p.read_text(encoding="utf-8"))


def save(path: str | Path, spec: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")


def new_spec(*, qid, labels, cluster, langs, start, end, report_lang, question) -> dict:
    return {
        "version": 1, "question": question or "", "topic_qid": qid, "topic_label": labels.get("en") or qid,
        "topic_labels": labels, "cluster": cluster, "langs": langs, "start": start, "end": end,
        "granularity": "monthly", "access": "all-access", "agent": "user", "normalize": "share_of_project",
        "report_lang": report_lang, "articles": {}, "redirects": {}, "missing": {}, "alternatives": {},
        "redirects_minor": {}, "truncated": {}, "resolved_for": {}, "manual_langs": [],
    }


def _effective_end(spec) -> str:
    end = spec.get("end", "latest")
    lcm = last_complete_month()
    return lcm if end == "latest" or end > lcm else end


def _split(key: str, value: str) -> list[str]:
    sep = "|" if key.startswith("articles.") else ","
    return [v.strip() for v in value.split(sep) if v.strip()]


def apply_set(spec: dict, assignments: list[str], http=None) -> list[str]:
    """Mutates spec. Returns human-readable change list."""
    changes = []
    for a in assignments:
        m = ASSIGN_RE.match(a.strip())
        if not m:
            raise WiaError(f"Cannot parse '{a}'",
                           hint="Use key=value, key+=value or key-=value, e.g. langs+=sk months=12")
        key, op, value = m.group(1), m.group(2), m.group(3).strip()
        if key in LISTS or key.startswith("articles."):
            items = _split(key, value)
            if not items:
                raise WiaError(f"'{a}' has no value", hint="e.g. langs+=sk or cluster+=Q123")
            if key == "langs":
                for l in items:
                    if not LANG_RE.match(l):
                        raise WiaError(f"Bad language code '{l}'", hint="Use Wikipedia codes: pl, cs, sk, uk, de ...")
                cur = list(spec.get("langs", []))
            elif key == "cluster":
                for q in items:
                    if not QID_RE.match(q):
                        raise WiaError(f"Bad QID '{q}'", hint="Cluster items are Wikidata ids like Q333")
                cur = [c["qid"] for c in spec.get("cluster", [])]
            else:
                cur = list(spec.get("articles", {}).get(key.split(".", 1)[1], []))
            if op == "=":
                new = items
            elif op == "+=":
                new = cur + [i for i in items if i not in cur]
            else:
                new = [c for c in cur if c not in items]
            if key == "langs":
                if not new:
                    raise WiaError("langs cannot be empty")
                spec["langs"] = new
            elif key == "cluster":
                if not new:
                    raise WiaError("cluster cannot be empty")
                labels = {c["qid"]: c.get("label") for c in spec.get("cluster", [])}
                missing_labels = [q for q in new if q not in labels]
                if missing_labels and http is not None:
                    ents = rs.entities(http, missing_labels)
                    labels.update({q: ents.get(q, {}).get("label", q) for q in missing_labels})
                spec["cluster"] = [{"qid": q, "label": labels.get(q, q)} for q in new]
                if not spec.get("topic_qid") or spec["topic_qid"] not in new:
                    spec["topic_qid"] = new[0]
            else:
                lang = key.split(".", 1)[1]
                _set_manual_articles(spec, lang, new, http)
            changes.append(f"{key} {op} {value}")
            continue
        if key not in SCALARS:
            raise WiaError(f"Unknown key '{key}'",
                           hint="Keys: langs, cluster, articles.<lang>, start, end, months, report_lang, question")
        if op != "=":
            raise WiaError(f"'{op}' only works on lists (langs, cluster, articles.<lang>)")
        if key == "months":
            if not value.isdigit() or int(value) < 1:
                raise WiaError("months must be a positive integer")
            end = _effective_end(spec)
            spec["end"] = end
            spec["start"] = add_months(end, -(int(value) - 1))
            changes.append(f"window = {spec['start']}..{end} ({value} months)")
            continue
        if key in ("start", "end"):
            value = value if (key == "end" and value == "latest") else parse_month(value)
        elif key == "access" and value not in ACCESS:
            raise WiaError(f"access must be one of {sorted(ACCESS)}")
        elif key == "agent" and value not in AGENTS:
            raise WiaError(f"agent must be one of {sorted(AGENTS)}")
        elif key == "normalize" and value != "share_of_project":
            raise WiaError("Only normalize=share_of_project is supported (raw views are always reported alongside).")
        elif key == "granularity" and value != "monthly":
            raise WiaError("Only monthly granularity is supported (see ROADMAP.md for daily).")
        spec[key] = value
        changes.append(f"{key} = {value}")
    return changes


def _set_manual_articles(spec, lang, titles, http):
    """User-chosen titles for one language (e.g. a proxy article where the topic has no article)."""
    if http is None:
        raise WiaError("internal: http required")
    r = rs.article_set(http, lang, titles)
    if r["not_found"]:
        raise WiaError(f"No article {r['not_found']} in {lang}.wikipedia",
                       hint="Check the exact title (case matters after the first letter).")
    for k in ("articles", "redirects", "redirects_minor", "missing", "truncated", "resolved_for"):
        spec.setdefault(k, {})
    spec["articles"][lang] = r["articles"]
    spec["redirects"][lang] = r["redirects"]
    spec["redirects_minor"][lang] = r["redirects_minor"]
    spec["truncated"][lang] = r["truncated"]
    spec["missing"][lang] = []
    spec.setdefault("manual_langs", [])
    if lang not in spec["manual_langs"]:
        spec["manual_langs"].append(lang)


def sync(spec: dict, http) -> list[str]:
    """Resolve articles only for languages that need it. Returns resolved languages."""
    for k in ("articles", "redirects", "redirects_minor", "missing", "alternatives", "truncated", "resolved_for"):
        spec.setdefault(k, {})
    langs = spec["langs"]
    cluster_ids = [c["qid"] for c in spec.get("cluster", [])]
    for k in ("articles", "redirects", "redirects_minor", "missing", "alternatives", "truncated", "resolved_for"):
        for l in list(spec[k]):
            if l not in langs:
                del spec[k][l]
    spec["manual_langs"] = [l for l in spec.get("manual_langs", []) if l in langs]
    done = []
    label = spec.get("topic_label") or ""
    for l in langs:
        if l in spec["manual_langs"] or spec["resolved_for"].get(l) == cluster_ids:
            continue
        r = rs.resolve_lang(http, spec["cluster"], l, label)
        spec["articles"][l] = r["articles"]
        spec["redirects"][l] = r["redirects"]
        spec["missing"][l] = r["missing"]
        spec["truncated"][l] = r["truncated"]
        spec["redirects_minor"][l] = r["redirects_minor"]
        if r.get("alternatives"):
            spec["alternatives"][l] = r["alternatives"]
        else:
            spec["alternatives"].pop(l, None)
        spec["resolved_for"][l] = cluster_ids
        done.append(l)
    return done


def summary(spec: dict) -> dict:
    """Compact view of a spec for stdout."""
    return {
        "topic": f"{spec.get('topic_label')} ({spec.get('topic_qid')})",
        "cluster": [f"{c['qid']} {c.get('label')}" for c in spec.get("cluster", [])],
        "langs": spec["langs"], "window": f"{spec['start']}..{spec['end']}", "report_lang": spec.get("report_lang"),
        "articles": {l: spec.get("articles", {}).get(l, []) for l in spec["langs"]},
        "redirects_counted": {l: sum(len(v) for v in spec.get("redirects", {}).get(l, {}).values())
                              for l in spec["langs"]},
        "no_article": {l: [m.get("label") for m in spec.get("missing", {}).get(l, [])]
                       for l in spec["langs"] if spec.get("missing", {}).get(l)},
        "alternatives": {l: v for l, v in spec.get("alternatives", {}).items() if v},
    }
