"""Narrative templates. The texts live in assets/report_template.json (en, uk);
this module only looks them up and fills placeholders. Unknown report languages
fall back to English."""
from __future__ import annotations

import json

from common import SKILL_DIR

_TPL = json.loads((SKILL_DIR / "assets" / "report_template.json").read_text(encoding="utf-8"))
SUPPORTED = tuple(_TPL["languages"])
LANG_NAMES = {k: (v["en"], v.get("uk", v["en"])) for k, v in _TPL["language_names"].items()}
MONTH_NAMES = _TPL["month_names"]
T = _TPL["text"]
FRAGMENTS = _TPL["fragments"]
CAVEATS = {k: (v["severity"], {lang: txt for lang, txt in v.items() if lang != "severity"})
           for k, v in _TPL["caveats"].items()}
SEVERITY_ORDER = {"high": 0, "medium": 1, "info": 2}


def rl_of(report_lang: str | None) -> str:
    return report_lang if report_lang in SUPPORTED else "en"


def lang_name(code: str, rl: str) -> str:
    en, uk = LANG_NAMES.get(code, (code, code))
    return uk if rl == "uk" else en


def wiki_subject(code: str, rl: str) -> str:
    """'Polish Wikipedia' / 'польська Вікіпедія'."""
    name = lang_name(code, rl)
    if rl == "uk":
        return f"{name} Вікіпедія" if name.endswith("а") else f"Вікіпедія мовою {name}"
    return f"{name} Wikipedia"


def wiki_in(code: str, rl: str) -> str:
    """'in Polish Wikipedia' / 'у польській Вікіпедії' (Ukrainian adjectives in -ька decline to -ькій)."""
    name = lang_name(code, rl)
    if rl == "uk":
        if not name.endswith("а"):
            return f"у Вікіпедії мовою {name}"
        prep = "в" if name[0] in "аеєиіїоуюя" else "у"
        return f"{prep} {name[:-1]}ій Вікіпедії"
    return f"in {name} Wikipedia"


def pct(x, nd=0) -> str:
    return "n/a" if x is None else f"{x:+.{nd}f}%"


def t(key: str, rl: str, **kw) -> str:
    row = T[key]
    s = row.get(rl) or row["en"]
    return s.format(**kw) if kw else s


def caveat_text(code: str, rl: str, params: dict) -> str:
    _, texts = CAVEATS[code]
    p = dict(params)
    lang = p.get("lang")
    if lang:
        subj = wiki_subject(lang, rl)
        p.setdefault("wiki_cap", subj[0].upper() + subj[1:])
        p.setdefault("wiki_subj", subj)
    if code == "seasonal_peaks":
        names = MONTH_NAMES.get(rl, MONTH_NAMES["en"])
        p["cal"] = ", ".join(names[int(m) - 1] for m in p["cal_months"])
    if code == "uk_2022_shift":
        p["span"] = FRAGMENTS["uk_2022_span"][rl] if p.get("spans_2022") else ""
    if code == "article_created_in_window":
        key = "article_created_trimmed" if p.get("trimmed") else "article_created_excluded"
        p["action"] = FRAGMENTS[key][rl]
    return (texts.get(rl) or texts["en"]).format(**p)


def severity(code: str) -> str:
    return CAVEATS[code][0]
