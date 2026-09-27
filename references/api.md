# APIs, limits, caching, errors

## Endpoints used

| Purpose | Endpoint |
|---|---|
| Article monthly views | `https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{lang}.wikipedia/all-access/user/{Title}/monthly/{YYYYMM0100}/{YYYYMMDD00}` |
| Whole-edition monthly views | `.../pageviews/aggregate/{lang}.wikipedia/all-access/user/monthly/{start}/{end}` |
| Topic search | `https://www.wikidata.org/w/api.php?action=wbsearchentities` |
| Titles per language (sitelinks) | `wikidata.org/w/api.php?action=wbgetentities&props=sitelinks\|labels\|descriptions` |
| Related items for `--expand` | Wikidata `haswbstatement:P279=Q…` / `P361=Q…`, `wbgetclaims P527`, `morelike:` search on a wiki |
| Redirects of articles (batched) | `{lang}.wikipedia.org/w/api.php?action=query&prop=redirects&titles=A\|B` |
| Recent views to rank redirects | `{lang}.wikipedia.org/w/api.php?action=query&prop=pageviews&pvipdays=60` (50 titles per call) |

A 404 from per-article means "no views recorded in that range" and is stored as zeros for published months.
Docs: https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/reference/page-views.html

## Data availability

- Per-article and aggregate `user` data: from **2015-07**. Older requests are clamped (`window_clamped`).
- Monthly data for a month appears a few days after it ends. The aggregate endpoint tells us which months
  are published. Unpublished months are neither analysed nor cached.
- The current month is always excluded (`incomplete_month_excluded`).

## Rate limits and etiquette

- Wikimedia rate limits (https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits): **200 requests/min** for
  unauthenticated clients with a policy-compliant User-Agent, i.e. one with contact info, where "an email or
  full URL" both count; **10/min** for unidentified clients. Wikimedia also asks for at most 3 concurrent requests.
- The User-Agent is `wiki-interest-analyzer/<version> (https://github.com/paivazov/wiki-interest-analyzer) python-requests`.
  The project URL is the contact, so every run qualifies for the 200/min tier.
- The gateway keys that counter by the contact it finds in the User-Agent (wikitech: REST_Gateway/Rate_limiting,
  `x-ua-contact`), and it prefers an e-mail to a URL. So by default **all users of this skill share one
  200/min budget**. Setting `WIA_CONTACT=you@example.org` adds your contact
  (`(…/wiki-interest-analyzer; you@example.org)`) and gives you a counter of your own. Set it in your shell
  profile, or in Claude Code's `settings.json` under `"env"`.
- Pacing is shared across processes through `pace.sqlite` in the cache dir (150 req/min; override with
  `WIA_REQ_PER_MIN`). At most 3 concurrent requests. A cold run of 4 languages with clusters makes a few dozen
  requests, which takes well under a minute at this pace.
- On 429/5xx: honour `Retry-After` (otherwise back off 5, 8, 16… s); every process waits; up to 5 attempts.
  Retries are printed on stderr as `retrying after HTTP 429`, and `network.rate_limited` counts the 429s. On the
  first 429 without `WIA_CONTACT`, stderr also suggests setting it.

## Cache

- SQLite at `$WIA_CACHE_DIR` (default `~/.cache/wiki-interest-analyzer/`, or `$XDG_CACHE_HOME/...`).
- `pageviews` table: key (project, article, agent, access, granularity, month). Published months never
  change, so they are cached forever and never refetched. Shortening the window or re-running needs no network.
- `http_json` table: Wikidata / wiki API responses (30 days; searches and recent views 7 days).
- `invocations.jsonl`: one line per CLI call (argv, status, request counts, duration). Useful for auditing
  and evals. `wia cache info` shows sizes; `wia cache clear` empties the cache.

## Errors

Every error is JSON with `status: "error"`, `error` and a `hint`. Exit code 2 = bad input (fix the command),
3 = network (retry later or check connectivity). Common ones:
- `Spec not found` → run `resolve` first, or use the `spec` path it printed.
- `Bad language code` → use Wikipedia codes (`pl`, `cs`, `uk`, `de`, `tr`, `vi`, `zh`, `simple` …).
- `No article 'X' in pl.wikipedia` (from `articles.pl=`) → the exact title is case-sensitive after the first letter.
