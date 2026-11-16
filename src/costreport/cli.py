from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from . import analysis, categories, importers, report, storage
from .config import load_config
from .providers import sample as sample_mod
from .providers.base import LiveAccessError


def _d(s: str) -> date:
    return date.fromisoformat(s)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="costreport", description="Multi-cloud cost analysis and weekly executive reports.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="generate synthetic data (free, offline)")
    s.add_argument("--out", required=True)
    s.add_argument("--end-date", type=_d, default=date.today() - timedelta(days=1))
    s.add_argument("--days", type=int, default=70)
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--tag-key", default="team")

    f = sub.add_parser("fetch", help="query a cloud cost API (dry-run unless --live)")
    f.add_argument("provider", choices=["aws", "azure", "gcp"])
    f.add_argument("--start", type=_d, required=True)
    f.add_argument("--end", type=_d, required=True, help="exclusive")
    f.add_argument("--config", default="config.toml")
    f.add_argument("--out", help="CSV to write (required with --live)")
    f.add_argument("--live", action="store_true", help="actually call the cloud API (may incur cost)")

    i = sub.add_parser("import", help="convert cost exports you downloaded into normalized CSV (offline)")
    i.add_argument("files", nargs="+", help="CSV or CSV.GZ exports")
    i.add_argument("--provider", required=True, choices=["aws", "azure", "gcp"])
    i.add_argument("--tag-key", default="team")
    i.add_argument("--out", required=True)

    r = sub.add_parser("report", help="build the weekly Markdown report from local CSVs (offline)")
    r.add_argument("--input", nargs="+", required=True)
    r.add_argument("--end-date", type=_d, help="last day of the report week; default: latest date in data")
    r.add_argument("--config", help="optional TOML with [report] thresholds, [budgets], category map")
    r.add_argument("--tag-key", default=None, help="default: config tag_key, else 'team'")
    r.add_argument("--top", type=int, default=10)
    r.add_argument("--min-anomaly", type=float, default=50.0, help="minimum $ above baseline to flag")
    r.add_argument("--out", help="write here instead of stdout")
    return p


def _provider(name: str, cfg):
    from .providers.aws import AwsProvider
    from .providers.azure import AzureProvider
    from .providers.gcp import GcpProvider

    if name == "aws":
        return AwsProvider(cfg.tag_key, cfg.aws.get("profile"))
    if name == "azure":
        if "scope" not in cfg.azure:
            raise LiveAccessError("config: [azure] scope is required")
        return AzureProvider(cfg.azure["scope"], cfg.tag_key)
    if "billing_table" not in cfg.gcp:
        raise LiveAccessError("config: [gcp] billing_table is required")
    kwargs = {"max_bytes_billed": cfg.gcp["max_bytes_billed"]} if "max_bytes_billed" in cfg.gcp else {}
    return GcpProvider(cfg.gcp["billing_table"], cfg.tag_key, **kwargs)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "sample":
            recs = sample_mod.generate(args.end_date, args.days, args.seed, args.tag_key)
            n = storage.write_csv(args.out, recs)
            print(f"wrote {n} synthetic rows to {args.out} (no cloud calls made)")
        elif args.cmd == "fetch":
            if args.end <= args.start:
                raise SystemExit("--end must be after --start")
            prov = _provider(args.provider, load_config(args.config))
            print(prov.plan(args.start, args.end))
            if not args.live:
                print("\nDRY RUN: nothing was sent. Re-run with --live --out FILE to execute.")
                return 0
            if not args.out:
                raise SystemExit("--out is required with --live")
            recs = prov.fetch(args.start, args.end)
            n = storage.write_csv(args.out, recs)
            print(f"wrote {n} rows to {args.out}")
        elif args.cmd == "import":
            res = importers.import_files(args.files, args.provider, args.tag_key)
            for f in res.files:
                picked = ", ".join(f"{role}={hdr!r}" for role, hdr in f.columns.items())
                print(f"{f.path}: {f.rows_read} rows read, {f.rows_skipped} skipped (blank or zero cost)")
                print(f"  columns used: {picked}")
            if not res.records:
                raise ValueError("no cost rows found in the input")
            n = storage.write_csv(args.out, res.records)
            days = sorted({r.date for r in res.records})
            print(
                f"wrote {n} rows to {args.out}: {days[0]} to {days[-1]} ({len(days)} days), "
                f"{len({r.service for r in res.records})} services, total ${sum(r.cost for r in res.records):,.2f}"
            )
            print("check the columns above, then run `costreport report`. (no cloud calls made)")
        else:
            cfg = load_config(args.config)
            tag_key = args.tag_key or cfg.tag_key
            recs = [r for path in args.input for r in storage.read_csv(path)]
            summary = analysis.analyze(
                recs,
                args.end_date,
                tag_key,
                args.min_anomaly,
                category_map=categories.load(cfg.report.category_map),
                budgets=cfg.budgets,
            )
            md = report.render(summary, top=args.top, settings=cfg.report)
            if args.out:
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.out).write_text(md, encoding="utf-8")
                print(f"wrote {args.out}")
            else:
                sys.stdout.write(md)
    except (LiveAccessError, FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
