---
name: wiki-interest-analyzer
description: Measures and compares public interest in a topic across Wikipedia language editions from Wikimedia pageview data, with a trust verdict (trend + confidence + caveats), charts and a one-page PDF report. Use when founders or product teams ask which topics, courses or features to invest in, which languages or markets to launch in, whether interest in something is growing in a given language, or ask to compare interest across Wikipedias (e.g. "Is interest in astronomy growing in Ukrainian Wikipedia?", "Compare intermittent fasting in Polish vs Czech"), including follow-ups that add a language or change the period. Not for news monitoring, sales forecasts or non-Wikipedia data.
compatibility: Needs uv (it installs Python 3.12 and locked dependencies on first run), bash, and internet access to wikimedia.org, wikidata.org and *.wikipedia.org. Tested on macOS and Linux.
metadata:
  version: "0.1.0"
---

# Wiki Interest Analyzer

Turns "is interest in X growing, and where?" into numbers you can trust or distrust for stated
reasons. The code does all data work: it finds the right article in every language (via
Wikidata), sums redirects, divides by the whole edition's traffic, fits robust trends, detects
spikes and seasonality, and returns a verdict with caveats in JSON. Your job: pick commands,
ask when a topic is ambiguous, and relay the results faithfully.

Do not use it for: sales/demand estimates (page views are curiosity, not purchases), daily news
tracking, or questions that don't involve Wikipedia.

## Setup and how to run

`SKILL_DIR` = the directory containing this file. Every command is:

```bash
SKILL_DIR/scripts/wia <command> ...        # e.g. /path/to/wiki-interest-analyzer/scripts/wia analyze --spec ...
```

The first run installs dependencies (~30 s). Network commands can take 1–3 min when nothing
is cached, so give Bash a 300000 ms timeout. Output is one JSON object on stdout.
All users share one Wikimedia rate budget unless `WIA_CONTACT` is set. If `network.rate_limited` > 0, tell
the user that `export WIA_CONTACT=<their e-mail>` gives them their own budget (faster runs).
Outputs go to `./wia-runs/<topic>/` in the current directory (spec.json, analysis.json,
series.csv, charts, report.pdf).

## Rules (why in brackets)

1. **Never write your own analysis code or report files** (no Python, pandas or matplotlib, no hand-made
   report.md). Every number, chart and PDF comes from `wia`. [The statistics and caveats are tested; ad-hoc code is not.]
2. **Always `resolve` before `analyze`.** If `status` is `ambiguous`, STOP and ask the user which
   meaning they want, listing the candidates' descriptions. Then rerun with `--qid`. Never guess.
   For `no_exact_match`: pick a candidate only if it clearly matches, otherwise ask.
3. **Report trend and confidence exactly as the JSON says**, with `confidence_reason`. Never
   upgrade "no clear trend"/`flat` to growth or decline (not even "slight decline"), and never drop the confidence level.
   Use the label word as given (`низька`/`low`), never "дуже низька", "very low" or "moderate".
4. **Copy every line of `must_relay` into the reply, word for word.** The code has already picked the top
   caveats and the "curiosity, not willingness to pay" line. Don't shorten, merge or skip them.
   [Replies that picked caveats themselves often left them out.]
5. **"No article" is a content gap, not zero interest.** Say so, and still run `analyze` for the other
   languages: don't stop to ask. Don't substitute another article unless the user asks for it; you may offer
   `alternatives` as proxies at the end of the reply.
6. **Follow-ups reuse the spec**: use `spec set` and then `analyze`, and don't re-run `resolve`.
   Cached months are never downloaded again.
7. **Numbers and reasons come from the JSON only.** Explain rankings by quoting `ranking[].why`. Don't
   compute new numbers (ratios, "N times more", sums), and don't add market facts, demographics or
   guesses the tool didn't return. [Haiku runs that did this contradicted the data, e.g. "8× more views"
   when the real ratio was 3.6×.]
8. **Language:** answer in the user's language. Pass `--report-lang uk` if the user writes
   Ukrainian, otherwise `en` (the `answer` field and the PDF use that language).

## Decision table

