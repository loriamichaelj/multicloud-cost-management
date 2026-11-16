"""Import cost exports that you downloaded yourself into the normalized records.

No network, no credentials, no cloud API calls: this only reads local files.

Columns are matched by *normalized name* (lowercased, punctuation removed), so
`lineItem/UsageStartDate` (CUR) and `line_item_usage_start_date` (CUR 2.0 /
Data Exports) both resolve to the same thing. When several candidate columns
exist, the earlier alias in the lists below wins. The aliases are written from
vendor documentation for: AWS CUR (legacy and 2.0), Azure cost exports (EA and
MCA), FOCUS 1.x, and plain CSVs from a BigQuery query. They have not been
checked against real exports. `import` prints the columns it picked so a wrong
guess is visible.

Everything the export contains is summed as-is (tax, credits and refunds
included), so filter the export first if you want a narrower view.
"""

from __future__ import annotations

import csv
import gzip
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

from .models import PROVIDERS, CostRecord

ALIASES: dict[str, list[str]] = {
    "date": [
        "chargeperiodstart", "lineitemusagestartdate", "usagedatetime", "usagedate",
        "date", "usagestartdate", "usagestarttime", "day",
    ],
    "service": [
        "servicename", "productproductname", "servicedescription", "metercategory",
        "lineitemproductcode", "productcode", "servicefamily", "service",
    ],
    "cost": [
        "billedcost", "lineitemunblendedcost", "costinbillingcurrency", "pretaxcost",
        "cost", "unblendedcost", "costusd",
    ],
    "account": [
        "subaccountid", "lineitemusageaccountid", "subscriptionid", "projectid",
        "accountid", "account", "subscriptionname",
    ],
    "region": [
        "regionname", "regionid", "productregioncode", "productregion", "resourcelocation",
        "locationregion", "region", "location",
    ],
    "currency": [
        "billingcurrency", "billingcurrencycode", "lineitemcurrencycode", "currencycode", "currency",
    ],
    "tags_blob": ["tags", "resourcetags", "labels", "tagsjson"],
    "tag_value": ["tagvalue"],
}
REQUIRED = ("date", "service", "cost")


def norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.lower())


@dataclass
class FileResult:
    path: str
    columns: dict[str, str]  # role -> original header
    rows_read: int = 0
    rows_skipped: int = 0


@dataclass
class ImportResult:
    records: list[CostRecord] = field(default_factory=list)
    files: list[FileResult] = field(default_factory=list)


def resolve_columns(headers: list[str], tag_key: str) -> dict[str, str]:
    by_norm: dict[str, str] = {}
    for h in headers:
        by_norm.setdefault(norm(h), h)
    found: dict[str, str] = {}
    for role, aliases in ALIASES.items():
        for a in aliases:
            if a in by_norm:
                found[role] = by_norm[a]
                break
    nk = norm(tag_key)
    for cand in (f"resourcetagsuser{nk}", f"resourcetags{nk}", f"tags{nk}", f"labels{nk}", nk):
        if cand in by_norm:
            found["tag_col"] = by_norm[cand]
            break
    return found


def parse_date(value: str) -> date:
    s = value.strip()
    if re.fullmatch(r"\d{8}", s):
        return date(int(s[:4]), int(s[4:6]), int(s[6:]))
    if re.match(r"\d{4}[-/]\d{2}[-/]\d{2}", s):
        return date.fromisoformat(s[:10].replace("/", "-"))
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        a, b, year = int(m[1]), int(m[2]), int(m[3])
        # Azure exports use MM/DD/YYYY; only treat as DD/MM when MM/DD is impossible.
        month, day = (b, a) if a > 12 else (a, b)
        return date(year, month, day)
    raise ValueError(f"unrecognized date {value!r}")


def _tag_from_blob(text: str, key: str) -> str:
    """Tags arrive as a JSON object, a brace-less `"k": "v"` list (Azure), or a list of {key,value}."""
    text = (text or "").strip()
    if not text:
        return ""
    candidate = text if text.startswith(("{", "[")) else "{" + text + "}"
    try:
        data = json.loads(candidate)
    except ValueError:
        return ""
    wanted = {key.lower(), f"user_{key}".lower(), f"user:{key}".lower()}
    if isinstance(data, dict):
        for k, v in data.items():
            if str(k).lower() in wanted and v is not None:
                return str(v).strip()
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and str(item.get("key", "")).lower() in wanted:
                return str(item.get("value") or "").strip()
    return ""


def _open(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", newline="", encoding="utf-8-sig")
    return path.open(newline="", encoding="utf-8-sig")


def import_files(paths: Iterable[str | Path], provider: str, tag_key: str = "team") -> ImportResult:
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; expected one of {PROVIDERS}")
    agg: dict[tuple, float] = defaultdict(float)
    result = ImportResult()

    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            raise FileNotFoundError(f"input not found: {path}")
        with _open(path) as fh:
            reader = csv.DictReader(fh)
            headers = reader.fieldnames or []
            cols = resolve_columns(headers, tag_key)
            missing = [r for r in REQUIRED if r not in cols]
            if missing:
                raise ValueError(
                    f"{path.name}: could not find column(s) for {missing}.\n"
                    f"  headers seen: {headers[:25]}{' ...' if len(headers) > 25 else ''}\n"
                    f"  accepted names for each: " + "; ".join(f"{r}={ALIASES[r]}" for r in missing)
                )
            fr = FileResult(str(path), cols)
            for lineno, row in enumerate(reader, start=2):
                fr.rows_read += 1
                raw_cost = (row.get(cols["cost"]) or "").strip().replace(",", "")
                raw_date = (row.get(cols["date"]) or "").strip()
                if not raw_cost or not raw_date:
                    fr.rows_skipped += 1
                    continue
                try:
                    cost = float(raw_cost)
                    day = parse_date(raw_date)
                except ValueError as e:
                    raise ValueError(f"{path.name} line {lineno}: {e}") from e
                if "currency" in cols:
                    cur = (row.get(cols["currency"]) or "").strip().upper()
                    if cur and cur != "USD":
                        raise ValueError(
                            f"{path.name} line {lineno}: currency {cur!r}; only USD is supported "
                            "(refusing to mix currencies)"
                        )
                if cost == 0:
                    fr.rows_skipped += 1
                    continue

                tag = ""
                if "tag_col" in cols:
                    tag = (row.get(cols["tag_col"]) or "").strip()
                if not tag and "tag_value" in cols:
                    tag = (row.get(cols["tag_value"]) or "").strip()
                if not tag and "tags_blob" in cols:
                    tag = _tag_from_blob(row.get(cols["tags_blob"]) or "", tag_key)

                key = (
                    day,
                    (row.get(cols["account"]) or "").strip() or "all" if "account" in cols else "all",
                    (row.get(cols["service"]) or "").strip() or "Unknown",
                    (row.get(cols["region"]) or "").strip() or "all" if "region" in cols else "all",
                    tag,
                )
                agg[key] += cost
        result.files.append(fr)

    for (day, account, service, region, tag), cost in sorted(agg.items()):
        result.records.append(
            CostRecord(day, provider, account, service, region, round(cost, 6), {tag_key: tag} if tag else {})
        )
    return result
