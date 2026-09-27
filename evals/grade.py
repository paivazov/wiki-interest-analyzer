#!/usr/bin/env python3
"""Grade eval runs programmatically.

Layout of one run (created by the eval driver; see 'How to re-run the evals' in DEVLOG.md):
  <runs>/<eval-name>/<config>/meta.json   {"eval_id", "config", "workspace", "transcript", "turn_start": [ts, ...]}
  <runs>/<eval-name>/<config>/reply.md    the agent's final reply (last turn)

Evidence used per check:
  artifact -> CLI invocation log lines whose cwd is inside the workspace + files the CLI wrote
  reply    -> reply.md (regexes, case-insensitive)
  both     -> PDF / transcript checks that apply to baseline runs too

Usage: uv run evals/grade.py <runs-dir> [--log ~/.cache/wiki-interest-analyzer/invocations.jsonl]
Writes grading.json next to each meta.json and prints a markdown summary.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OWN_CODE = re.compile(r"\b(pandas|matplotlib|numpy|scipy|statsmodels|reportlab|fpdf|plotly|seaborn)\b|"
                      r"(curl|wget|requests\.get|urllib)[^\n]*(wikimedia\.org|wikipedia\.org|wikidata\.org)", re.I)

# "page views = curiosity, not willingness to pay", in uk or en, verbatim or paraphrased
CURIOSITY = [r"цікав\w*[^.\n]{0,80}(?:плат|попит|купів)", r"(?:платити|попит\w*)[^.\n]{0,80}цікав",
             r"curiosity[^.\n]{0,80}(?:pay|demand|purchas|buy)", r"(?:pay|demand)[^.\n]{0,80}curiosity",
             r"≠\s*(?:готовн|попит|willingness|demand|purchas)"]
# one regex per caveat code (uk + en): the reply relays that caveat if it matches
CAVEAT_KEYWORDS = {
    "no_article": r"не\s?має статті|відсутн\w* стат|стат\w* (?:немає|відсутн)|прогалин|no article|content gap",
    "insufficient_data": r"замало\s+(?:\w+\s+)?(?:даних|місяц)|недостатньо даних|too (?:few|little)|insufficient data",
    "no_yoy_short_window": r"рік до року|рік-до-року|year.over.year|\bYoY\b",
    "weak_significance": r"не доведен|not conclusive|suggestive|Манна|Mann.Kendall",
    "wide_interval": r"шум|noise|95\s?%|\bДІ\b|\bCI\b|інтервал|interval",
    "spike_driven_growth": r"сплеск|spike|олімпі|olympi",
    "articles_disagree": r"статей кластера|з \d+ статей|of \d+ articles|articles? in the (?:topic )?cluster|"
                         r"які статті враховувати|which articles you count",
    "possible_bot_spike": r"\bбот|автоматизован|\bbots?\b|automated",
    "low_volume": r"переглядів на місяць|views (?:per|a) month|малі числа|small numbers",
    "small_volume": r"переглядів на місяць|views (?:per|a) month|невеликі числа|modest numbers",
    "signals_disagree": r"різні боки|different directions|суперечать|розходяться|disagree",
    "edition_wide_growth": r"весь мовний розділ|всього (?:мовного )?розділу|whole (?:language )?edition|edition.wide",
    "edition_traffic_declining": r"загальн\w* перегляд\w*[^.\n]{0,40}пада|трафік\w*[^.\n]{0,30}(?:пада|скороч|знижу)|"
                                 r"пошуковик|search engines|\bШІ\b|\bAI answer|edition traffic|views are falling",
    "uk_2022_shift": r"2022",
    "partial_cluster": r"лише \d+ з \d+ статей|only \d+ of the \d+|бракує|не повністю порівнюван|not fully comparable",
    "article_created_in_window": r"з['’]явил\w* лише|appeared only|нов\w* стат|new article",
}
# confidence labels as the CLI renders them (uk stems + en), and words that change a label
LEVELS = {"high": r"висок\w*|high", "medium": r"середн\w*|medium", "low": r"низьк\w*|low"}
NOT_A_LABEL = r"помірн\w*|moderate"
CONF_WORD = r"довір\w*|впевнен\w*|надійн\w*|confidence"
INTENSIFIER = re.compile(r"^(?:дуже|вкрай|украй|надзвичайно|досить|доволі|відносно|порівняно|помірно|вельми|"
                         r"very|extremely|fairly|relatively|quite|moderately|rather)$", re.I)
_LEVEL_ANY = "|".join(LEVELS.values()) + "|" + NOT_A_LABEL
_OF_WHAT = r"(?:\s+(?:до|у|в|щодо|of|in|for)\s+(?:\w+\s+)?\w+)?"  # "довіра до (цього) висновку: низька"
CONF_AFTER = re.compile(rf"(?:{CONF_WORD}){_OF_WHAT}\s*[:—–-]?\s*(?:(\w+)\s+)?\b({_LEVEL_ANY})\b", re.I)
CONF_BEFORE = re.compile(rf"(?:\b(\w+)\s+)?\b({_LEVEL_ANY})[\s-]+(?:{CONF_WORD})", re.I)    # низька довіра


def load_log(path: Path, workspace: str) -> list[dict]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if str(e.get("cwd", "")).startswith(workspace):
            out.append(e)
    return out


def ts(e) -> float:
    return dt.datetime.fromisoformat(e["ts"]).timestamp()


def tool_calls(transcript: Path | None) -> list[dict]:
    calls = []
    if not transcript or not transcript.exists():
        return calls

    def walk(o):
        if isinstance(o, dict):
            if o.get("type") == "tool_use":
                calls.append({"name": o.get("name"), "input": o.get("input", {})})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for line in transcript.read_text(encoding="utf-8").splitlines():
        try:
            walk(json.loads(line))
        except ValueError:
            pass
    return calls


def transcript_before(text: str, cut: float) -> str:
    """Transcript lines written before `cut` (epoch); lines carry ISO UTC timestamps."""
    keep = []
    for line in text.splitlines():
        m = re.search(r'"timestamp":"([^"]+)"', line)
        if m and dt.datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).timestamp() < cut:
            keep.append(line)
    return "\n".join(keep)


def find_one(workspace: Path, pattern: str) -> Path | None:
    hits = sorted(workspace.rglob(pattern), key=lambda p: p.stat().st_mtime)
    return hits[-1] if hits else None


def verdict_state(a: dict, ctx: dict, ana: dict | None) -> dict | None:
    """What analyze/report told the agent in the graded turn: the last such log entry in that turn.
    Logs written before these fields existed fall back to analysis.json (only valid for the last turn)."""
    turns = ctx["turn_start"]
    calls = [e for e in ctx["log"] if e.get("argv") and e["argv"][0] in ("analyze", "report")]
    earlier = "before_turn" in a and len(turns) > a["before_turn"]
    if earlier:
        calls = [e for e in calls if ts(e) < turns[a["before_turn"]]]
    elif "after_turn" in a and len(turns) > a["after_turn"]:
        calls = [e for e in calls if ts(e) >= turns[a["after_turn"]]]
    entries = [e for e in calls if "confidence" in e]
    if entries:
        return entries[-1]
    if not calls:
        return None
    if earlier or not ana:
        return {"old_log": True}
    return {"confidence": {l: r["confidence"] for l, r in ana["results"].items() if r.get("status") == "ok"},
            "caveat_codes": sorted({c["code"] for c in ana.get("caveats", []) if c["severity"] != "info"}),
            "must_relay_codes": [c["code"] for c in ana.get("must_relay", [])]}


def confidence_mentions(reply: str) -> list[tuple[str, str]]:
    """(word before the label, label) for every confidence label written next to a confidence word."""
    plain = re.sub(r"[*_`]", "", reply)  # markdown emphasis: "**Впевненість:** Низька"
    return [(m.group(1) or "", m.group(2)) for rx in (CONF_AFTER, CONF_BEFORE) for m in rx.finditer(plain)]


def check(a: dict, ctx: dict) -> tuple[bool | None, str]:
    kind = a["check"]
    ws: Path = ctx["workspace"]
    log = ctx["log"]
    reply = ctx["reply"]
    turns = ctx["turn_start"]
    after = turns[a["after_turn"]] if "after_turn" in a and len(turns) > a["after_turn"] else None
    log_after = [e for e in log if after is None or ts(e) >= after]
    spec_p = find_one(ws, "spec.json")
    spec = json.loads(spec_p.read_text()) if spec_p else None
    ana_p = find_one(ws, "analysis.json")
    ana = json.loads(ana_p.read_text()) if ana_p else None

    if kind == "regex_all":
        miss = [p for p in a["patterns"] if not re.search(p, reply, re.I)]
        return not miss, f"missing: {miss}" if miss else "all matched"
    if kind == "regex_any":
        hit = next((p for p in a["patterns"] if re.search(p, reply, re.I)), None)
        return hit is not None, f"matched {hit!r}" if hit else "no pattern matched"
    if kind == "regex_none":
        hit = next((p for p in a["patterns"] if re.search(p, reply, re.I)), None)
        return hit is None, f"forbidden {hit!r} found" if hit else "none found"
    if kind == "no_own_analysis_code":
        calls = ctx["calls"]
        if not calls:
            return None, "no transcript"
        bad = []
        for c in calls:
            inp = json.dumps(c["input"], ensure_ascii=False)
            if c["name"] in ("Write", "Edit") and str(c["input"].get("file_path", "")).endswith((".py", ".ipynb")):
                bad.append(f"{c['name']} {c['input'].get('file_path')}")
            elif c["name"] == "Bash" and OWN_CODE.search(inp):
                bad.append(f"Bash uses {OWN_CODE.search(inp).group(0)[:40]!r}")
        if bad:
            return False, f"{len(bad)} violation(s): {bad[:2]}"
        return True, f"{len(calls)} tool calls, none write analysis code"
    if kind == "pdf_pages" or kind == "pdf_regex":
        pdf = find_one(ws, "*.pdf")
        if not pdf:
            return False, "no PDF in workspace"
        import pypdf
        r = pypdf.PdfReader(str(pdf))
        if kind == "pdf_pages":
            return len(r.pages) == a["equals"], f"{pdf.name}: {len(r.pages)} page(s)"
        text = " ".join(p.extract_text() or "" for p in r.pages)
        hit = next((p for p in a["patterns"] if re.search(p, text)), None)
        return hit is not None, f"matched {hit!r}" if hit else "no limitations section found"

    # ---------------- artifact checks (need the skill's files / log)
    if kind == "spec_field":
        return (spec or {}).get(a["field"]) == a["equals"], f"{a['field']}={(spec or {}).get(a['field'])!r}"
    if kind == "spec_langs_include":
        got = (spec or {}).get("langs", [])
        return set(a["langs"]) <= set(got), f"langs={got}"
    if kind == "spec_cluster_min":
        n = len((spec or {}).get("cluster", []))
        return n >= a["min"], f"cluster size {n}"
    if kind == "spec_missing_lang":
        miss = (spec or {}).get("missing", {}).get(a["lang"])
        return bool(miss), f"missing[{a['lang']}]={miss}"
    if kind == "analysis_months":
        if "before_turn" in a and len(ctx["turn_start"]) > a["before_turn"]:
            # state at the end of an earlier turn: last analyze/report logged before the next turn started
            cut = ctx["turn_start"][a["before_turn"]]
            prior = [e for e in log if ts(e) < cut and e.get("months")]
            if prior:
                n = prior[-1]["months"]
                return n == a["equals"], f"{n} months (log, before turn {a['before_turn'] + 1})"
            q = r'\\?"'  # quotes may be JSON-escaped inside the transcript
            text = transcript_before(ctx["transcript_text"], cut)
            m = re.search(rf'{q}period{q}:\{{{q}start{q}:{q}[\d-]+{q},{q}end{q}:{q}[\d-]+{q},{q}months{q}:(\d+)', text)
            if m:
                return int(m.group(1)) == a["equals"], f"{m.group(1)} months (first analyze output in transcript)"
            return False, f"no analyze before turn {a['before_turn'] + 1}"
        n = len((ana or {}).get("months", []))
        return n == a["equals"], f"{n} months"
    if kind == "caveats_relayed":
        st = verdict_state(a, ctx, ana)
        if st is None:
            return False, "no analyze/report in this turn, so nothing was relayed"
        if st.get("old_log"):
            return None, "log predates verdict fields; not gradable"
        curiosity = any(re.search(p, reply, re.I) for p in CURIOSITY)
        codes = [c for c in st["caveat_codes"] if c in CAVEAT_KEYWORDS]
        hit = [c for c in codes if re.search(CAVEAT_KEYWORDS[c], reply, re.I)]
        relay = [c for c in st.get("must_relay_codes", []) if c != "curiosity_not_demand"]
        ok = curiosity and (len(hit) >= a.get("min", 1) or not codes)
        return ok, (f"curiosity line: {'yes' if curiosity else 'NO'}; caveats relayed: {hit or 'none'} of {codes}; "
                    f"must_relay covered {len(set(relay) & set(hit))}/{len(relay)}")
    if kind == "confidence_label_exact":
        st = verdict_state(a, ctx, ana)
        if st is None:
            return False, "no analyze/report in this turn, so no verdict to quote"
        if st.get("old_log"):
            return None, "log predates verdict fields; not gradable"
        want = set(st["confidence"].values())
        said, bad = set(), []
        for before, label in confidence_mentions(reply):
            lvl = next((k for k, rx in LEVELS.items() if re.fullmatch(rx, label, re.I)), None)
            if INTENSIFIER.match(before) or lvl not in want:
                bad.append(f"{before} {label}".strip())
            else:
                said.add(lvl)
        missing = sorted(want - said)
        ok = not bad and not missing
        return ok, (f"JSON {sorted(want)}; reply {sorted(said) or 'none'}"
                    + (f"; not as given: {bad}" if bad else "") + (f"; not stated: {missing}" if missing else ""))
    if kind == "analysis_has_caveat":
        codes = [c["code"] for c in (ana or {}).get("caveats", [])]
        return a["code"] in codes, f"caveats={sorted(set(codes))}"
    if kind == "analysis_lang_field":
        v = (ana or {}).get("results", {}).get(a["lang"], {}).get(a["field"])
        return v == a["equals"], f"{a['lang']}.{a['field']}={v!r}"
    if kind == "log_has_command":
        hits = [e for e in log_after if e["argv"] and e["argv"][0] == a["command"]]
        return bool(hits), f"{len(hits)} `{a['command']}` call(s)"
    if kind == "log_lacks_command":
        hits = [e for e in log_after if e["argv"] and e["argv"][0] == a["command"]]
        return not hits, f"{len(hits)} `{a['command']}` call(s)"
    if kind == "log_status":
        hits = [e for e in log_after if e["argv"] and e["argv"][0] == a["command"]]
        sts = [e.get("status") for e in hits]
        return a["status"] in sts, f"statuses={sts}"
    if kind == "followup_network_only":
        seen = {}
        for e in log_after:
            for proj, n in ((e.get("network") or {}).get("pageview_requests_by_project") or {}).items():
                seen[proj] = seen.get(proj, 0) + n
        bad = {p: n for p, n in seen.items() if p not in a["allowed_projects"] and n}
        return not bad, f"pageview requests after turn 1: {seen or 'none'}"
    return None, f"unknown check {kind}"


def grade_run(meta_path: Path, evals: dict, log_path: Path) -> dict:
    meta = json.loads(meta_path.read_text())
    ev = evals[meta["eval_id"]]
    run_dir = meta_path.parent
    ws = Path(meta["workspace"])
    ctx = {
        "workspace": ws,
        "reply": (run_dir / "reply.md").read_text(encoding="utf-8") if (run_dir / "reply.md").exists() else "",
        "log": load_log(log_path, str(ws)), "turn_start": meta.get("turn_start", []),
        "calls": tool_calls(Path(meta["transcript"]) if meta.get("transcript") else None),
        "transcript_text": Path(meta["transcript"]).read_text(encoding="utf-8")
        if meta.get("transcript") and Path(meta["transcript"]).exists() else "",
    }
    results = []
    for a in ev["assertions"]:
        if meta["config"] == "baseline" and a["kind"] == "artifact":
            continue  # skill-only evidence
        ok, why = check(a, ctx)
        results.append({"id": a["id"], "text": a["text"], "passed": ok, "evidence": why})
    graded = [r for r in results if r["passed"] is not None]
    out = {"eval_id": ev["id"], "name": ev["name"], "config": meta["config"], "results": results,
           "passed": sum(1 for r in graded if r["passed"]), "total": len(graded),
           "wia_calls": [" ".join(e["argv"][:2]) + f" -> {e.get('status')}" for e in ctx["log"]],
           "tool_calls": len(ctx["calls"])}
    (run_dir / "grading.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs")
    ap.add_argument("--log", default=str(Path.home() / ".cache" / "wiki-interest-analyzer" / "invocations.jsonl"))
    args = ap.parse_args()
    evals = {e["id"]: e for e in json.loads((HERE / "evals.json").read_text())["evals"]}
    rows = [grade_run(m, evals, Path(args.log)) for m in sorted(Path(args.runs).rglob("meta.json"))]
    rows.sort(key=lambda r: (r["eval_id"], r["config"]))
    print("| eval | config | passed | failed checks |\n|---|---|---|---|")
    tot = {}
    for r in rows:
        fails = [f"{x['id']} ({x['evidence']})" for x in r["results"] if x["passed"] is False]
        print(f"| {r['eval_id']} {r['name']} | {r['config']} | {r['passed']}/{r['total']} "
              f"| {'; '.join(fails) or '—'} |")
        t = tot.setdefault(r["config"], [0, 0])
        t[0] += r["passed"]
        t[1] += r["total"]
    for c, (p, n) in tot.items():
        print(f"\n**{c}: {p}/{n} checks passed ({100 * p / max(n, 1):.0f}%)**")


if __name__ == "__main__":
    sys.exit(main())
