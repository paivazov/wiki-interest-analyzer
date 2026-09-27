# Eval results: Claude Haiku 4.5 (2026-09-26, iterations 4–5 on 2026-09-27)

Every run used `claude-haiku-4-5` as a sub-agent with an empty workspace. **with_skill** = the agent is told
where the skill is and to read SKILL.md. **baseline** = same prompt and tools (Bash, internet, Python, web
search), no skill. Checks are in `evals.json` and graded by `grade.py` (log, files, PDF, reply regexes,
transcript). Baselines are graded only on checks that don't need the skill's own files.

## Scores

| Eval | Baseline | Iteration 1 | Iteration 2 | Iteration 3 | Iteration 4 | Iteration 5 (final SKILL.md) |
|---|---|---|---|---|---|---|
| 1 pl vs cs, intermittent fasting | 1/4 | 8/8 | 8/8 | — | 6/10 | 10/10 |
| 2 astronomy in uk, trust | 0/4 | 6/6 | 6/6 | — | 7/7 | 7/7 |
| 3 learning English, 4 langs, report | 2/5 | 6/6 | 6/6 | 6/6 | 6/9 | 6/9 (9/9, see below) |
| 4 follow-up: +sk, 12 months | 1/2 | 6/6 | 6/6 | — | 7/7 | 7/7 |
| 5 "Mercury" (ambiguous) | 0/1 | 3/3 | 3/3 | — | 3/3 | 3/3 |
| 6 padel, sk has no article | 2/3 | 4/4 | 4/4 | — | 7/7 | 7/7 |
| 7 curling, Olympic spike | 1/4 | 5/6 | 6/6 | 6/6 | 8/8 | 8/8 |
| **Total** | **8/23 (35%)** | **38/39 (97%)** | **39/39 (100%)** | **12/12** | **44/51 (86%)** | **48/51 (94%)** |

Iteration 1's miss (7.raw_vs_clean) is a check added *after* reading that reply; see below.
Iteration 3 re-ran only the two evals whose replies still had problems when read by hand.
Iterations 4 and 5 ran all 7 evals with 12 new checks (51 in total), so their totals are not comparable with
iterations 1–3. Baselines were not re-run and are scored on the original checks.

Efficiency (iteration 2, with skill): 3–7 tool calls per task, all of them `wia` commands, and 20–60 s per
task. Baselines: 8–28 tool calls, 45–345 s, writing their own `requests`/`pandas` code in 3 of 6 tasks.
Iteration 5: 3–7 tool calls and 28–80 s per task.

## What the baseline got wrong (without the skill)

- **Eval 1:** analysed general "fasting" articles (pl "Post", cs "Půst") instead of intermittent fasting. It
  missed that pl has no such article, compared absolute views across languages, included the incomplete
  current month, and gave no confidence.
- **Eval 2:** measured *edits*, not views, leaned on "global trends", and rated trust 4/5, while the data show a
  −43 %/yr decline in share.
- **Eval 3:** no page-view analysis at all. Recommendations came from article counts and web market reports,
  with an invented "140% growth". No PDF.
- **Eval 4:** found a stray Slovak page with 9 views a year and concluded "critically low interest" (the topic
  has no sk article). The window again included the incomplete month.
- **Eval 5:** didn't ask which Mercury; picked "Mercurio" and reported a decline.
- **Eval 6:** claimed uk has no padel article (it has "Падель (спорт)") and ranked languages by *article length*.
- **Eval 7:** "YES, interest is growing a lot", driven by the Feb 2026 Winter Olympics spike. It put the Games in
  Turin (they were in Milan–Cortina) and recommended launching now.

## Found by reading the with-skill replies (not only by regex), and what changed

| Iteration | Problem in Haiku's reply | Change | Result |
|---|---|---|---|
| 1 | Eval 3: invented reasons ("Turkish has the highest interest", although vi is 291/M against tr 81/M) | code emits `ranking[].why`; SKILL.md rule "reasons come from the JSON only" | iteration 2 dropped that claim but invented a ratio ("8× more views") |
| 2 | Eval 3: invented ratio | rule tightened: "don't compute new numbers (ratios, 'N times more')" | iteration 3: no invented numbers or claims (terser reply) |
| 1 | Eval 7: "+40% YoY is caused by the spike" (+40% is YoY *without* it) | answer sentence says "+40% without one-off spikes (+438% with them)"; SKILL.md: quote both | iterations 2 and 3 quote both correctly |
| 2 | Eval 7: called the −10 %/yr slope a "definite decline" while the verdict is "no clear trend" | rule: never turn "no clear trend" into growth or decline | iteration 3: "no clear direction (flat)" |
| 2 | Eval 4: "very low" confidence (the JSON says "low") | rule 3 names the forbidden variants; `confidence_label_exact` check | iterations 4 and 5: exact labels in every run that analysed data |
| 3 | Eval 3: correct but terse, skipped the caveats | code-chosen `must_relay`, copied verbatim; `caveats_relayed` check | iterations 4 and 5: relayed verbatim (eval 3 in iteration 5: in its text, not in its handback) |
| 2, 4 | Eval 3: invented ratios ("8 разів більше", "вдвічі більше") | `no_computed_ratios` check; rule for an empty `explore_next` | iteration 5: none |
| 4 | Eval 1: stopped to ask about a proxy article instead of analysing | `resolve` note and rule 5: analyse anyway, offer alternatives at the end | iteration 5: analysed, gap reported, proxy offered as a next step |
| 4 | Eval 3: hand-made `report.md`, no PDF | rule 1: no hand-made report files; "звіт" → `report` in the decision table | iteration 5: PDF and chart made with `wia` |
| 5 | Eval 7: "no clear trend" retold as a gradual decline | — | open issue |

