# Companies

One CSV row per job board: `provider,key,name,website,origins,bet_de,bet_checked`.

| File | Where it comes from |
|---|---|
| `bet.csv` | Bet's radar at [b52e9b4](https://github.com/betklab/ashby-job-radar/tree/b52e9b4): `companies.txt` (`bet-companies`), `boards.json` (`bet-registry`, with her Germany flag and last check) and `discovery/workable-companies.json` (`bet-workable-search`, with names and websites) |

Regenerate from a checkout of her repo:

```sh
uv run python -m jobboard.registry import-bet companies.txt boards.json discovery/workable-companies.json --out companies/bet.csv
```

Workable keys are company IDs from Workable's search, not the account slugs of
`apply.workable.com/<slug>`; see docs/contract.md.
