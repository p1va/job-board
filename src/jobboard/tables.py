"""Schemas of the tables the join writes to data/ (see docs/contract.md).

Typed and nullable: unknown stays null or "unknown", never a guess. Timestamps are UTC.
"""
from __future__ import annotations

import pyarrow as pa

UTC = pa.timestamp("us", tz="UTC")
KEY = [pa.field("source", pa.string(), nullable=False),
       pa.field("board", pa.string(), nullable=False),
       pa.field("native_id", pa.string(), nullable=False)]

WORKPLACE = ("onsite", "hybrid", "remote", "unknown")
JOB_STATUS = ("open", "closed", "unlisted", "unverified", "filtered_out")

# One row per new or changed version of a tracked job: data/versions/YYYY/MM/DD/<run_id>.parquet
VERSION = pa.schema(KEY + [
    pa.field("run_id", pa.string(), nullable=False),
    pa.field("seen_at", UTC, nullable=False),
    pa.field("payload_hash", pa.string(), nullable=False),   # over every display/selection field below
    pa.field("company_id", pa.string(), nullable=False),
    pa.field("company_name", pa.string()),            # when the provider sends it
    pa.field("listed", pa.bool_(), nullable=False),     # false: direct-link only (e.g. Ashby isListed)
    pa.field("url", pa.string()),
    pa.field("title", pa.string(), nullable=False),
    pa.field("department", pa.string()),
    pa.field("employment", pa.string()),
    pa.field("workplace", pa.string(), nullable=False),       # one of WORKPLACE
    pa.field("location_raw", pa.string()),
    pa.field("locations", pa.list_(pa.string())),
    pa.field("countries", pa.list_(pa.string())),             # ISO 3166-1 alpha-2
    pa.field("remote_regions", pa.list_(pa.string())),        # worldwide | europe | eu | emea | ISO-2
    pa.field("published_at", UTC),
    pa.field("salary_raw", pa.string()),
    pa.field("salary_min", pa.float64()),
    pa.field("salary_max", pa.float64()),
    pa.field("salary_currency", pa.string()),
    pa.field("salary_period", pa.string()),                   # year | month | day | hour
    pa.field("description_text", pa.string()),
    pa.field("description_sha", pa.string()),
    pa.field("role_family", pa.string()),
    pa.field("seniority", pa.string()),
    pa.field("stack", pa.list_(pa.string())),
])

# One row per tracked job: data/state.*
STATE = pa.schema(KEY + [
    pa.field("company_id", pa.string(), nullable=False),
    pa.field("first_seen", UTC, nullable=False),
    pa.field("last_confirmed_seen", UTC, nullable=False),
    pa.field("last_changed", UTC, nullable=False),
    pa.field("current_hash", pa.string(), nullable=False),
    pa.field("status", pa.string(), nullable=False),          # one of JOB_STATUS
    pa.field("scopes", pa.list_(pa.string()), nullable=False),
    pa.field("closed_at", UTC),
])

# One row per board: data/boards.* (generated from companies/*.csv plus check results)
BOARD = pa.schema([
    pa.field("board_id", pa.string(), nullable=False),        # provider:key, e.g. ashby:n8n
    pa.field("provider", pa.string(), nullable=False),
    pa.field("key", pa.string(), nullable=False),
    pa.field("company_id", pa.string(), nullable=False),
    pa.field("api_region", pa.string()),                      # e.g. eu for Lever's EU instance
    pa.field("origins", pa.list_(pa.string()), nullable=False),
    pa.field("status", pa.string(), nullable=False),          # unchecked | live | empty | dead
    pa.field("consecutive_404", pa.int16(), nullable=False),
    pa.field("last_checked", UTC),
    pa.field("last_complete", UTC),
])
