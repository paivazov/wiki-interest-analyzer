#!/usr/bin/env python3
"""wiki-interest-analyzer CLI. Every command prints one compact JSON object on stdout.

  resolve  --topic "..." | --qid Q..   --langs pl,cs [--lang-hint uk] [--expand] [--months 24]
  analyze  --spec SPEC
  chart    --spec SPEC [--kind share|absolute|yoy]
  report   --spec SPEC [--out report.pdf] [--lang uk] [--note "..."]
  spec     set SPEC key=value ... | show SPEC
  cache    info | clear
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze as an  # noqa: E402
import resolve as rs  # noqa: E402
import spec as sp  # noqa: E402
from cache import Cache  # noqa: E402
from common import (LANG_RE, QID_RE, WiaError, add_months, cache_dir, emit, last_complete_month,  # noqa: E402
                    parse_month, slugify)
from fetch import Http, Pacer  # noqa: E402

WIA = "wia"  # how commands are shown in "next" hints


def _langs(s: str) -> list[str]:
    langs = [l.strip().lower() for l in s.split(",") if l.strip()]
    for l in langs:
        if not LANG_RE.match(l):
            raise WiaError(f"Bad language code '{l}'", hint="Use Wikipedia codes: pl, cs, sk, uk, de, tr, vi ...")
    if not langs:
        raise WiaError("--langs is empty")
    return langs


# ---------------------------------------------------------------- commands

def cmd_resolve(args, http) -> dict:
    langs = _langs(args.langs)
    hint = (args.lang_hint or "en").lower()
    candidates: list[dict] = []
    if args.qid:
        if not QID_RE.match(args.qid):
            raise WiaError(f"Bad QID '{args.qid}'")
        qid = args.qid
    else:
        hits, ents = rs.search(http, args.topic, hint, langs)
        status, qid, candidates = rs.choose(args.topic, hits, ents)
        if status != "resolved":
            msg = {
                "ambiguous": "Several different topics match. ASK the user which one they mean, then rerun with --qid.",
                "no_exact_match": "No exact match. If one candidate clearly is the user's topic, rerun with --qid; "
                                  "otherwise ask the user or try another phrasing (English usually works best).",
                "not_found": "Nothing found. Try the English name or a more common phrasing.",
            }[status]
            return {"status": status, "query": args.topic, "candidates": candidates, "next": msg}

    label_langs = tuple(dict.fromkeys(["en", hint, args.report_lang or "en"] + langs))
    ent = rs.entities(http, [qid], label_langs=label_langs).get(qid)
    if not ent:
        raise WiaError(f"Wikidata item {qid} not found")
    cluster = [{"qid": qid, "label": ent["label"]}]
    for name in [x.strip() for x in (args.with_topics or "").split(";") if x.strip()]:
        if QID_RE.match(name):
            wq = name
        else:
            hits, ents = rs.search(http, name, hint, langs)
            st, wq, cands = rs.choose(name, hits, ents)
            if st != "resolved":
                return {"status": "with_unresolved", "topic": name, "candidates": cands,
                        "next": "Pick the right QID from candidates and pass it in --with instead of the name "
                                "(ask the user if unsure)."}
        if wq not in {c["qid"] for c in cluster}:
            cluster.append({"qid": wq, "label": rs.entities(http, [wq]).get(wq, {}).get("label", wq)})
    other_related = []
    if args.expand:
        related, other_related = rs.expand(http, qid, langs, k=args.related)
        cluster += [{"qid": r["qid"], "label": r["label"]} for r in related]
    ents = rs.entities(http, [c["qid"] for c in cluster], label_langs=label_langs)
    for c in cluster:  # labels in the report language, for the PDF header
        c["labels"] = {l: v for l, v in ents.get(c["qid"], {}).get("labels", {}).items() if l in label_langs}

    lcm = last_complete_month()
    end = parse_month(args.end) if args.end else lcm
    end = min(end, lcm)
    start = parse_month(args.start) if args.start else add_months(end, -(args.months - 1))
    labels = {l: ent["labels"][l] for l in label_langs if l in ent["labels"]}
    spec = sp.new_spec(qid=qid, labels=labels, cluster=cluster, langs=langs, start=start, end=end,
                       report_lang=(args.report_lang or "en"), question=args.question)
    sp.sync(spec, http)
    path = Path(args.spec) if args.spec else Path("wia-runs") / slugify(ent["label"]) / "spec.json"
    sp.save(path, spec)
    out = {"status": "resolved", "qid": qid, "label": ent["label"], "description": ent["description"][:120],
           **sp.summary(spec), "spec": str(path)}
    if candidates:
        out["other_senses"] = candidates
    if other_related:
        out["more_related"] = other_related
    gaps = [l for l in langs if not spec["articles"].get(l)]
    if gaps:
        out["note"] = (f"No article at all in: {', '.join(gaps)}. Run analyze now; it reports this as a content gap, "
                       "not zero interest. Don't stop to ask about substitutes: at the end of your reply you may "
                       "offer `alternatives` as proxies (spec set <spec> articles.<lang>=Title if the user picks one).")
    out["next"] = f"{WIA} analyze --spec {path}"
    return out


def cmd_analyze(args, http) -> dict:
    spec = sp.load(args.spec)
    a = an.run_analysis(spec, http)
    files = an.write_outputs(a, Path(args.spec).parent)
    return dict(an.compact(a, files), _log=an.log_summary(a))


def cmd_chart(args, http) -> dict:
    import charts

    spec = sp.load(args.spec)
    a = an.run_analysis(spec, http)
    out = Path(args.out) if args.out else Path(args.spec).parent / f"chart_{args.kind}.png"
    charts.render(a, args.kind, out)
    return {"status": "ok", "chart": str(out), "kind": args.kind}


def cmd_report(args, http) -> dict:
    import report

    spec = sp.load(args.spec)
    if args.lang:
        spec["report_lang"] = args.lang
    a = an.run_analysis(spec, http)
    out = Path(args.out) if args.out else Path(args.spec).parent / "report.pdf"
    files = an.write_outputs(a, Path(args.spec).parent)
    info = report.render(a, out, note=args.note)
    return {"status": "ok", "pdf": str(out), **info, "answer": an.answer_text(a),
            "must_relay": [c["text"] for c in an.must_relay(a)], "files": files, "_log": an.log_summary(a)}


def cmd_spec(args, http) -> dict:
    spec = sp.load(args.path)
    if args.action == "show":
        return {"status": "ok", **sp.summary(spec), "spec": args.path}
    changes = sp.apply_set(spec, args.assignments, http=http)
    resolved = sp.sync(spec, http)
    sp.save(args.path, spec)
    return {"status": "ok", "changes": changes, "resolved_langs": resolved, **sp.summary(spec), "spec": args.path,
            "next": f"{WIA} analyze --spec {args.path}"}


def cmd_cache(args, http) -> dict:
    if args.action == "clear":
        http.cache.clear()
    return {"status": "ok", **http.cache.info()}


# ---------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wia", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("resolve", help="topic -> Wikidata item -> articles per language; writes the spec")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--topic", help="free-text topic, any language")
    g.add_argument("--qid", help="Wikidata id, e.g. Q308 (use after an 'ambiguous' answer)")
    r.add_argument("--langs", required=True, help="Wikipedia language codes, comma-separated: pl,cs")
    r.add_argument("--lang-hint", help="language the topic is written in (default en)")
    r.add_argument("--expand", action="store_true", help="add related articles to form a topic cluster")
    r.add_argument("--related", type=int, default=5, help="how many related articles --expand adds (default 5)")
    r.add_argument("--with", dest="with_topics",
                   help="extra cluster topics, ';'-separated names or QIDs, e.g. \"TOEFL; IELTS\"")
    r.add_argument("--months", type=int, default=36, help="window length ending at the last complete month")
    r.add_argument("--start", help="YYYY-MM (overrides --months)")
    r.add_argument("--end", help="YYYY-MM (default: last complete month)")
    r.add_argument("--report-lang", help="language of answers and report: uk or en (default en)")
    r.add_argument("--question", help="the user's question, printed as the report title")
    r.add_argument("--spec", help="where to write the spec (default wia-runs/<topic>/spec.json)")

    for name in ("analyze", "chart", "report"):
        s = sub.add_parser(name)
        s.add_argument("--spec", required=True)
        if name == "chart":
            s.add_argument("--kind", choices=["share", "absolute", "yoy"], default="share")
            s.add_argument("--out")
        if name == "report":
            s.add_argument("--out")
            s.add_argument("--lang", help="report language override: uk or en")
            s.add_argument("--note", help="optional analyst paragraph (max 600 chars); must not contradict the verdict")

    s = sub.add_parser("spec", help="edit or show a spec")
    ssub = s.add_subparsers(dest="action", required=True)
    s_set = ssub.add_parser("set")
    s_set.add_argument("path")
    s_set.add_argument("assignments", nargs="+", help="key=value | key+=value | key-=value")
    s_show = ssub.add_parser("show")
    s_show.add_argument("path")

    c = sub.add_parser("cache")
    c.add_argument("action", choices=["info", "clear"])
    return p


def _log(entry: dict) -> None:
    try:
        path = Path(os.environ.get("WIA_LOG") or cache_dir() / "invocations.jsonl")
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)
    if getattr(args, "note", None) and len(args.note) > 600:
        emit({"status": "error", "error": "--note is limited to 600 characters"})
        return 2
    t0 = time.time()
    http = Http(Cache(), pacer=Pacer(cache_dir() / "pace.sqlite"))
    code, result = 0, None
    try:
        fn = {"resolve": cmd_resolve, "analyze": cmd_analyze, "chart": cmd_chart, "report": cmd_report,
              "spec": cmd_spec, "cache": cmd_cache}[args.cmd]
        result = fn(args, http)
        if args.cmd != "cache":
            result["network"] = http.stats()
    except WiaError as e:
        code = 3 if e.code == "network" else 2
        result = {"status": "error", "error": str(e), "hint": e.hint, "network": http.stats()}
    extra = result.pop("_log", {})
    emit(result)
    _log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "cwd": os.getcwd(), "argv": argv, "status": result.get("status"),
          "network": result.get("network"), "resolved_langs": result.get("resolved_langs"),
          "months": (result.get("period") or {}).get("months"), **extra,
          "seconds": round(time.time() - t0, 2)})
    return code


if __name__ == "__main__":
    sys.exit(main())
