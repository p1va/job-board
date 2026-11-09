# Retrievers

Each run collects raw jobs, including descriptions, compensation when the API
offers it, and provider-specific fields. It applies no role/location filters.
Run the join later to normalise and select jobs; these commands don't publish
the board or write job history.

```sh
uv sync --locked
uv run python -m jobboard.collect --source ashby --out raw/ashby/0-of-1
uv run python -m jobboard.collect --source greenhouse --out raw/greenhouse/0-of-1
uv run python -m jobboard.collect --source workable --out raw/workable/0-of-1
uv run python -m jobboard.collect --source personio --out raw/personio/0-of-1
uv run python -m jobboard.collect --source lever --out raw/lever/0-of-1
```

Ashby/Greenhouse read the corresponding provider rows in `companies/*.csv`.
Ashby requests `includeCompensation=true`; Greenhouse always requests
`content=true`. Both public APIs return the whole board in one response.
Workable collects through its search API, independent of the reference company
UUID inventory imported from Bet. Hosted Workable account slugs are not search
company IDs.

## Personio

Personio reads the provider rows in `companies/*.csv` through each board's
public `/xml` feed. No credentials or pagination are needed. Board keys are
single DNS labels; `.jobs.personio.de` and `.jobs.personio.com` URLs resolve to
one canonical key. The saved raw payload is JSON containing the parsed XML
fields, including all offices, description sections, salary, schedule and
experience metadata. Identity is `(personio, board slug, position id)`.

When any position has no description, one optional `?language=en` fetch fills
missing sections by native ID. Existing descriptions, titles, offices and the
base feed's set of positions remain authoritative. There is no language guess
or German retry. A failed optional fetch keeps the complete base board; missing
descriptions and enrichment errors appear in the report and Actions summary.
An HTTP 404 or malformed feed is unresolved; only a valid, empty XML feed is
an empty successful board. A position without a title is set aside; usable
postings flow through as partial, so absence cannot close a previously tracked
job. Discovery retains these live boards and reports the invalid positions.
Discovery validates the base feed without the
optional description request.

Locations are free text: the feed has no country field. The adapter preserves
the main office and additional offices for the shared places rules; it does
not assume Germany from the provider, employer or domain. Salary units stay
year/month/hour as reported, and `createdAt` supplies the posting timestamp.
The company name falls back to the most common `subcompany` across that board's
open jobs when no cached/list profile exists, with a deterministic tie-break.
One board uses the same label for headings, filters and roles.

## Lever

Lever reads `provider=lever` rows from `companies/*.csv`. Keys explicitly include
the API region: `global:<slug>` or `eu:<slug>`. Slug case is preserved: `Legend`
and `legend` are different API tokens. The two regions can host different
live boards with the same slug, so they have separate scope and job identities.
Daily collection calls only the stored endpoint, never switches region on an
error, and requests the full published-postings array with `mode=json`.
No API key or detail requests are needed. The native posting payload is preserved.

For example, `lever,eu:prima` calls
`https://api.eu.lever.co/v0/postings/prima?mode=json`. A valid `[]` is an empty
board; a 404, malformed array, duplicate ID or missing title leaves the board
unresolved. The shared HTTP client validates gzip/truncation and retries within
the same pacing/deadline budget. There is no inferred absence from a failed feed.

The normalizer joins opening/body, list sections and closing content into readable
text, without repeating the combined description's opening. It preserves salary
units, all locations, and the provider's workplace declaration. `country` qualifies
the primary location only; neither the EU API region nor a remote flag proves
European eligibility. `createdAt` is a creation timestamp in milliseconds, not
verified evidence of when a role was first published. Registry names/websites
are fallback labels when profiles and curated lists have no better information.

Discovery checks both regions for a bare candidate slug, even if the global board
is live. Explicit regional keys check only that region. Hosted board URLs can also
establish region when importing curated lists; ambiguous list entries stay in the
coverage report until their region is known.

## Workable scope and cost

This endpoint is undocumented. The query parameters were checked against its
public website and live responses on 8 November 2026. Each European country is
a separate `location=<country name>` selection; an additional
`workplace=remote` query finds remote postings worldwide. See `EUROPE` in
`retrievers.py` for the explicit country/dependency list, including
transcontinental countries. A query name is a discovery selection, not proof
that every hit is eligible to work from that country. In particular, Georgia
can be ambiguous; the join must inspect the original job locations.

