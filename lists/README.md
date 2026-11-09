# Lists

Curated company lists, one CSV per list (AI labs, global remote, Berlin, Italy, UK hottest), researched
with sources on 8 November 2026. Columns:
`name,website,country,city,why,source_url,careers_url,ats,board_key,checked`.

Each list becomes a filter on the board (label from the file name: `ai-labs.csv` is "AI labs").
Companies on Ashby, Greenhouse, Personio or Lever with a `board_key` are scanned: they go into `companies/lists.csv`.
Everything else is listed in `reports/lists-coverage.csv` with the reason (no board found, a provider we
don't read yet, or a Workable account slug, which Workable's search doesn't use). Regenerate both:

```sh
uv run python -m jobboard.registry import-lists lists --out companies/lists.csv --coverage reports/lists-coverage.csv
```

For Lever use `global:<slug>` or `eu:<slug>`. An older bare slug is resolved only
when its hosted Lever `careers_url` establishes the matching region; otherwise
the coverage report asks for a regional key. Region is never inferred from the
company's country.
