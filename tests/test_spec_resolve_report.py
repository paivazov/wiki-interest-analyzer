"""spec editing, topic resolution decisions, the one-page PDF and CLI error handling."""
import json

import pypdf
import pytest

import analyze as an
import resolve as rs
import spec as sp
from common import WiaError
from conftest import months_list, synth


# ------------------------------------------------------------------ spec
def base_spec():
    return sp.new_spec(qid="Q1", labels={"en": "topic"}, cluster=[{"qid": "Q1", "label": "topic"}],
                       langs=["pl", "cs"], start="2023-09", end="2026-08", report_lang="uk", question="")


def test_months_sets_window_ending_at_end():
    s = base_spec()
    sp.apply_set(s, ["months=12"])
    assert (s["start"], s["end"]) == ("2025-09", "2026-08")


def test_list_operators():
    s = base_spec()
    sp.apply_set(s, ["langs+=sk", "langs-=cs"])
    assert s["langs"] == ["pl", "sk"]


@pytest.mark.parametrize("bad", ["foo=1", "langs+=", "months=0", "start=2024-13", "access=mobile",
                                 "normalize=none", "langs=PL!"])
def test_bad_assignments_raise_user_errors(bad):
    with pytest.raises(WiaError):
        sp.apply_set(base_spec(), [bad])


def test_sync_resolves_only_new_languages(http, fake):
    fake.sitelinks["Q1"] = {"pl": "Temat", "cs": "Tema", "sk": "Téma"}
    s = base_spec()
    assert sorted(sp.sync(s, http)) == ["cs", "pl"]
    sp.apply_set(s, ["langs+=sk"])
    before = len(fake.calls)
    assert sp.sync(s, http) == ["sk"]
    assert all("pl.wikipedia" not in c and "cs.wikipedia" not in c for c in fake.calls[before:])
    assert s["articles"]["sk"] == ["Téma"]


def test_sync_marks_missing_article_and_suggests_alternatives(http, fake):
    fake.sitelinks["Q1"] = {"cs": "Tema"}
    s = base_spec()
    sp.sync(s, http)
    assert s["articles"]["pl"] == []
    assert s["missing"]["pl"][0]["qid"] == "Q1"
    assert s["alternatives"]["pl"] == ["Nearest article"]


def test_redirects_split_into_major_and_minor_by_recent_views(http, fake):
    fake.sitelinks["Q1"] = {"pl": "Temat"}
    fake.redirects[("pl", "Temat")] = ["Popular alias", "Typo"]
    fake.recent.update({("pl", "Temat"): 50_000, ("pl", "Popular alias"): 4_000, ("pl", "Typo"): 3})
    out = rs.resolve_lang(http, [{"qid": "Q1", "label": "topic"}], "pl", "topic")
    assert out["redirects"]["Temat"] == ["Popular alias"]
    assert out["redirects_minor"]["Temat"] == ["Typo"]


# ------------------------------------------------------------------ resolve decisions
def hit(qid, label, text=None):
    return {"id": qid, "label": label, "description": "", "match": {"type": "label", "text": text or label}}


def ent(qid, wikis, desc=""):
    return {"qid": qid, "label": qid, "description": desc, "descriptions": {"en": desc}, "labels": {},
            "sitelinks": {f"l{i}": "x" for i in range(wikis)}, "wikis": wikis}


def test_mercury_is_ambiguous():
    hits = [hit("Q308", "Mercury"), hit("Q925", "mercury"), hit("Q1150", "Mercury"), hit("Q9", "Mercury Records")]
    ents = {"Q308": ent("Q308", 251), "Q925": ent("Q925", 176), "Q1150": ent("Q1150", 84), "Q9": ent("Q9", 30)}
    status, qid, cands = rs.choose("Mercury", hits, ents)
    assert status == "ambiguous" and qid is None
    assert [c["qid"] for c in cands] == ["Q308", "Q925", "Q1150"]


def test_dominant_sense_resolves():
    hits = [hit("Q333", "astronomy"), hit("Q3232273", "Astronomy")]
    ents = {"Q333": ent("Q333", 200), "Q3232273": ent("Q3232273", 9)}
    assert rs.choose("Astronomy", hits, ents)[:2] == ("resolved", "Q333")


def test_disambiguation_pages_and_papers_are_ignored():
    hits = [hit("Q1", "Foo"), hit("Q2", "Foo"), hit("Q3", "Foo")]
    ents = {"Q1": ent("Q1", 40, "Wikimedia disambiguation page"), "Q2": ent("Q2", 30),
            "Q3": ent("Q3", 0, "scholarly article")}
    assert rs.choose("Foo", hits, ents)[:2] == ("resolved", "Q2")


def test_no_exact_match_is_not_guessed():
    hits = [hit("Q5", "Learning English as a second language")]
    status, qid, cands = rs.choose("learning english", hits, {"Q5": ent("Q5", 20)})
    assert status == "no_exact_match" and qid is None and cands


