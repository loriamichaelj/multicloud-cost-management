from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from . import analysis, categories, delivery, importers, report, storage
from .config import Config, load_config
from .providers import sample as sample_mod
from .providers.base import LiveAccessError

# 35 days covers: this week + prior week, the 14-day anomaly baseline before the
# window, and the start of the month (month-to-date, budgets) for any end date.
LOOKBACK_DAYS = 35
BILLING_LAG_DAYS = 2


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _add_report_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--end-date", type=_d, help="last day of the report week (default: see command)")
    p.add_argument("--config", help="optional TOML with [report], [budgets], [delivery], category map")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--min-anomaly", type=float, default=50.0, help="minimum $ above baseline to flag")
    p.add_argument("--deliver", nargs="+", choices=list(delivery.TARGETS), help="send a summary (preview unless --send)")
    p.add_argument("--send", action="store_true", help="actually send to --deliver targets")


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
    r.add_argument("--tag-key", default=None, help="default: config tag_key, else 'team'")
    r.add_argument("--out", help="write here instead of stdout")
    _add_report_options(r)
    r.description = "End date defaults to the latest date in the data."

    w = sub.add_parser(
        "weekly",
        help="fetch, report, archive and optionally deliver, for cron or CI (dry-run unless --live)",
        description=(
            "One scheduled run: fetch the last %d days from each provider, write data/<provider>.csv, "
            "write reports/<ISO-week>.md, then optionally deliver. Without --live it only prints the "
            "plan and sends nothing. End date defaults to %d days ago because billing data lags."
            % (LOOKBACK_DAYS, BILLING_LAG_DAYS)
        ),
    )
    w.add_argument("--providers", nargs="+", required=True, choices=["aws", "azure", "gcp"])
    w.add_argument("--live", action="store_true", help="actually call the cloud APIs (may incur cost)")
    w.add_argument("--data-dir", default="data")
    w.add_argument("--reports-dir", default="reports")
    _add_report_options(w)
    w.set_defaults(config="config.toml")
    return p


def _provider(name: str, cfg: Config):
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


def iso_week_label(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _build_report(args, cfg: Config, paths: list, tag_key: str | None = None):
    recs = [r for path in paths for r in storage.read_csv(path)]
    summary = analysis.analyze(
        recs,
        args.end_date,
        tag_key or cfg.tag_key,
        args.min_anomaly,
        category_map=categories.load(cfg.report.category_map),
        budgets=cfg.budgets,
    )
    return summary, report.render(summary, top=args.top, settings=cfg.report)


def _write(path: str | Path, text: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")


def _deliver(args, cfg: Config, summary, md: str) -> None:
    if not args.deliver:
        return
    for r in delivery.deliver(args.deliver, summary, md, cfg.delivery, cfg.report, os.environ, args.send):
        status = "SENT" if r.sent else "PREVIEW, not sent (add --send)"
        print(f"\n--- {r.target}: {status}\n{r.preview}")


def _run_weekly(args) -> int:
    if args.send and not args.deliver:
        raise SystemExit("--send needs --deliver")
    if args.send and not args.live:
        raise SystemExit("--send requires --live (a dry run has no report to send)")
    cfg = load_config(args.config)
    end = args.end_date or (date.today() - timedelta(days=BILLING_LAG_DAYS))
    start = end - timedelta(days=LOOKBACK_DAYS - 1)
    exclusive_end = end + timedelta(days=1)
    names = list(dict.fromkeys(args.providers))
    provs = [(n, _provider(n, cfg)) for n in names]

    print(f"Weekly run: week ending {end}, fetching {start} to {end} (inclusive)\n")
    for _, p in provs:
        print(p.plan(start, exclusive_end) + "\n")
    out_path = Path(args.reports_dir) / f"{iso_week_label(end)}.md"
    if not args.live:
        print(
            f"DRY RUN: nothing was sent. With --live this would write {args.data_dir}/<provider>.csv and "
            f"{out_path}" + (f", then {'send' if args.send else 'preview'} {', '.join(args.deliver)}." if args.deliver else ".")
        )
        return 0

    # Check delivery settings BEFORE any paid fetch, so a typo cannot waste the spend.
    if args.deliver:
        delivery.preflight(args.deliver, cfg.delivery, os.environ, args.send)

    paths = []
    for name, p in provs:
        recs = p.fetch(start, exclusive_end)
        path = Path(args.data_dir) / f"{name}.csv"
        n = storage.write_csv(path, recs)
        print(f"{name}: wrote {n} rows to {path}")
        paths.append(path)
    args.end_date = end
    summary, md = _build_report(args, cfg, paths)
    _write(out_path, md)
    print(f"wrote {out_path}")
    _deliver(args, cfg, summary, md)
    return 0


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
        elif args.cmd == "weekly":
            return _run_weekly(args)
        else:
            if args.send and not args.deliver:
                raise SystemExit("--send needs --deliver")
            cfg = load_config(args.config)
            if args.deliver:
                delivery.preflight(args.deliver, cfg.delivery, os.environ, args.send)
            summary, md = _build_report(args, cfg, args.input, args.tag_key)
            if args.out:
                _write(args.out, md)
                print(f"wrote {args.out}")
            else:
                sys.stdout.write(md)
            _deliver(args, cfg, summary, md)
    except (LiveAccessError, delivery.DeliveryError, FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
