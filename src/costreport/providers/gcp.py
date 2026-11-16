"""GCP billing export in BigQuery. Read-only; cost is bytes scanned, hard-capped here."""

from __future__ import annotations

import re
from datetime import date

from ..models import CostRecord
from .base import LiveAccessError, require_usd

TABLE_RE = re.compile(r"^[A-Za-z0-9_\-]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")
TAG_KEY_RE = re.compile(r"^[A-Za-z0-9_\-./:]{1,63}$")
DEFAULT_MAX_BYTES = 10_000_000_000  # 10 GB


def build_query(table: str) -> str:
    """Table identifiers cannot be query parameters, so the name is strictly validated."""
    if not TABLE_RE.match(table):
        raise LiveAccessError(f"gcp: invalid billing table name {table!r}")
    return f"""
SELECT
  DATE(usage_start_time) AS day,
  IFNULL(project.id, 'unknown') AS account,
  service.description AS service,
  IFNULL(location.region, 'global') AS region,
  IFNULL((SELECT l.value FROM UNNEST(labels) l WHERE l.key = @tag_key LIMIT 1), '') AS tag_value,
  currency AS currency,
  SUM(cost + IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS cost
FROM `{table}`
WHERE usage_start_time >= TIMESTAMP(@start)
  AND usage_start_time <  TIMESTAMP(@end)
GROUP BY day, account, service, region, tag_value, currency
""".strip()


def row_to_record(row: dict, tag_key: str) -> CostRecord:
    require_usd(row.get("currency"), "gcp")
    return CostRecord(
        date=row["day"],
        provider="gcp",
        account=row["account"],
        service=row["service"],
        region=row["region"],
        cost=float(row["cost"]),
        tags={tag_key: row["tag_value"]} if row["tag_value"] else {},
    )


def check_budget(estimated_bytes: int, max_bytes: int) -> None:
    if estimated_bytes > max_bytes:
        raise LiveAccessError(
            f"gcp: dry run estimates {estimated_bytes:,} bytes, over the cap of {max_bytes:,}; aborting"
        )


class GcpProvider:
    name = "gcp"

    def __init__(self, billing_table: str, tag_key: str, max_bytes_billed: int = DEFAULT_MAX_BYTES):
        if not TAG_KEY_RE.match(tag_key):
            raise LiveAccessError(f"gcp: invalid tag key {tag_key!r}")
        self.table = billing_table
        self.tag_key = tag_key
        self.max_bytes = int(max_bytes_billed)

    def plan(self, start: date, end: date) -> str:
        build_query(self.table)  # validates
        return (
            f"BigQuery query on {self.table}, {start} -> {end} (exclusive), label:{self.tag_key}\n"
            f"  a dry run reports bytes scanned first; aborts above {self.max_bytes:,} bytes;\n"
            f"  maximum_bytes_billed={self.max_bytes:,} is enforced on the real query"
        )

    def fetch(self, start: date, end: date) -> list[CostRecord]:
        try:
            from google.cloud import bigquery  # type: ignore
        except ImportError as e:
            raise LiveAccessError("google-cloud-bigquery not installed: pip install 'costreport[gcp]'") from e
        sql = build_query(self.table)
        params = [
            bigquery.ScalarQueryParameter("start", "STRING", start.isoformat()),
            bigquery.ScalarQueryParameter("end", "STRING", end.isoformat()),
            bigquery.ScalarQueryParameter("tag_key", "STRING", self.tag_key),
        ]
        client = bigquery.Client()
        dry = client.query(
            sql, job_config=bigquery.QueryJobConfig(query_parameters=params, dry_run=True, use_query_cache=False)
        )
        check_budget(dry.total_bytes_processed, self.max_bytes)
        job = client.query(
            sql,
            job_config=bigquery.QueryJobConfig(query_parameters=params, maximum_bytes_billed=self.max_bytes),
        )
        return [row_to_record(dict(r.items()), self.tag_key) for r in job.result()]
