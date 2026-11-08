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
```

Ashby/Greenhouse read the corresponding provider rows in `companies/*.csv`.
Ashby requests `includeCompensation=true`; Greenhouse always requests
`content=true`. Both public APIs return the whole board in one response.
Workable collects through its search API, independent of the reference company
UUID inventory imported from Bet. Hosted Workable account slugs are not search
company IDs.

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
requests per second for board APIs and one every two seconds for Workable, with two
workers. Rate limits and server/network/truncation errors get bounded retries.
Long Retry-After values defer the shard rather than hammering subsequent
boards. These settings do not imply provider-published rate limits.

The output directory contains:

- `jobs.parquet`: the unchanged job JSON, native key, scope, run ID and fetch time.
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

**Collect job boards** is manual-only. Its six independent matrix jobs collect
two shards for each provider. `fail-fast: false` lets the other shards finish
when one fails; `always()` uploads usable outputs. Artifacts are overwritten on
reruns and retained for three days. The workflow has read-only repository
permissions and makes no data commits, deployments or email requests. The join
will consume these artifacts in the next phase.

Dispatch inputs can limit Workable to country codes such as `DE,GB,IT`, include
or exclude the additional worldwide remote query, and cap pages/scopes for a
smoke run. Defaults keep all European country selections plus worldwide remote.

API references: [Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api),
[Greenhouse](https://docs.greenhouse.io/job-board). Workable has no published
contract for the search endpoint; keep the recorded fixtures as regression
examples, not a guarantee of future compatibility.
