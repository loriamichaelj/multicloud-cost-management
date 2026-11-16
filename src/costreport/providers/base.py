from __future__ import annotations

from datetime import date
from typing import Protocol

from ..models import CostRecord


class LiveAccessError(RuntimeError):
    """Raised when live access is misconfigured or a cost guard trips."""


class Provider(Protocol):
    name: str

    def plan(self, start: date, end: date) -> str:
        """Describe the request(s) and estimated cost. Must not touch the network."""

    def fetch(self, start: date, end: date) -> list[CostRecord]:
        """Run the read-only query. [start, end) -- end is exclusive."""


def require_usd(currency: str | None, where: str) -> None:
    if currency and currency.upper() != "USD":
        raise LiveAccessError(
            f"{where}: got currency {currency!r}; only USD is supported (refusing to mix currencies)"
        )
