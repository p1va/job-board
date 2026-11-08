"""What every retriever hands to the join: raw job rows plus a manifest of the scopes it scanned.

See docs/contract.md. A retriever writes, per shard:
    raw/<source>/<shard>/jobs.parquet   rows in RAW_SCHEMA
    raw/<source>/<shard>/manifest.json  a Manifest
Retrievers don't normalise: each row keeps the provider's own JSON for one job.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum

import pyarrow as pa

SCHEMA_VERSION = 1


class Status(StrEnum):
    COMPLETE = "complete"   # every page fetched, at least one job
    EMPTY = "empty"         # fetched fine, the scope has no jobs (HTTP 200, zero jobs)
    FAILED = "failed"       # nothing usable: error, timeout, 404, 429 after retries
    PARTIAL = "partial"     # some pages fetched, pagination didn't finish


class Kind(StrEnum):
    BOARD = "board"   # a company's whole board: absence from a complete scan is authoritative
    QUERY = "query"   # a search selection (e.g. Workable by country): absence proves nothing


RESOLVED = {Status.COMPLETE, Status.EMPTY}

RAW_SCHEMA = pa.schema([
    pa.field("schema_version", pa.int16(), nullable=False),
    pa.field("run_id", pa.string(), nullable=False),
    pa.field("source", pa.string(), nullable=False),        # ashby | greenhouse | workable
    pa.field("scope_id", pa.string(), nullable=False),      # the scope that returned this row
    pa.field("board", pa.string(), nullable=False),         # the job's own board (for search hits, from the payload)
    pa.field("native_id", pa.string(), nullable=False),     # the provider's job id
    pa.field("fetched_at", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("payload", pa.string(), nullable=False),       # the provider's JSON for this job, unchanged
])


@dataclass
class Scope:
    scope_id: str
    kind: Kind
    status: Status
    http_status: int | None = None
    pages: int = 0
    jobs: int = 0
    error: str | None = None

    def problems(self) -> list[str]:
        p = []
        if self.status == Status.COMPLETE and self.jobs == 0:
            p.append("complete with zero jobs: use empty")
        if self.status == Status.EMPTY and self.jobs != 0:
            p.append("empty with jobs")
        if self.status in (Status.FAILED, Status.PARTIAL) and not self.error:
            p.append(f"{self.status} without an error")
        if self.status == Status.FAILED and self.jobs:
            p.append("failed with jobs: use partial")
        return [f"{self.scope_id}: {x}" for x in p]


@dataclass
class Manifest:
    source: str
    shard: str
    run_id: str
    input_sha: str          # hash of the scope list the shard was asked to scan
    started_at: datetime
    finished_at: datetime
    scopes: list[Scope] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def problems(self, raw: pa.Table | None = None) -> list[str]:
        """Contract violations; with the shard's raw rows, also checks the per-scope job counts."""
        p = [x for s in self.scopes for x in s.problems()]
        if self.schema_version != SCHEMA_VERSION:
            p.append(f"schema_version {self.schema_version} != {SCHEMA_VERSION}")
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None:
            p.append("timestamps must be UTC-aware")
        dupes = [k for k, n in Counter(s.scope_id for s in self.scopes).items() if n > 1]
        p += [f"duplicate scope {k}" for k in dupes]
        if raw is not None:
            if not raw.schema.equals(RAW_SCHEMA):
                p.append("raw rows don't match RAW_SCHEMA")
            else:
                rows = Counter(raw.column("scope_id").to_pylist())
                for s in self.scopes:
                    if rows.get(s.scope_id, 0) != s.jobs:
                        p.append(f"{s.scope_id}: manifest says {s.jobs} jobs, raw has {rows.get(s.scope_id, 0)}")
                p += [f"raw rows for unlisted scope {k}" for k in set(rows) - {s.scope_id for s in self.scopes}]
                runs = set(raw.column("run_id").to_pylist())
                if runs - {self.run_id}:
                    p.append(f"raw rows from other runs: {sorted(runs - {self.run_id})}")
        return p

    def to_json(self) -> str:
        d = asdict(self)
        d["started_at"], d["finished_at"] = self.started_at.isoformat(), self.finished_at.isoformat()
        return json.dumps(d, indent=1, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Manifest:
        d = json.loads(text)
        d["started_at"] = datetime.fromisoformat(d["started_at"])
        d["finished_at"] = datetime.fromisoformat(d["finished_at"])
        d["scopes"] = [Scope(**{**s, "kind": Kind(s["kind"]), "status": Status(s["status"])}) for s in d["scopes"]]
        return cls(**d)
