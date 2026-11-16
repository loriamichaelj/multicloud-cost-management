from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .models import PROVIDERS


@dataclass
class ReportSettings:
    """Presentation thresholds. Only affect wording and filtering, never the numbers."""

    min_change_usd: float = 100.0   # a mover must change by at least this many dollars...
    min_change_pct: float = 0.05    # ...and at least this fraction (new spend is exempt from the %)
    untagged_target: float = 0.10   # untagged share above this is flagged
    category_map: str | None = None  # path to a custom service_categories.toml


@dataclass
class Config:
    tag_key: str = "team"
    aws: dict = field(default_factory=dict)
    azure: dict = field(default_factory=dict)
    gcp: dict = field(default_factory=dict)
    report: ReportSettings = field(default_factory=ReportSettings)
    budgets: dict[str, float] = field(default_factory=dict)  # monthly USD per provider


def _number(section: str, key: str, value, lo: float, hi: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"config: [{section}] {key} must be a number, got {value!r}")
    if value < lo or (hi is not None and value > hi):
        bound = f"between {lo} and {hi}" if hi is not None else f">= {lo}"
        raise ValueError(f"config: [{section}] {key} must be {bound}, got {value}")
    return float(value)


def load_config(path: str | Path | None) -> Config:
    if path is None:
        return Config()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config file not found: {p} (see config.example.toml)")
    raw = tomllib.loads(p.read_text(encoding="utf-8"))

    rep = raw.get("report", {})
    settings = ReportSettings()
    if "min_change_usd" in rep:
        settings.min_change_usd = _number("report", "min_change_usd", rep["min_change_usd"], 0)
    if "min_change_pct" in rep:
        settings.min_change_pct = _number("report", "min_change_pct", rep["min_change_pct"], 0, 10)
    if "untagged_target" in rep:
        settings.untagged_target = _number("report", "untagged_target", rep["untagged_target"], 0, 1)
    if "category_map" in rep:
        cm = Path(rep["category_map"])
        settings.category_map = str(cm if cm.is_absolute() else p.parent / cm)

    budgets = {}
    for prov, amount in raw.get("budgets", {}).items():
        if prov not in PROVIDERS:
            raise ValueError(f"config: [budgets] unknown provider {prov!r} (expected one of {PROVIDERS})")
        budgets[prov] = _number("budgets", prov, amount, 0.01)

    return Config(
        tag_key=raw.get("tag_key", "team"),
        aws=raw.get("aws", {}),
        azure=raw.get("azure", {}),
        gcp=raw.get("gcp", {}),
        report=settings,
        budgets=budgets,
    )
