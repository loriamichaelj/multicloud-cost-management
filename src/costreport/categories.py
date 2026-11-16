"""Cross-cloud service category mapping, driven by an editable TOML data file."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

OTHER = "Other"


@dataclass(frozen=True)
class CategoryMap:
    rules: tuple[tuple[str, tuple[str, ...]], ...]

    def categorize(self, service: str) -> str:
        s = service.lower()
        for category, patterns in self.rules:
            if any(p in s for p in patterns):
                return category
        return OTHER


def parse(text: str, source: str = "category map") -> CategoryMap:
    raw = tomllib.loads(text)
    rules = []
    for i, rule in enumerate(raw.get("rule", []), 1):
        cat, match = rule.get("category"), rule.get("match")
        if not isinstance(cat, str) or not cat.strip():
            raise ValueError(f"{source}: rule {i} needs a non-empty 'category'")
        if cat == OTHER:
            raise ValueError(f"{source}: rule {i} uses the reserved category {OTHER!r}")
        if not isinstance(match, list) or not match or not all(isinstance(m, str) and m.strip() for m in match):
            raise ValueError(f"{source}: rule {i} ({cat}) needs a non-empty 'match' list of strings")
        rules.append((cat, tuple(m.lower() for m in match)))
    if not rules:
        raise ValueError(f"{source}: no [[rule]] entries")
    return CategoryMap(tuple(rules))


def load_default() -> CategoryMap:
    text = resources.files("costreport").joinpath("data/service_categories.toml").read_text(encoding="utf-8")
    return parse(text, "packaged service_categories.toml")


def load(path: str | Path | None) -> CategoryMap:
    if path is None:
        return load_default()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"category map not found: {p}")
    return parse(p.read_text(encoding="utf-8"), str(p))
