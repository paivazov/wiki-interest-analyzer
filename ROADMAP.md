# Roadmap: from single questions to larger studies

The current skill answers "topic × a few languages × a few years" in seconds to a couple of minutes,
through the per-article API. Below is how to grow it, in the order I would do it. Each step keeps
the principle: **all data work in tested code, the agent only picks commands and explains.**

## 1. Bigger data: bulk dumps + DuckDB/Parquet

*Trigger:* hundreds of articles, 10-year windows, or daily granularity. At about 150 req/min the
per-article API becomes the bottleneck (500 series ≈ 3–4 min), and daily series multiply the volume by 30.

- Ingest the monthly **pageview complete dumps** (`dumps.wikimedia.org/other/pageview_complete/monthly/`,
  one file per month, all projects and articles, user/automated split) with a `wia ingest --months 2016-01..`
  command. Filter to the languages of interest while streaming and write **Parquet partitioned by
  project/month**.
- Replace the SQLite `pageviews` table with **DuckDB** over those Parquet files. The cache key
  (project, article, agent, access, month) stays the same, so `analyze.py` does not change. Only `fetch.py`
  gets a second backend, chosen automatically: API for fewer than about 50 series, dumps otherwise.
- Add daily granularity from the same dumps. Spike detection then works at day level (sharper bot detection:
  a one-day, one-article spike with a flat referrer mix).

## 2. Better topics: Wikidata-driven expansion

*Trigger:* users ask about fields ("fintech", "mental health") rather than single articles.

- `--expand` today uses P279/P361/P527 plus `morelike` and ranks by how many Wikipedias cover an item.
  Next: a SPARQL-based expansion (`subclass of*` up to depth 2, `main subject`, `facet of`), plus
  **category trees** per language, ranked by pageviews from the dumps (cheap once step 1 exists).
- Store clusters as named, versioned files (`clusters/astronomy.json`) so a team reuses the same
  definition across studies and runs a sensitivity check ("does the verdict hold if we drop the top
  article?"). `articles_agree` is the first version of that check.

## 3. Where readers come from: clickstream

The monthly **clickstream** dumps (en, de, fr, es, ja, ru, fa, it, pl, zh, pt…) give referrer → article
pairs: search, internal links and external sites. That separates *searched* interest (people looking
for the topic) from *navigated* interest (arriving via a news article or a link). It also gives a much better
bot filter and an "intent" signal. Add it as `wia sources --spec S`, with a caveat when more than X%
of the growth arrives from one referrer.

## 4. Portfolio ranking: many topics × many languages

Founders really want "rank these 20 course ideas across these 8 markets". Add a `portfolio.json`
spec (a list of topic specs) and `wia portfolio --spec P`. It outputs a topics × languages matrix of
level, trend and confidence, one heatmap, and a 1-page summary with the top opportunities and "no article"
gaps. Ranking stays deterministic and documented; the confidence filter from `explore_next` becomes a
parameter.

## 5. Per-language seasonality models

YoY on matching months and recurring-peak detection handle seasonality coarsely. With 5+ years per
series, fit STL (seasonal-trend decomposition) per language edition. Seasonality differs by country:
school years start in September in Ukraine and Poland, and in August or September for most of Germany;
the southern hemisphere is shifted. Test the trend on the deseasonalised series, and use the Seasonal
Mann–Kendall test when there are at least 3 years. Keep the current rules as the fallback for short windows.

## 6. An MCP server around the same core

Expose `resolve / analyze / chart / report / spec_set` as MCP tools backed by the same Python modules
(the CLI becomes one thin client, the MCP server another). Gains: a persistent, shared cache and
rate-limit budget for a whole team; specs stored server-side with history; and typed tool schemas,
which are even easier for small models than CLI flags. The skill then documents the workflow, and the
server does the work.

## 7. Evals that grow with the skill

- Grow `evals/evals.json` from 7 to about 30 cases. Cover each caveat code at least once (bot spike,
  rename, a new article, small volume, partial cluster), non-Latin scripts (ja, ar, hi), and multi-turn
  follow-ups (remove a language, swap a cluster article, "why is confidence low?").
- Freeze the data for assertions: record the API responses of each eval (the cache DB is already a
  recording) and set `WIA_TODAY`, so evals are reproducible and don't break when real traffic changes.
- Run the same suite on other cheap models (Haiku, plus free OpenRouter models such as Llama or Qwen
  variants through an OpenAI-compatible tool-calling harness). Track pass rate per model, and treat any
  instruction the weakest model keeps missing as a SKILL.md bug.
- Add an LLM-judge pass only for what regexes can't check: "is the reply consistent with the verdict
  JSON?", "is the interpretation paragraph non-contradictory?"
