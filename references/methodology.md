# Methodology: how the verdict and its confidence are computed

Read this when the user asks "why this confidence?", "how is this calculated?" or "can I trust it?".
All thresholds are constants at the top of `scripts/analyze.py`; this file mirrors them.

Contents: 1 Pipeline · 2 Metrics · 3 Trend · 4 Confidence rules · 5 Spikes & seasonality ·
6 Cross-language ranking · 7 Explaining it to a user

## 1. Pipeline

1. **Topic → articles.** The topic is resolved to a Wikidata item (QID). Its sitelinks give the exact
   article title in every language, which is why pl/cs titles that differ still match. A *cluster* is several
   QIDs (main topic + related); each language gets the articles that exist there.
2. **Redirects.** Views of a redirect title ("Englisch" → "Englische Sprache") are recorded under the
   redirect, so they are added to the article. To save requests, redirects are ranked by their last-60-day
   views. *Major* redirects (≥1% of the article's views and ≥10 views) are always summed. *Minor* ones are
   summed only when the article looks newly created, because after a rename the history sits on the old title.
3. **Monthly views** for `agent=user`, `access=all-access`, complete months only.
4. **Normalization.** share = topic views ÷ all views of that language edition × 10⁶ ("views per million").
   This removes edition-wide changes: overall traffic decline, bot reclassification, the Ukrainian
   readership shift. Raw views are kept as secondary numbers.

## 2. Metrics (per language)

| Field | Meaning |
|---|---|
| `level_per_million` | mean share over the last 12 months. The only cross-language comparable level. |
| `slope_pct_per_year` | Theil–Sen slope of log(share), outliers removed, as compound %/year |
| `slope_ci_pct` | 95% interval: moving-block bootstrap (block 3 months, 500 reps, fixed seed) of residuals |
| `mk_p` | Mann–Kendall p-value (rank-based, so robust to outliers and skew) |
| `yoy_pct` / `yoy_pct_raw` | mean share of the last 12 months vs the 12 before (same calendar months, so seasonality cancels), without / with one-off spikes |
| `abs_views_pct_per_year` | same slope on raw views. Compare it with the share to separate topic growth from edition growth. |
| `edition_pct_per_year` | slope of the whole edition's views (the baseline) |
| `articles_agree` | how many cluster articles (median ≥ 50 views/month) move in the cluster's direction |

Why these methods: Theil–Sen (the median of pairwise slopes) ignores a few extreme months.
The log scale makes a change from 100 to 200 the same as from 1000 to 2000, and keeps rates above −100%.
The block bootstrap keeps month-to-month correlation, which an ordinary bootstrap would erase,
producing falsely narrow intervals.

## 3. Trend

- `growing` if slope ≥ +5%/yr **and** the 95% CI excludes 0.
- `declining` if slope ≤ −5%/yr **and** the CI excludes 0.
- `flat` otherwise, meaning **no clear trend**: either a small change or one indistinguishable from noise.
- `insufficient_data` if there are fewer than 12 usable months.

## 4. Confidence rules

Start at `high`. Each rule can cap it; the final value is the lowest cap. `confidence_reason` names the
most informative cap.

| Condition | Cap | Caveat code |
|---|---|---|
| < 12 months | low (trend = insufficient_data) | `insufficient_data` |
| One-off spikes explain > 30% of the growth (mean-based growth with vs without them) | low | `spike_driven_growth` |
| Median < 300 views/month | low | `low_volume` |
| Median < 1000 views/month | medium | `small_volume` |
| No clear trend and the CI reaches beyond ±40%/yr | low | `wide_interval` |
| No clear trend and the CI reaches beyond ±15%/yr | medium | `wide_interval` |
| Direction claimed but Mann–Kendall p ≥ 0.05 | medium | `weak_significance` |
| Slope and YoY point opposite ways (and differ ≥ 10 pp) | low | `signals_disagree` |
| One of slope / YoY is flat and the other isn't (≥ 10 pp apart) | medium | `signals_disagree` |
| < 24 months (no YoY check; seasonality can mimic a trend) | medium | `no_yoy_short_window` |
| < 60% of ≥ 3 cluster articles agree with the direction | medium | `articles_disagree` |
| Ukrainian window that includes 2022 | medium | `uk_2022_shift` |

Caveats that inform without capping: `edition_wide_growth` (raw views grew, share didn't),
`edition_traffic_declining`, `spikes_present`, `seasonal_peaks`, `possible_bot_spike`, `partial_cluster`,
`article_created_in_window`, `no_article`, `edition_sizes_differ`, `curiosity_not_demand`.

## 5. Spikes and seasonality

- Hampel filter on the share series: a month is an outlier if it lies more than 3 robust SDs (1.4826×MAD)
  **and** more than 30% away from the 7-month rolling median. The 30% floor stops tiny wiggles in smooth series
  from counting.
- **Recurring** outliers repeat in the same calendar month in another year at a similar size (within 3×),
  e.g. September school-year peaks for astronomy. They are seasonality, not news, and do not count as spike-driven.
- **One-off** outliers (the Olympics, a death, a viral post) are named with their month. The trend is
  recomputed without them.
- `possible_bot_spike`: one page (article or redirect) carries ≥ 80% of a spike that is ≥ 3× normal.
  Automated traffic that slips into `agent=user` looks like this.

## 6. Cross-language ranking

Languages with data are ranked by level (share) and by trend (slope). The final rank is the average of the
two ranks. `explore_next` = the top two ranked languages whose confidence is not low and whose trend is not declining.
Languages with no article are listed as `content_gap`. Shares are relative to each edition's own readers, and
edition audiences differ (many Germans and Poles also read English Wikipedia), so a level compares salience,
not audience size.

## 7. Explaining it to a user (plain words)

- "Share" = out of every million pages people read in that Wikipedia, how many were about this topic.
- "High confidence" = the direction is statistically clear, the year-over-year check agrees, no single event
  drives it, and there are enough views to trust it.
- "Low confidence because of a spike" = one event (name the month) inflated the numbers; without it, the change
  is the `yoy_pct` value.
- "No clear trend" is a finding, not a failure: it means you shouldn't plan around growth there.
- Always add: Wikipedia views show curiosity. Validate demand separately (search volume, surveys, landing-page tests).
