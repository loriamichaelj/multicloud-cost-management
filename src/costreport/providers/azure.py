"""Azure Cost Management Query API. Read-only; no per-call charge but rate limited."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

from ..models import CostRecord
from .base import LiveAccessError, require_usd

API_VERSION = "2023-11-01"
COST_COLUMNS = ("Cost", "PreTaxCost", "CostUSD")
MAX_RETRIES = 5
MAX_SLEEP = 60


def build_body(start: date, end: date, tag_key: str) -> dict:
    """end is inclusive in the Azure API, so the caller's exclusive end is shifted back a day."""
    last = end - timedelta(days=1)
    return {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": f"{start.isoformat()}T00:00:00Z", "to": f"{last.isoformat()}T23:59:59Z"},
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [
                {"type": "Dimension", "name": "ServiceName"},
                {"type": "TagKey", "name": tag_key},
            ],
        },
    }


def _parse_day(v) -> date:
    s = str(v)
    if s.isdigit() and len(s) == 8:
        return datetime.strptime(s, "%Y%m%d").date()
    return datetime.fromisoformat(s.replace("Z", "")).date()


def parse_rows(columns: list[dict], rows: list[list], tag_key: str, account: str = "all") -> list[CostRecord]:
    idx = {c["name"]: i for i, c in enumerate(columns)}
    cost_col = next((c for c in COST_COLUMNS if c in idx), None)
    if cost_col is None or "ServiceName" not in idx:
        raise LiveAccessError(f"azure: unexpected columns {list(idx)}")
    date_col = "UsageDate" if "UsageDate" in idx else "BillingMonth"
    out: list[CostRecord] = []
    for row in rows:
        require_usd(row[idx["Currency"]] if "Currency" in idx else None, "azure")
        tag_val = row[idx["TagValue"]] if "TagValue" in idx else ""
        out.append(
            CostRecord(
                date=_parse_day(row[idx[date_col]]),
                provider="azure",
                account=account,
                service=row[idx["ServiceName"]] or "Unknown",
                region="all",
                cost=float(row[idx[cost_col]]),
                tags={tag_key: tag_val} if tag_val else {},
            )
        )
    return out


class AzureProvider:
    name = "azure"

    def __init__(self, scope: str, tag_key: str):
        self.scope = scope.strip("/")
        self.tag_key = tag_key

    def _url(self) -> str:
        return (
            f"https://management.azure.com/{self.scope}"
            f"/providers/Microsoft.CostManagement/query?api-version={API_VERSION}"
        )

    def plan(self, start: date, end: date) -> str:
        return (
            f"Azure Cost Management Query POST {self._url()}\n"
            f"  {start} -> {end} (exclusive), Daily, group by ServiceName + TagKey:{self.tag_key}\n"
            f"  no per-call charge; may return HTTP 429 (retried with backoff)"
        )

    def _post(self, url: str, body: dict, token: str) -> dict:
        for attempt in range(MAX_RETRIES):
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode(),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < MAX_RETRIES - 1:
                    time.sleep(min(float(e.headers.get("Retry-After", 5)), MAX_SLEEP))
                    continue
                raise LiveAccessError(f"azure: HTTP {e.code} from Cost Management") from e
        raise LiveAccessError("azure: retries exhausted")

    def fetch(self, start: date, end: date) -> list[CostRecord]:
        try:
            from azure.identity import DefaultAzureCredential  # type: ignore
        except ImportError as e:
            raise LiveAccessError("azure-identity not installed: pip install 'costreport[azure]'") from e
        token = DefaultAzureCredential().get_token("https://management.azure.com/.default").token
        body = build_body(start, end, self.tag_key)
        url: str | None = self._url()
        out: list[CostRecord] = []
        while url:
            data = self._post(url, body, token)
            props = data["properties"]
            out.extend(parse_rows(props["columns"], props["rows"], self.tag_key))
            url = props.get("nextLink")
        return out
