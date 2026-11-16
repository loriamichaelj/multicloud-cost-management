"""AWS Cost Explorer. Read-only. Costs ~$0.01 per paginated request (verify current pricing)."""

from __future__ import annotations

from datetime import date, datetime

from ..models import CostRecord
from .base import LiveAccessError

SERVICE_GROUP = {"Type": "DIMENSION", "Key": "SERVICE"}


def build_request(start: date, end: date, tag_key: str) -> dict:
    """end is exclusive, as Cost Explorer requires."""
    return {
        "TimePeriod": {"Start": start.isoformat(), "End": end.isoformat()},
        "Granularity": "DAILY",
        "Metrics": ["UnblendedCost"],
        "GroupBy": [SERVICE_GROUP, {"Type": "TAG", "Key": tag_key}],
    }


def parse_pages(pages: list[dict], tag_key: str) -> list[CostRecord]:
    out: list[CostRecord] = []
    prefix = f"{tag_key}$"
    for page in pages:
        for day in page.get("ResultsByTime", []):
            d = datetime.strptime(day["TimePeriod"]["Start"][:10], "%Y-%m-%d").date()
            for g in day.get("Groups", []):
                service, tag_raw = g["Keys"][0], g["Keys"][1]
                metric = g["Metrics"]["UnblendedCost"]
                if metric.get("Unit", "USD").upper() != "USD":
                    raise LiveAccessError(f"aws: non-USD unit {metric['Unit']!r}")
                value = tag_raw[len(prefix):] if tag_raw.startswith(prefix) else tag_raw
                out.append(
                    CostRecord(
                        date=d,
                        provider="aws",
                        account="all",
                        service=service,
                        region="all",
                        cost=float(metric["Amount"]),
                        tags={tag_key: value} if value else {},
                    )
                )
    return out


class AwsProvider:
    name = "aws"

    def __init__(self, tag_key: str, profile: str | None = None):
        self.tag_key = tag_key
        self.profile = profile

    def plan(self, start: date, end: date) -> str:
        # One page typically holds many day-groups; assume a handful of pages per month.
        pages = max(1, ((end - start).days // 30) + 1)
        return (
            f"AWS Cost Explorer GetCostAndUsage {start} -> {end} (exclusive), DAILY,\n"
            f"  group by SERVICE + TAG:{self.tag_key}\n"
            f"  estimated {pages}+ paginated request(s) at ~$0.01 each (~${0.01 * pages:.2f}+)"
        )

    def fetch(self, start: date, end: date) -> list[CostRecord]:
        try:
            import boto3  # type: ignore
        except ImportError as e:
            raise LiveAccessError("boto3 not installed: pip install 'costreport[aws]'") from e
        session = boto3.Session(profile_name=self.profile) if self.profile else boto3.Session()
        ce = session.client("ce", region_name="us-east-1")
        req = build_request(start, end, self.tag_key)
        pages: list[dict] = []
        token = None
        while True:
            kwargs = dict(req, **({"NextPageToken": token} if token else {}))
            page = ce.get_cost_and_usage(**kwargs)
            pages.append(page)
            token = page.get("NextPageToken")
            if not token:
                break
        return parse_pages(pages, self.tag_key)
