# Discovering more boards

`athena-ats-boards.sql` finds company job boards on hosted ATS sites (Ashby, Greenhouse, Lever, Workable,
SmartRecruiters, Personio, Recruitee, Teamtailor) in Common Crawl's URL index with Amazon Athena. Steps,
cost and budget are at the top of the file; it is run by hand in the Athena console.

Import a result CSV: new Ashby and Greenhouse boards are checked against their APIs and the live ones go
into `companies/athena.csv`; boards on other providers are counted in the report to pick the next adapter.

```sh
uv run python -m jobboard.discover results.csv --out companies/athena.csv --report reports/athena.json
```
