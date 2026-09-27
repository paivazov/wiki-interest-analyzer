# Data pitfalls and how the skill handles them

Each pitfall below is either handled in code or surfaced automatically as a caveat code. Mention the
caveat when the JSON shows it; use this file to explain *why* it matters.

| Pitfall | Why it matters | What the code does | Caveat code |
|---|---|---|---|
| Page views ≠ willingness to pay | Curiosity, homework and news all create views | Always printed once in answers and reports | `curiosity_not_demand` |
| Editions differ hugely in size | en.wikipedia gets many times more views than cs.wikipedia; many people read en.wikipedia instead of their own | Only shares are compared across languages; raw views are labelled "not comparable" | `edition_sizes_differ` |
| Ukrainian Wikipedia shifted from 2022 | Many readers moved from ru-wiki to uk-wiki after February 2022, so uk raw views jumped for almost every topic | Share normalization. Caveat always shown for `uk`; confidence capped at medium if the window includes 2022 | `uk_2022_shift` |
| Overall human traffic is falling | Search engines and AI assistants answer directly; bot detection keeps improving, so traffic moves from `user` to `automated` | Share normalization corrects it. The edition's own trend is reported, and flagged at ≤ −5%/yr | `edition_traffic_declining` |
| Growth of the edition, not the topic | Raw views can rise simply because the edition grows | Compares raw-view trend with share trend | `edition_wide_growth` |
| Renames and redirects split views | After a rename, history stays on the old title (now a redirect); aliases collect their own views | Sums major redirects; for articles that look newly created, also sums minor redirects to recover renamed history | `article_created_in_window` if it really is new |
| Bots counted as users | Some automated traffic evades detection and shows up as sharp one-page spikes | Spike attribution per page; flags one-page spikes ≥ 3× normal | `possible_bot_spike` |
| News and event spikes | One event can fake a year of growth | Hampel filter, trend and YoY recomputed without one-off spikes | `spike_driven_growth`, `spikes_present` |
| Seasonality (school year, holidays, sports seasons) | September vs July comparisons mislead | YoY uses matching months; recurring peaks are labelled seasonal, not news | `seasonal_peaks` |
| Short windows | 12 months cannot separate trend from season | No YoY below 24 months, confidence capped at medium | `no_yoy_short_window` |
| Small numbers | 200 views/month swing wildly | < 300/month → low confidence; < 1000 → at most medium | `low_volume`, `small_volume` |
| Mobile vs desktop mix | The mix shifted to mobile over the years | Always `access=all-access` | — |
| Data availability | Per-article data starts 2015-07; the current month is incomplete; last month appears a few days after month end | Window clamped; current month excluded; unpublished months are never cached as zero | `window_clamped`, `incomplete_month_excluded`, `latest_month_unpublished` |
| No article in a language | Absence isn't zero interest; it may be an opening | Reported as `no_article` with nearby `alternatives`; never plotted as 0 | `no_article` |
| Cluster covers languages unevenly | Missing sub-articles lower one language's level | Partial clusters flagged; `--expand` only adds articles present in every requested language | `partial_cluster` |
| Ambiguous names ("Mercury") | Planet, element and god have different audiences | `resolve` returns `ambiguous` with candidates; the agent must ask | — |
| Cluster choice changes the answer | Auto-picked related articles may not match the user's idea of the topic | Reports `articles_agree`; caps confidence when articles disagree; the user can edit the cluster | `articles_disagree` |
