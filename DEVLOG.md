# DEVLOG — how this skill was built and how the AI-generated parts were verified

The code, docs and evals were written with an AI coding agent (Claude Code, Opus) directing the work.
The skill targets **Claude Haiku 4.5**, and every agent eval ran on Haiku. Below, per stage: what was
generated, how it was checked, and what the checks caught. "Caught" items are real defects found by the
checks, not hypotheticals.

Environment: macOS, uv 0.11, Python 3.12 (pinned in `.python-version`, deps locked in `uv.lock`).
Run the tests with `uv run pytest` from the skill directory.

---

## Stage 0: probe the real APIs before writing code

**Did:** called the Wikimedia pageviews API (per-article and aggregate), Wikidata `wbsearchentities` /
`wbgetentities`, `haswbstatement` search, `morelike` search and `prop=redirects` by hand with curl.

**Learned (and designed for):**
- Per-article requests return **404** for articles with no views in the range, so a 404 means "zero", not an error.
- **pl.wikipedia has no article on intermittent fasting** (Q1666254 has cs, uk… but no plwiki). The first
  example task therefore hits a real "no article" case, so the "content gap ≠ zero interest" rule was needed from day one.
- "Mercury" matches planet (251 wikis), element (176), god (84), a commune, and a car brand. Ambiguity
  can be decided deterministically from exact-label matches plus sitelink counts.
- Wikidata `P279/P361 = topic` (subclass/part of) gives sensible branches for "astronomy"
  (cosmology, astrophysics…); `morelike` adds neighbours.

## Stage 1: CLI skeleton, modules, unit tests on synthetic data

**Generated:** `scripts/` modules: `common`, `cache` (SQLite), `fetch` (HTTP + retry + cache), `stats`
(Theil–Sen, block bootstrap, Mann–Kendall, Hampel, YoY), `analyze` (pipeline + verdict), `resolve`,
`spec`, `charts`, `report`, and the `wia.py` CLI. Also `tests/` with a `FakeWiki` transport that
serves synthetic series and counts every request.

**How it was checked (60 tests, no network):**
- **Stats cross-checked against scipy/textbook values**, because hand-written statistics are an easy place
  for an LLM to be subtly wrong. Theil–Sen slope and intercept equal `scipy.stats.theilslopes` on 5 heavy-tailed
  random series. Mann–Kendall on 1..5 reproduces the textbook p = 0.0275, and its direction and p agree
  with `scipy.stats.kendalltau` on tied data. The bootstrap CI covers a known +15%/yr slope and is deterministic.
- **Verdict rules on series with a known answer:**
  - +25%/yr with seasonality → growing, high confidence, CI covers 25.
  - A single injected spike → `spike_driven_growth`, low confidence, the spike month named, durable trend flat.
  - September peaks every year → `seasonal_peaks`, not news.
  - 12 months → no YoY, confidence capped.
  - 120 views/month → low confidence.
  - Edition grows while the share is constant → `edition_wide_growth`, trend flat.
  - uk → `uk_2022_shift` always; confidence capped only if the window spans 2022.
  - A redirect carrying a one-month 200k burst → `possible_bot_spike`.
- **Pipeline with FakeWiki:** redirect views summed; the current month excluded; an unpublished last month
  is not cached as zero (and is fetched once published); no-article languages trigger no requests and rank
  as `content_gap`. A second run makes **0 requests**; adding a language requests **only that language**; a
  shorter window needs no network. A rename is recovered from a minor redirect; a genuinely new article trims the window.
- **Report:** the PDF has **exactly 1 page** (pypdf) even with 40 extra caveats and a 600-character note. It
  contains the limitations heading and the curiosity≠demand line, and Ukrainian text is extractable.
- **Templates:** every caveat and sentence exists in en and uk with the same placeholders. Every caveat code
  that the code can emit exists in the template file.

**Caught:**
- My own test asserted `annual_rate(-10) > -100`. The function correctly returns −100.0 (floating-point
  underflow of exp(−120)), so the test was wrong, not the code.
- `spec set langs+=` (empty value) silently did nothing. It now errors.
- The first version of the i18n module had fragment entries written as `({...})`, which is a dict, not a
  tuple, so indexing `[0]` would crash. It was found by rendering every template once before any test existed.

## Stage 2: real API end to end

**Checked by** running `resolve → analyze → chart → report` on the brief's topics, reading every JSON output,
looking at every chart and PDF as an image, and spot-checking numbers against the raw API with a separate script.

