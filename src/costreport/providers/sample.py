"""Deterministic synthetic cost data. Generates no network traffic and costs nothing."""

from __future__ import annotations

import random
from datetime import date, timedelta

from ..models import CostRecord

TEAMS = {"platform": 0.35, "data": 0.25, "web": 0.20, "ml": 0.12, "security": 0.08}

# (provider, account, region, service, base $/day, weekly growth, weekday_heavy, untagged share)
CATALOG = [
    ("aws", "aws-main", "us-east-1", "Amazon Elastic Compute Cloud - Compute", 1800, 0.00, True, 0.04),
    ("aws", "aws-main", "us-east-1", "Amazon Relational Database Service", 900, 0.01, False, 0.03),
    ("aws", "aws-main", "us-east-1", "Amazon Simple Storage Service", 420, 0.02, False, 0.10),
    ("aws", "aws-main", "us-east-1", "EC2 - Other", 350, 0.015, False, 0.12),
    ("aws", "aws-main", "us-east-1", "Amazon Elastic Kubernetes Service", 260, 0.00, False, 0.02),
    ("aws", "aws-main", "us-east-1", "AmazonCloudWatch", 120, 0.03, False, 0.15),
    ("azure", "azure-sub-1", "eastus", "Virtual Machines", 1100, 0.00, True, 0.06),
    ("azure", "azure-sub-1", "eastus", "Azure Cosmos DB", 500, 0.04, False, 0.05),
    ("azure", "azure-sub-1", "eastus", "Storage", 260, 0.01, False, 0.30),
    ("azure", "azure-sub-1", "eastus", "Azure Kubernetes Service", 480, 0.00, False, 0.03),
    ("azure", "azure-sub-1", "eastus", "Azure SQL Database", 390, 0.005, False, 0.04),
    ("gcp", "gcp-proj-1", "us-central1", "BigQuery", 650, 0.01, False, 0.05),
    ("gcp", "gcp-proj-1", "us-central1", "Compute Engine", 780, 0.00, True, 0.05),
    ("gcp", "gcp-proj-1", "us-central1", "Cloud Storage", 210, 0.01, False, 0.08),
    ("gcp", "gcp-proj-1", "us-central1", "Cloud Run", 140, 0.00, False, 0.02),
    ("gcp", "gcp-proj-1", "us-central1", "Vertex AI", 300, 0.06, False, 0.04),
]

# Injected one-off: BigQuery runaway query 3 days before the end date.
SPIKE_SERVICE = "BigQuery"
SPIKE_DAYS_BEFORE_END = 3
SPIKE_MULTIPLIER = 4.5


def generate(
    end: date, days: int = 70, seed: int = 42, tag_key: str = "team"
) -> list[CostRecord]:
    """Daily records for [end - days + 1, end], inclusive."""
    rng = random.Random(seed)
    start = end - timedelta(days=days - 1)
    team_names = list(TEAMS)
    records: list[CostRecord] = []
    for offset in range(days):
        d = start + timedelta(days=offset)
        weeks = offset / 7
        weekend = d.weekday() >= 5
        for provider, account, region, service, base, growth, weekday_heavy, untagged in CATALOG:
            cost = base * (1 + growth) ** weeks * rng.gauss(1.0, 0.03)
            if weekday_heavy and weekend:
                cost *= 0.75
            if service == SPIKE_SERVICE and d == end - timedelta(days=SPIKE_DAYS_BEFORE_END):
                cost *= SPIKE_MULTIPLIER
            weights = [TEAMS[t] * rng.uniform(0.85, 1.15) for t in team_names]
            scale = (1 - untagged) / sum(weights)
            shares = [(t, w * scale) for t, w in zip(team_names, weights)]
            shares.append((None, untagged))
            for team, share in shares:
                records.append(
                    CostRecord(
                        date=d,
                        provider=provider,
                        account=account,
                        service=service,
                        region=region,
                        cost=round(cost * share, 6),
                        tags={tag_key: team} if team else {},
                    )
                )
    return records
