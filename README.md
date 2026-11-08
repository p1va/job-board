# job-board

A daily radar of software, research, product and data jobs in Europe and remote, and a board to
browse them.

- **Roles:** software engineering, research, product management, engineering management, data & ML
  engineering, data science.
- **Places:** all of Europe, on-site or hybrid, plus remote (worldwide, Europe, or one country). The
  board opens on Germany, the UK, Italy and remote.
- **Sources:** company job boards on Ashby, Greenhouse and Workable to start, seeded from
  [Bet's radar](https://github.com/betklab/ashby-job-radar).

Status: phase 1, the data contract and company inputs. Retrievers, the join and the board follow.

## Layout

| Path | What |
|---|---|
| [`docs/contract.md`](docs/contract.md) | how retrievers hand jobs to the join, and how jobs' history is kept |
| `src/jobboard/` | contract, table schemas, lifecycle rules, company inputs |
| `companies/` | company boards to scan, with where each came from |
| `lists/` | curated company lists (AI labs, global remote, …) |
| `tests/` | contract and lifecycle cases; `tests/examples/` is a sample retriever output |

## Run

```sh
uv sync
uv run pytest
```
