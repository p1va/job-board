# job-board

A daily radar of software, research, product and data jobs in Europe and remote, and a board to
browse them.

- **Roles:** software engineering, research, product management, engineering management, data & ML
  engineering, data science.
- **Places:** all of Europe, on-site or hybrid, plus remote (worldwide, Europe, or one country). The
  board opens on Germany, the UK, Italy and remote.
- **Sources:** Ashby, Greenhouse, Workable, Personio and Lever, seeded from
  [Bet's radar](https://github.com/betklab/ashby-job-radar), curated lists and validated Common Crawl discoveries.

Browse the [job board](https://p1va.github.io/job-board/). GitHub Actions collects and publishes daily
at 05:17 UTC; manual runs can limit the collection or publish on demand. No email is sent.

## Layout

| Path | What |
|---|---|
| [`docs/contract.md`](docs/contract.md) | how retrievers hand jobs to the join, and how jobs' history is kept |
| [`docs/retrievers.md`](docs/retrievers.md) | collect full raw jobs, inspect failures and control Workable's scope |
| `src/jobboard/` | contract, table schemas, lifecycle rules, company inputs |
| `companies/` | company boards to scan, with where each came from |
| `lists/` | curated company lists (AI labs, global remote, …) |
| `tests/` | contract and lifecycle cases; `tests/examples/` is a sample retriever output |

## Run

```sh
uv sync
uv run pytest
```

Try a small collection (read-only public APIs):

```sh
uv run python -m jobboard.collect --source ashby --limit-scopes 3 --out raw/ashby/smoke
```

## Credits

Town and city names in `src/jobboard/cities.tsv` come from [GeoNames](https://www.geonames.org/)
(cities5000), licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