**Independent spot check (not using the skill's code):** for uk "astronomy" 2025-09 I summed 6 articles + 12
redirects directly from the API: 3 380 views, edition 61 256 706, share 55.178 per million. This is
**identical** to the pipeline's `series.csv` row.

**Caught on real data, and fixed:**
1. **Nonsense rates.** A linear slope divided by the median produced "−120 %/yr" for Ukrainian astronomy.
   Trends are now fitted on log(share) and reported as compound %/yr, which is bounded at −100%. The same
   series now reads −42 %/yr, CI [−49, −35], and YoY −44% agrees.
2. **School-year peaks flagged as news.** September peaks in 2023, 2024 and 2025 (the school year starts
   on 1 September) were "spikes". Outliers are now classified as *recurring* when the same calendar month
   is an outlier in another year.
3. **…but that rule hid the Olympics.** For Italian curling, the 54× Winter Olympics month (Feb 2026, held in
   Milan-Cortina) was called "recurring" because Feb 2025 had a small 2.5× bump, and YoY came out at
   +1280%. Recurring now also requires a similar size (within 3×, on raw ratios; a log-ratio version was
   tried first and was still too lenient).
4. **Direction claimed without evidence.** German curling read "declining" with a 95% CI of −47…+47 %/yr.
   A direction is now claimed only when the CI excludes 0; otherwise the result is "no clear trend", and a
   very wide interval lowers confidence.
5. **Rate limiting.** Bursts of HTTP 429. Wikimedia allows 10 req/min for clients without contact info and
   200 req/min with it. Fixes:
   - The User-Agent now carries a contact. The first version read it from a `contact.txt` holding the author's
     e-mail. That shipped a personal address in the repo, and it made every install send requests under it. It
     was replaced by the project URL, which Wikimedia accepts as contact info ("an email or full URL"), so the
     200/min tier still applies with no configuration. `WIA_CONTACT` adds the operator's own contact.
     Wikimedia's gateway keys the counter by the User-Agent contact and prefers an e-mail, so an e-mail there
     gets its own budget instead of the one all default installs share. Tests assert that the default
     User-Agent contains the URL and no `@`.
   - A cross-process pacer in SQLite (150 req/min shared by parallel runs) honours `Retry-After`. Concurrency
     went from 4 to 3, the maximum Wikimedia's rate-limit page asks for.
   - Redirects are fetched in one batched call and ranked by 60-day views, so only meaningful ones get a
     series (German "English language": 17 redirect series → 3).
6. **Wikidata `mul` labels.** Items whose default label is stored under `mul` showed up as bare QIDs. Now
   the code falls back to `mul`.
7. **Wording.** "trend no clear trend", "У українській" (now "В українській"), a duplicated "(confidence low)".
   Each was found by reading the generated answers.
8. **Environment.** The `VIRTUAL_ENV` warning leaked from the caller's venv; the wrapper now unsets it.

## Stage 3: SKILL.md and references

- `SKILL.md` (~140 lines) has a decision table (request → exact commands), copy-paste examples, rules with
  their reasons, a guide to reading the JSON, and a reply template. Methodology, pitfalls and API details
  live in `references/`, one level deep.
- Frontmatter uses spec fields only (`name`, `description`, `compatibility`, `metadata`). It was validated
  with the skill-creator validator and against agentskills.io/specification (read before writing): the name
  matches the directory, the description is 680 chars and compatibility 177, both under their limits.
- The narrative templates moved to `assets/report_template.json`, so a report language can be added without code.

## Stage 4: agent evals on Haiku 4.5

Setup: `evals/evals.json` has 7 cases and 51 programmatic checks (38 at first; checks were added
whenever reading the replies exposed a miss). Each run gets an empty workspace; the agent is told where the
skill is and to read SKILL.md, and nothing else. `evals/grade.py` checks:
- the CLI invocation log (every `wia` call is logged with argv, status and per-project request counts;
  `analyze`/`report` also log the verdict the agent saw: confidence per language, caveat codes, `must_relay` codes),
- the spec/analysis files and the PDF (page count, limitations text),
- the final reply (regexes),
- the transcript (Write/Edit of .py files, or Bash lines using pandas/matplotlib/numpy or fetching
  Wikimedia directly, count as "wrote its own analysis code").

Baseline = same prompt, same model, no skill.

### Results (details in `evals/results.md`)

| | Baseline (Haiku, no skill) | Iteration 1 | Iteration 2 | Iteration 3 (evals 3, 7) | Iteration 4 | Iteration 5 (final) |
|---|---|---|---|---|---|---|
| Checks passed | 8/23 (35%) | 38/39 (97%) | 39/39 (100%) | 12/12 | 44/51 (86%) | 48/51 (94%) |

Iterations 4 and 5 ran all 7 evals against 51 checks, so they are not comparable with the earlier columns.

In every with-skill run, Haiku used only `wia` commands (3–7 tool calls) and wrote no analysis code; the grader
confirmed this from the transcripts. The follow-up (eval 4) used `spec set langs+=sk months=12`: the invocation
log shows 0 pageview requests for pl/cs, and no `resolve` call. The baselines, run on the same model, fell
into the traps the skill encodes: the wrong article, absolute views across languages, the incomplete month,
a missing article reported as low interest, a spike read as growth, and a guessed ambiguous topic.

**Grader bugs found while grading (fixed; they were not skill failures):**
- Eval 1 and its follow-up (eval 4) share a workspace, so eval 1's "24-month window" check read the spec
  *after* the follow-up had changed it to 12 months. The grader now evaluates it at the end of turn 1, and
  the CLI now logs the analysed period.
- The "not zero interest" regex fired on the correct negated phrase "а не нульовий інтерес", and "0 переглядів"
  matched inside "29.0 переглядів". A negative lookbehind and a digit boundary fixed both.

**Quality issues in with-skill replies that the regex checks did not catch (read manually, then fixed and re-run):**
- Eval 3: when explaining *why* Turkish is next, Haiku invented reasons ("highest interest", although
  Vietnamese is 291/M against Turkish 81/M; "denser community"). Fix: the code now writes a `why` for every
  ranking entry, and SKILL.md has rule 7, "reasons come from the JSON only".
- Eval 7: Haiku said "+40% YoY is caused by the spike". In fact +40% is YoY *without* the spike and +438%
  *with* it. Fix: the answer sentence now says "+40% without one-off spikes (+438% with them)", and
  SKILL.md tells the agent to quote both.
- Iteration 2 → 3: Haiku still invented a ratio ("8× more views", real 3.6×) and called a "no clear trend"
  slope a "definite decline". SKILL.md now forbids computing new numbers and describing a flat slope as a
  direction. In iteration 3 both replies were faithful to the JSON.

### Iterations 4–5: caveats and confidence labels moved into code

Iteration 3's eval-3 reply skipped every caveat and still scored 6/6, because only eval 2 checked that a
caveat reached the reply. Iteration 2's eval-4 reply said "very low" where the JSON said "low". Fixes:
- **The code picks what the reply must say.** `analyze`/`report` print `must_relay`: the most severe caveats
  (at most 3, one per kind first) plus the curiosity≠willingness-to-pay line, in the report language. SKILL.md
  tells the agent to copy them word for word and names the forbidden label variants. The multi-language
  `answer` now says "довіра <label>" for every language, so the label always sits next to a confidence word.
- **New checks:** `caveats_relayed` (evals 1, 3, 6, 7), `confidence_label_exact` (evals 1–4, 6, 7) and
  `no_computed_ratios` (evals 3, 6).
- **The checks were validated on old replies before the new runs.** Re-grading the saved iteration-2/3
  replies fails exactly the problems found earlier by reading: iteration 3's caveat-less eval 3, iteration 2's
  "дуже низькою", and "8 разів більше". The first version of the label regex missed "Довіра до висновку:
  **низька**" (words and markdown between the label and the confidence word), which old eval-7 replies
  showed; it was widened before any new run was graded.

Iteration 4 relayed `must_relay` verbatim in every run that analysed data, but exposed two unrelated
regressions: eval 1 stopped to ask about a proxy article, and eval 3 hand-wrote `report.md` with an invented
"вдвічі більше". After instruction fixes (the resolve note, rules 1 and 5, an empty `explore_next`),
iteration 5 re-ran all 7 against the final SKILL.md: 48/51. The three misses are eval 3's handed-back summary;
its chat text had the full reply and scores 9/9. Details and remaining issues: `evals/results.md`.

## What I would verify next

- Freeze the eval data (cache DB + `WIA_TODAY`), so numeric assertions don't drift as months pass.
- Run the suite on a second cheap model (an OpenRouter free model) to find instructions that only Haiku
  happens to follow.
- Add LLM-judge checks for "reply consistent with the verdict JSON", the class of error found above by reading
  (still open: iteration 5 retold a "no clear trend" as a gradual decline).
- Run each eval several times per iteration. With one run each, a single Haiku run decides a column.

## How to re-run the evals

1. For each eval in `evals/evals.json`, create `<runs>/<id>-<name>/<config>/workspace/`, with config
   `with_skill` or `baseline`.
2. Start a Haiku sub-agent per run. For with_skill: "A skill is installed at <skill dir>; read SKILL.md first
   and follow it. Start every Bash command with `cd <workspace>`. The user's message: <prompt>".
   For baseline: the same, without the skill sentence. For eval 4, send its prompt as a follow-up turn to the
   eval-1 agent.
3. Write `meta.json`: `{"eval_id", "config", "workspace", "transcript": <agent JSONL>, "turn_start": [epoch, ...]}`,
   then `uv run python evals/extract_replies.py <runs>`. It writes `reply.md`: the agent's handed-back message
   if it is in the user's language, otherwise its last user-facing text of that turn.
4. `uv run python evals/grade.py <runs>`. It writes `grading.json` per run and prints the summary table.
   The CLI log it reads defaults to `~/.cache/wiki-interest-analyzer/invocations.jsonl`.
