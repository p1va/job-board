"""Writes the example retriever output in tests/examples/ashby-0-of-1/.

    uv run python tests/examples/make_examples.py

One shard of an Ashby run with three scopes: a board with two jobs (complete), a board with no
jobs (empty) and a board that failed. Payloads are trimmed Ashby posting-API job objects.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from jobboard.contract import RAW_SCHEMA, Kind, Manifest, Scope, Status

OUT = Path(__file__).parent / "ashby-0-of-1"
RUN = "19034567890-1"
T = datetime(2026, 11, 9, 6, 0, tzinfo=timezone.utc)

JOBS = [
    {"id": "0b1f6c2e-1111-4a5b-9c1d-000000000001", "title": "Senior Backend Engineer (Go)",
     "location": "Berlin", "secondaryLocations": [{"location": "Remote - Germany"}],
     "isRemote": True, "workplaceType": "Hybrid", "department": "Engineering",
     "employmentType": "FullTime", "publishedAt": "2026-11-02T09:00:00.000+00:00",
     "jobUrl": "https://jobs.ashbyhq.com/acme/0b1f6c2e-1111-4a5b-9c1d-000000000001",
     "compensation": {"compensationTierSummary": "€80K – €100K"},
     "descriptionPlain": "You will build our payments API in Go and PostgreSQL on Kubernetes."},
    {"id": "0b1f6c2e-2222-4a5b-9c1d-000000000002", "title": "Product Manager, Growth",
     "location": "London", "secondaryLocations": [], "isRemote": False, "workplaceType": "OnSite",
     "department": "Product", "employmentType": "FullTime", "publishedAt": "2026-11-05T09:00:00.000+00:00",
     "jobUrl": "https://jobs.ashbyhq.com/acme/0b1f6c2e-2222-4a5b-9c1d-000000000002",
     "compensation": None, "descriptionPlain": "Own activation and retention experiments."},
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = [{"schema_version": 1, "run_id": RUN, "source": "ashby", "scope_id": "ashby:acme", "board": "acme",
             "native_id": j["id"], "fetched_at": T, "payload": json.dumps(j, ensure_ascii=False, sort_keys=True)}
            for j in JOBS]
    pq.write_table(pa.Table.from_pylist(rows, schema=RAW_SCHEMA), OUT / "jobs.parquet", compression="zstd")
    scopes = [Scope("ashby:acme", Kind.BOARD, Status.COMPLETE, 200, 1, 2),
              Scope("ashby:quiet-co", Kind.BOARD, Status.EMPTY, 200, 1, 0),
              Scope("ashby:flaky-co", Kind.BOARD, Status.FAILED, 503, 0, 0, "HTTP 503 after 3 tries")]
    input_sha = hashlib.sha256("\n".join(s.scope_id for s in scopes).encode()).hexdigest()[:16]
    manifest = Manifest("ashby", "0-of-1", RUN, input_sha, T, T.replace(minute=2), scopes)
    (OUT / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")


if __name__ == "__main__":
    main()
