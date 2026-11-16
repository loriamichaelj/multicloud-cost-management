"""Normalized CSV read/write. This is the boundary between paid fetching and free analysis."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Iterable

from .models import CostRecord

FIELDS = ["date", "provider", "account", "service", "region", "cost", "tags"]


def write_csv(path: str | Path, records: Iterable[CostRecord]) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in records:
            w.writerow(
                {
                    "date": r.date.isoformat(),
                    "provider": r.provider,
                    "account": r.account,
                    "service": r.service,
                    "region": r.region,
                    "cost": f"{r.cost:.6f}",
                    "tags": json.dumps(dict(r.tags), sort_keys=True),
                }
            )
            n += 1
    return n


def read_csv(path: str | Path) -> list[CostRecord]:
    out: list[CostRecord] = []
    with Path(path).open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = set(FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")
        for row in reader:
            out.append(
                CostRecord(
                    date=date.fromisoformat(row["date"]),
                    provider=row["provider"],
                    account=row["account"],
                    service=row["service"],
                    region=row["region"],
                    cost=float(row["cost"]),
                    tags=json.loads(row["tags"] or "{}"),
                )
            )
    return out
