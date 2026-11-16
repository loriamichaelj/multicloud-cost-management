from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Mapping

PROVIDERS = ("aws", "azure", "gcp")
PROVIDER_LABELS = {"aws": "AWS", "azure": "Azure", "gcp": "GCP"}


@dataclass(frozen=True)
class CostRecord:
    """One normalized cost row. Currency is always USD."""

    date: date
    provider: str
    account: str
    service: str
    region: str
    cost: float
    tags: Mapping[str, str] = field(default_factory=dict)