Remote results include US-only roles. Neither the remote flag nor a company's
country proves worldwide work eligibility. Country restrictions, Europe/global
scope and ambiguous cases are the join's concern. Querying remote jobs only
within European countries is cheaper but is not proven equivalent: it could
miss worldwide ads whose primary address is elsewhere.

The initial probe reported about 10,300 UK and 49,800 remote jobs at 20 jobs per
page (`pageSize=100` was ignored). A full remote walk alone needs about 2,500
requests. The default cap is 5,000 pages per query; reaching it produces a
**partial** outcome. Full walks may take an hour or more. Inspect `report.json`
and measure the scope budget before enabling a schedule.

A bounded local pilot:

```sh
uv run python -m jobboard.collect --source workable --countries DE,GB,IT \
  --max-pages 150 --out raw/workable/pilot
```

`--remote exclude` omits the additional worldwide query; `--remote only` selects
just that query. Stopping early preserves fetched jobs but establishes no
absence. The collector checks cursor loops, invalid/empty intermediate pages,
the advertised total and page limits. Identical jobs returned by overlapping
queries retain their scope membership and native identity.

## Shards and failures

`--shard-index 0 --shard-count 2` takes alternating scopes from the sorted
selection for board APIs. Shard 1 takes the others. For Workable, the last shard
handles only the worldwide remote query; countries go to the other shards, so
the remote walk doesn't stack on top of country walks. `--limit-scopes 3` limits a smoke run; zero
means all. `input_sha` hashes the exact selection for this shard, so different
shards normally have different input hashes.

Requests share a pacing/retry budget within a shard: by default at most two
requests per second for Ashby/Greenhouse, one per second for Personio/Lever and one
every two seconds for Workable, with two workers. Retries and the optional
Personio language request share the same pacing and deadline. Rate limits and
server/network/truncation errors get bounded retries.
Long Retry-After values defer the shard rather than hammering subsequent
boards. These settings do not imply provider-published rate limits.

The output directory contains:

- `jobs.parquet`: provider fields (JSON or parsed XML), native key, scope, run ID and fetch time.
- `manifest.json`: every attempted scope's completeness and errors, validated
  against the raw rows; written last as the completion marker.
- `report.json`: timings (including per scope), counts, Parquet size, request
  totals, downloaded bytes and HTTP errors.

A per-scope error still yields a validated artifact with unresolved scopes and
any usable earlier positives. A valid artifact exits successfully; failed and
partial counts by HTTP status appear in the Actions summary with a warning.
An invalid artifact fails and has no completion marker. Neither failure is an
empty successful board. A rerun replaces only raw staging output.
Committed history/replay rules belong to the join.

`--deadline 6000` (the default, seconds) stops starting requests/pages after 100
minutes and flushes a valid artifact before the workflow's 120-minute timeout.
In-progress walks become partial; unstarted scopes become failed with a
deadline error. First/last/maximum advertised totals are saved per scope; a
shrinking Workable total remains conservatively partial when fewer jobs than
the maximum were returned. Any tolerance needs observed churn data first.

## Actions

**Collect job boards** runs daily at 05:17 UTC and supports manual dispatch.
Its ten matrix jobs collect two shards per provider. `fail-fast: false` lets
other shards finish when one fails; `always()` uploads usable outputs.
Artifacts are overwritten on reruns and retained for three days. Collection
has read-only repository permissions. The serialized publisher joins valid
shards with committed history, optionally refreshes company profiles, renders
the board, commits its data and deploys that generation to GitHub Pages.
Scheduled runs publish; manual runs publish only with `publish=true` on main.

Dispatch inputs can limit Workable to country codes such as `DE,GB,IT`, include
or exclude the additional worldwide remote query, and cap pages/scopes for a
smoke run. Defaults keep all European country selections plus worldwide remote.

API references: [Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api),
[Greenhouse](https://docs.greenhouse.io/job-board). Workable has no published
contract for the search endpoint; keep the recorded fixtures as regression
examples, not a guarantee of future compatibility.
Personio documents its [public XML integration](https://developer.personio.de/docs/integration-of-open-positions)
and [language-specific description fields](https://support.personio.de/hc/en-us/articles/29375445597725-Frequently-asked-questions-on-XML-job-integration).
Lever documents its [public Postings API](https://github.com/lever/postings-api).
