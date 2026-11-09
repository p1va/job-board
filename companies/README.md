# Companies

One CSV row per job board: `provider,key,name,website,origins,bet_de,bet_checked`.

| File | Where it comes from |
|---|---|
| `lists.csv` | generated from `lists/*.csv` (supported boards on the curated lists), see lists/README.md |
| `personio.csv` | Personio boards from the supplied Common Crawl export, checked against their XML feeds; live empty boards are retained and unresolved checks are reported |
| `lever.csv` | Lever boards checked independently in both API regions, with case-sensitive tokens; curation and matching-job results are in reports/lever-backfill.json |
| `profiles.json` | cached names, websites and logos for 379 of Bet's companies, from the fork's company-info (8 Nov 2026); the board's company names and logos |
| `bet.csv` | Bet's radar at [b52e9b4](https://github.com/betklab/ashby-job-radar/tree/b52e9b4): `companies.txt` (`bet-companies`), `boards.json` (`bet-registry`, with her Germany flag and last check) and `discovery/workable-companies.json` (`bet-workable-search`, with names and websites) |

Regenerate from a checkout of her repo:

```sh
uv run python -m jobboard.registry import-bet companies.txt boards.json discovery/workable-companies.json --out companies/bet.csv
```

Workable keys are company IDs from Workable's search, not the account slugs of
`apply.workable.com/<slug>`; see docs/contract.md.

Lever keys are regional: `global:<slug>` or `eu:<slug>`, with `provider=lever`.
Slug case is significant. The same slug can have independent boards in both regions. Discovery validates
each region; company names and websites in these rows also supply board labels
when no cached profile or curated list entry has them.