| User asks | Commands (S = spec path printed by resolve) |
|---|---|
| Is interest in X growing in language L? | `resolve --topic "X" --langs L` → `analyze --spec S` |
| Broad field or course (astronomy, programming) | same, add `--expand` (adds 5 related articles as a cluster) |
| Learning a language or skill | `resolve --topic "English language" --with "TOEFL; IELTS" --langs ...` (add learning-intent articles) |
| Compare languages / which audiences next | `resolve ... --langs a,b,c` → `analyze` (see `ranking`, `explore_next`) |
| A report ("звіт") / PDF / something to share | after analyze: `report --spec S` (optional `--note "one paragraph"`) |
| A chart only | `chart --spec S --kind share` (`absolute`, `yoy` also exist) |
| "last two years", "since 2020" | `--months 24` / `--start 2020-01` (default: last 36 complete months) |
| Follow-up: add or remove a language | `spec set S langs+=sk` (or `langs-=cs`) → `analyze --spec S` |
| Follow-up: change the period | `spec set S months=12` (or `start=2024-01`) → `analyze --spec S` |
| Follow-up: add or remove a cluster article | `spec set S cluster+=Q123` / `cluster-=Q123` → `analyze` |
| User picks a proxy article where one is missing | `spec set S "articles.pl=Głodówka lecznicza"` → `analyze` |
| Ambiguous topic answered by the user | `resolve --qid Q308 --langs ...` |

Use the English name of the topic when you can (`--topic "intermittent fasting"`); for a topic
written in Ukrainian add `--lang-hint uk`. Put the user's question in `--question "..."`
(it becomes the report title).

## Copy-paste examples

```bash
# Compare two languages over the last two years, answer in Ukrainian
SKILL_DIR/scripts/wia resolve --topic "intermittent fasting" --langs pl,cs --months 24 --report-lang uk --question "Інтерес до інтервального голодування: pl vs cs"
SKILL_DIR/scripts/wia analyze --spec wia-runs/intermittent-fasting/spec.json

# Broad topic as a cluster, then a PDF
SKILL_DIR/scripts/wia resolve --topic "astronomy" --expand --langs uk --report-lang uk
SKILL_DIR/scripts/wia analyze --spec wia-runs/astronomy/spec.json
SKILL_DIR/scripts/wia report --spec wia-runs/astronomy/spec.json

# Follow-up: "add Slovak and only the last 12 months"
SKILL_DIR/scripts/wia spec set wia-runs/intermittent-fasting/spec.json langs+=sk months=12
SKILL_DIR/scripts/wia analyze --spec wia-runs/intermittent-fasting/spec.json
```

## Reading the analyze JSON

- `answer`: 2–3 sentences written by the code in the report language. Relay or translate them;
  don't contradict them.
- `langs.<code>`:
  - `trend` (`growing` | `flat` = no clear trend | `declining`) and `confidence` (`high` | `medium` | `low`), with `confidence_reason`.
  - `slope_pct_per_year` is the compound change per year of the topic's *share* of all views in that edition. `slope_ci_pct` is its 95% interval.
  - `yoy_pct` compares the last 12 months with the 12 before, with one-off spikes removed; `yoy_pct_raw` keeps them.
    When they differ, quote both ("+40% without the spike, +438% with it"); growth that exists only in `yoy_pct_raw` came from the spike.
  - `level_per_million` is the topic's views per million views of that edition (comparable across languages). `avg_monthly_views` is raw views (NOT comparable across languages).
  - `spikes[]`: `kind` is `one-off` (news/events) or `recurring` (seasonal).
  - `status: no_article` means a content gap.
- `must_relay`: the lines your reply must contain verbatim (rule 4). `caveats`: the full list, most important first. `ranking` (each entry has a ready-made `why`) and `explore_next` exist when there are 2+ languages.
- `network.pageview_requests_by_project` shows what was downloaded; after a follow-up, only new languages should appear.

## Writing the reply

1. The direct answer (from `answer`), in the user's language.
2. Per language, one line: level, trend with %/yr, confidence and its reason. For "which audiences next",
   name `explore_next` and give each language's `ranking[].why` as the reason. If `explore_next` is empty,
   say that no language stands out yet (as `answer` does). Don't make up your own priority order.
3. A limitations list: every `must_relay` line verbatim, one bullet each.
4. Paths of any chart or PDF you made.
5. One useful next step (e.g. add a language, widen the period, check a sub-topic).

Shape (written in the user's language):

```
<answer>
- <language>: <level_per_million>/M, <trend> (<slope_pct_per_year>%/yr), confidence <label> — <confidence_reason>
**Limitations:**
- <must_relay[0]>
- … every other must_relay line
Files: <paths>. Next: <one next step>
```

If you add your own interpretation, keep it to one paragraph and label it as yours. It must not
contradict the verdict.

## More detail (read only when needed)

- Why a verdict has a given confidence, and how to explain it → [references/methodology.md](references/methodology.md)
- Known data traps (Ukrainian 2022 shift, falling traffic, bots, renames) → [references/pitfalls.md](references/pitfalls.md)
- Endpoints, rate limits, caching, errors → [references/api.md](references/api.md)
