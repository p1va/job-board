# Data contract

How retrievers hand jobs to the join, and how the join keeps jobs' history. Code:
[`contract.py`](../src/jobboard/contract.py), [`tables.py`](../src/jobboard/tables.py),
[`lifecycle.py`](../src/jobboard/lifecycle.py). Example output:
[`tests/examples/ashby-0-of-1/`](../tests/examples/ashby-0-of-1/).

## Pipeline

```
retrieve  one job per source (Ashby sharded) → raw/<source>/<shard>/{jobs.parquet, manifest.json}
join      read every manifest + previous data/state → lifecycle step → versions + new state
classify  role family, seniority, location (countries, workplace, remote regions), stack, lists
publish   one serialized job: validate, render the site, commit data/, deploy that same generation
```

## Identity

- A job is **(source, board, native_id)**: never merged when collected.
- A **version** is a job's normalised payload at one time. `payload_hash` covers every field that
  affects display or selection (title, locations, workplace, remote fields, salary, department,
  employment, url, description hash). A description hash identifies a version, not a job.
- Copies of one ad in several cities are grouped for display only after filtering, and each copy
  keeps its link, location, salary and lifecycle.
- A company can have several boards. A dead board is deactivated; the company, where it came from and
  its list memberships stay.

## Retriever output

Each shard writes `jobs.parquet` (`RAW_SCHEMA`: run_id, source, scope_id, board, native_id,
fetched_at, and the provider's JSON for the job, unchanged) and `manifest.json`:

```json
{"schema_version": 1, "source": "ashby", "shard": "0-of-4", "run_id": "19034567890-1",
 "input_sha": "…", "started_at": "2026-11-09T06:00:00+00:00", "finished_at": "…",
 "scopes": [{"scope_id": "ashby:acme", "kind": "board", "status": "complete",
             "http_status": 200, "pages": 1, "jobs": 2, "error": null}]}
```

| Scope status | Meaning |
|---|---|
| `complete` | every page fetched, at least one job |
| `empty` | fetched fine and the scope has no jobs (HTTP 200, zero jobs) |
| `failed` | nothing usable: error, timeout, 404, rate limit after retries (`error` says which) |
| `partial` | some pages fetched, pagination didn't finish (`error` says why) |

| Scope kind | Example | Absence from a complete scan |
|---|---|---|
| `board` | `ashby:acme`, `greenhouse:stripe` | authoritative: the job closed |
| `query` | `workable:search:country=GB` | only absent from that selection |

`Manifest.problems(raw)` checks the rules above, that resolved scopes were fully successful fetches
(HTTP 200, at least one page, no error), that raw rows carry the manifest's source, run and schema
version with every required value, that board scopes hold only their own board's jobs, and that each
scope's `jobs` equals its raw rows. **A shard with problems counts as missing:** all its scopes stay
unresolved, so it can't close anything.
`run_id` is `<GitHub run id>-<attempt>` (local runs: a UTC timestamp).

## State and history in `data/`

| File | What | Written |
|---|---|---|
| `data/versions/YYYY/MM/DD/<run_id>.parquet` | new or changed versions of tracked jobs (`VERSION`) | append-only, immutable once committed |
| `data/state.*` | one row per tracked job (`STATE`) | rewritten each run |
| `data/boards.*` | one row per board (`BOARD`) | rewritten each run |

- **Tracked** jobs are those kept by the role × Europe/remote filter at any point. A tracked job
  whose new version falls outside the filter is `filtered_out`, not closed.
- A version is written only when (job, payload_hash) differs from state. Replaying a run_id that is
  already committed is a no-op; before commit, a retry may replace its own staging file.

## Lifecycle per run

Scope membership (`scopes` in state) changes per scope outcome: seen → added; `complete` or
`empty` without the job → removed; `failed`, `partial` or missing → unchanged until a later scan
resolves it. Then, per job:

1. **Seen in any scope:** open (or `filtered_out` if no longer kept), confirmed now, payload updated.
2. **Removed by a board scan:** `closed`.
3. **Membership emptied by complete query scans:** `unlisted` (hidden); only an authoritative
   board or detail endpoint can close it.
4. **Otherwise unresolved:** status unchanged and not confirmed; an open job unconfirmed for over
   7 days becomes `unverified` (hidden).

Closed and unlisted jobs stay so until seen again; a reopened job keeps its first_seen. Every case
here is a test in `tests/test_lifecycle.py`, including Workable searches that overlap while one fails.

## Normalised fields

Typed, nullable, UTC. Unknown stays unknown: `workplace=unknown` is not onsite; worldwide, Europe,
EU, EMEA and single-country remote stay distinct (the UK is in Europe and EMEA, not the EU).