# ------------------------------------------------------------------ report
@pytest.fixture
def analysis(http, fake):
    months = months_list("2023-09", 36)
    for lang, lvl in (("uk", 3000), ("pl", 8000)):
        fake.totals[f"{lang}.wikipedia"] = {m: 60_000_000 for m in months}
        fake.pages[(f"{lang}.wikipedia", "A")] = dict(zip(months, synth(36, lvl, 0.15, 0.2, 0.05, seed=1).astype(int)))
    s = base_spec()
    s.update(langs=["uk", "pl", "cs"], articles={"uk": ["A"], "pl": ["A"], "cs": []},
             missing={"cs": [{"qid": "Q1", "label": "topic"}]}, topic_labels={"uk": "тема"},
             question="Чи зростає інтерес до теми?")
    return an.run_analysis(s, http)


def test_report_is_exactly_one_page_with_limitations(analysis, tmp_path):
    import report

    out = tmp_path / "r.pdf"
    report.render(analysis, out, note="Коментар. " * 55)
    reader = pypdf.PdfReader(str(out))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    assert "Що це означає і чого не означає" in text
    assert "готовність платити" in text  # curiosity != demand, always present
    assert "немає статті" in text


def test_report_stays_one_page_with_many_caveats(analysis, tmp_path):
    import report

    for l in ("uk", "pl"):
        analysis["results"][l]["caveats"] += [("low_volume", {"lang": l, "views": 10})] * 20
    out = tmp_path / "r.pdf"
    report.render(analysis, out)
    assert len(pypdf.PdfReader(str(out)).pages) == 1


def test_english_report(analysis, tmp_path):
    import report

    analysis["rl"] = "en"
    out = tmp_path / "r.pdf"
    report.render(analysis, out)
    assert "What this does and doesn't tell you" in pypdf.PdfReader(str(out)).pages[0].extract_text()


def test_must_relay_is_top_caveats_then_curiosity_line(analysis):
    rel = an.must_relay(analysis)
    assert [c["code"] for c in rel] == ["no_article", "uk_2022_shift", "curiosity_not_demand"]  # most severe first
    out = an.compact(analysis, {})
    assert out["must_relay"] == [c["text"] for c in rel]
    assert "не має статті" in out["must_relay"][0] and "готовність платити" in out["must_relay"][-1]
    assert an.log_summary(analysis)["confidence"].keys() == {"uk", "pl"}  # cs has no article


def test_must_relay_covers_different_kinds_of_caveats(analysis):
    analysis["results"]["uk"]["caveats"] += [("low_volume", {"lang": "uk", "views": v}) for v in (10, 20, 30)]
    codes = [c["code"] for c in an.must_relay(analysis)]
    assert len(codes) == 4 and codes.count("low_volume") == 1
    assert set(codes[:2]) == {"low_volume", "no_article"} and codes[2:] == ["uk_2022_shift", "curiosity_not_demand"]


# ------------------------------------------------------------------ CLI
def test_cli_error_is_json_with_hint(tmp_path, capsys):
    import wia

    code = wia.main(["analyze", "--spec", str(tmp_path / "missing.json")])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and out["status"] == "error" and out["hint"]


def test_cli_logs_invocations(tmp_path, capsys, monkeypatch):
    import wia

    wia.main(["cache", "info"])
    log = (tmp_path / "log.jsonl").read_text().strip().splitlines()
    assert json.loads(log[-1])["argv"] == ["cache", "info"]


# ------------------------------------------------------------------ templates
def test_every_template_has_all_languages_with_same_placeholders():
    import re
    import i18n

    ph = lambda s: {x.replace("_cap", "") for x in re.findall(r"{(\w+)", s)}  # noqa: E731  (x_cap = capitalised x)
    for code, (sev, texts) in i18n.CAVEATS.items():
        assert sev in i18n.SEVERITY_ORDER, code
        assert set(texts) >= set(i18n.SUPPORTED), code
        assert len({frozenset(ph(t)) for t in texts.values()}) == 1, code
    for key, texts in i18n.T.items():
        assert set(texts) >= set(i18n.SUPPORTED), key
        assert len({frozenset(ph(t)) for t in texts.values()}) == 1, key


def test_all_caveat_codes_emitted_by_code_exist_in_templates():
    import pathlib
    import re
    import i18n

    src = "".join(p.read_text() for p in pathlib.Path("scripts").glob("*.py"))
    # every ("code", {...}) tuple appended anywhere; "low"/"medium"/"high" are confidence caps, not caveats
    tuples = re.findall(r'[\[(,]\s*\(\s*"([a-z][a-z_0-9]+)",\s*(?:\{|params\b)', src)  # ("code", {...}) in a list/call
    emitted = set(tuples) - {"low", "medium", "high"}
    assert {"article_created_in_window", "spike_driven_growth", "no_article"} <= emitted
    assert emitted and emitted <= set(i18n.CAVEATS), emitted - set(i18n.CAVEATS)