## Iterations 4–5: limitations and labels moved into code

**Why.** Iteration 3's eval-3 reply skipped the caveats, but still scored 6/6, because only eval 2 checked
that a caveat reached the reply. Iteration 2's eval-4 reply said "дуже низькою" ("very low") where the JSON
said "low". Rules 3 and 7 had changed in iteration 3, yet only evals 3 and 7 were re-run.

**Change.**
- `analyze` and `report` now print `must_relay`, chosen by the code: the most severe caveats (at most 3,
  one of each kind first) plus the curiosity≠willingness-to-pay line, all in the report language. SKILL.md
  rule 4 and the reply shape tell the agent to copy these lines word for word. Rule 3 now names the forbidden
  variants of the confidence label ("дуже низька", "very low", "moderate").
- Both commands also write the verdict the agent saw (confidence per language, caveat codes, `must_relay`
  codes) to the invocation log, so each turn is graded against its own verdict.

**New checks:**

| Check | Evals | Passes when |
|---|---|---|
| `N.caveats_relayed` | 1, 3, 6, 7 | the reply contains the curiosity≠demand line and at least one caveat of that turn's analysis (per-code keywords, uk + en) |
| `N.confidence_label_exact` | 1, 2, 3, 4, 6, 7 | every confidence level in the JSON appears next to a confidence word, with no intensifier or synonym |
| `N.no_computed_ratios` | 3, 6 | no ratio the tool didn't compute ("вдвічі", "8 разів більше", "N× more"; rule 7) |

**The checks catch the old failures.** Re-grading the saved iteration-2 and iteration-3 replies with the
new checks fails exactly the problems that had been found by reading:
- Iteration 2, eval 3: "8 разів більше" (`no_computed_ratios`), and no caveat of that run (`caveats_relayed`).
- Iteration 2, eval 4: "дуже низькою" (`confidence_label_exact`).
- Iteration 3, eval 3: no curiosity line and no caveats; the "high" labels were dropped.

Iteration-1/2 eval-1 runs can't be graded on the new checks, because their logs predate the verdict fields.

**Iteration 4** (the must_relay change, all 7 evals): every run that analysed data relayed `must_relay`
verbatim and used the exact labels. Two new failures were unrelated to relaying:
- Eval 1 stopped after `resolve` to ask whether to substitute a Polish proxy article, so it never ran `analyze`.
- Eval 3 wrote its own `report.md` with a heredoc instead of running `wia report` (no PDF). It also added an
  unlabelled "priorities" section with an invented ratio ("вдвічі більше": the data say 255 vs 65 per million,
  about 3.9×) and a market claim the tool never made.

Fixes:
- The `resolve` note and rule 5 now say "run analyze anyway; offer `alternatives` at the end, don't stop to ask".
- Rule 1 forbids hand-made report files, and the decision table maps "звіт" to `report`.
- The reply section says what to write when `explore_next` is empty.
- `no_computed_ratios` was added.

**Iteration 5** (final SKILL.md, all 7 evals): 48/51.
- Evals 1, 2, 4, 6 and 7 relayed `must_relay` word for word under a limitations heading. Evals 1 and 4 did so
  after running `analyze` despite the Polish gap.
- Eval 3 made the PDF and a chart. All three misses are in eval 3's delivered message: its `SubagentHandback`
  was a 5-line summary without caveats, labels or language names. Its chat text, written before and after the
  handback, was a full reply with the `must_relay` lines verbatim, and graded on that text eval 3 scores 9/9.
  The grader counts the handback, a rule fixed in iteration 4 before this run, so the official score stays 48/51.

## Open issues (not fixed yet)

- Haiku can still turn a "no clear trend" into a direction in its own words. Iteration 5, eval 7 wrote
  "довгостроковий тренд фактично спрямований вниз … інтерес поступово зменшується" next to the correct
  "не показує виразного тренду"; iteration 2 did the same. No regex catches this. It needs the LLM-judge
  check proposed in DEVLOG ("reply consistent with the verdict JSON").
- In the eval harness a sub-agent can hand back a summary that differs from the reply it wrote (iteration 5,
  eval 3). In a real chat the user sees the full text, so this is partly an artifact of the harness. The grader
  scores the handed-back message.
- Each eval ran once per iteration, and Haiku varies between runs: eval 1 passed in iterations 1, 2 and 5 but
  stopped early in 4. Several runs per eval would separate instruction problems from noise.
- The data behind these evals is live. Assertions pinned to numbers (e.g. +438%) will drift once the window
  moves past Feb 2026. ROADMAP.md §7 proposes freezing the cache DB and `WIA_TODAY` per eval.
