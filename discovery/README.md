# Discovering more boards

`athena-ats-boards.sql` finds company job boards on hosted ATS sites (Ashby, Greenhouse, Lever, Workable,
SmartRecruiters, Personio, Recruitee, Teamtailor) in Common Crawl's URL index with Amazon Athena. Steps,
cost and budget are at the top of the file; it is run by hand in the Athena console.

Import a result CSV: new Ashby, Greenhouse, Personio and Lever boards are checked against their feeds and the live ones go
into `companies/athena.csv`; boards on other providers are counted in the report to pick the next adapter.

```sh
uv run python -m jobboard.discover results.csv --out companies/athena.csv --report reports/athena.json
```

To validate only Personio, at one request per second:

```sh
uv run python -m jobboard.discover results.csv --providers personio --interval 1 \
  --out companies/personio.csv --report reports/personio-backfill.json
```

Previous discoveries are preserved on reimport. Personio slugs are validated
before becoming hostnames; malformed XML/error pages cannot count as empty boards.

For Lever, supply `ats=lever` and either a bare `board_key` (checks both API
regions independently) or `global:<slug>` / `eu:<slug>` (checks only that region).
A bare slug checks both even if `api_region` is present as a discovery hint.
Imported keys always include the region and preserve the case-sensitive slug.
The SQL export also preserves Lever slug case. Names, websites and origins are kept
when supplied; origins default to `athena`.

```sh
uv run python -m jobboard.discover results.csv --providers lever --interval 1 \
  --out companies/lever.csv --report reports/lever-backfill.json
```

The report's `checks` records each attempted regional board separately, including
empty boards, HTTP failures and invalid responses. Duplicate candidates are not
retried within one import. A regional skip-list key excludes only that board;
a bare Lever skip token excludes both regions. Invalid skip entries are warned
and included in the report. Discovery
establishes that a board is live; matching job counts require the join's normal
role/location filters.
