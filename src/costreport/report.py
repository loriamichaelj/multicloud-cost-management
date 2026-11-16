"""Render a Summary as an executive Markdown report. Deterministic: same input, same output."""

from __future__ import annotations

from .analysis import Driver, Summary
from .config import ReportSettings
from .models import PROVIDER_LABELS


def money(v: float) -> str:
    sign = "-" if v < 0 else ""
    a = abs(v)
    return f"{sign}${a:,.0f}" if a >= 100 else f"{sign}${a:,.2f}"


def delta_money(v: float) -> str:
    if round(v) == 0:
        return "$0"
    arrow = "▲" if v > 0 else "▼"
    return f"{arrow} {money(abs(v))}"


def pct(v: float | None) -> str:
    if v is None:
        return "new"
    return f"{v * 100:+.1f}%"


def _esc(s: str) -> str:
    return s.replace("|", "\\|")


def _table(headers: list[str], rows: list[list[str]], right_from: int = 1) -> str:
    align = ["---" if i < right_from else "---:" for i in range(len(headers))]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(align) + " |"]
    lines += ["| " + " | ".join(_esc(c) if i == 0 else c for i, c in enumerate(r)) + " |" for i, r in enumerate(rows)]
    return "\n".join(lines)


def _driver_rows(drivers: list[Driver], top: int) -> list[list[str]]:
    rows = [
        [d.name, money(d.current), money(d.previous), delta_money(d.delta), pct(d.pct), f"{d.share * 100:.1f}%"]
        for d in drivers[:top]
    ]
    rest = drivers[top:]
    if rest:
        cur = sum(d.current for d in rest)
        prev = sum(d.previous for d in rest)
        share = sum(d.share for d in rest)
        rows.append(
            [
                f"Other ({len(rest)})",
                money(cur),
                money(prev),
                delta_money(cur - prev),
                pct(None if prev <= 0 else (cur - prev) / prev),
                f"{share * 100:.1f}%",
            ]
        )
    return rows


def is_material(d: Driver, cfg: ReportSettings) -> bool:
    """A change is worth mentioning only if it clears both the $ and % floors.
    Spend with no prior-week baseline ("new") is exempt from the % floor."""
    if abs(d.delta) < cfg.min_change_usd:
        return False
    return d.pct is None or abs(d.pct) >= cfg.min_change_pct


def headlines(s: Summary, cfg: ReportSettings) -> list[str]:
    out = []
    direction = "up" if s.delta > 0 else "down" if s.delta < 0 else "flat"
    if direction == "flat":
        out.append(f"**Total spend was {money(s.total)}**, flat versus the prior {s.window.days} days.")
    else:
        out.append(
            f"**Total spend was {money(s.total)}**, {direction} {money(abs(s.delta))} "
            f"({pct(s.pct)}) versus the prior {s.window.days} days ({money(s.prior_total)})."
        )
    if s.services:
        top = s.services[0]
        out.append(f"Largest cost driver: **{top.name}** at {money(top.current)} ({top.share * 100:.0f}% of spend).")
    ups = [d for d in s.increases if is_material(d, cfg)]
    downs = [d for d in s.decreases if is_material(d, cfg)]
    if ups:
        out.append(f"Biggest increase: **{ups[0].name}**, {delta_money(ups[0].delta)} ({pct(ups[0].pct)}).")
    if downs:
        out.append(f"Biggest saving: **{downs[0].name}**, {delta_money(downs[0].delta)} ({pct(downs[0].pct)}).")
    if not ups and not downs:
        out.append(f"No service changed by more than {money(cfg.min_change_usd)} and {cfg.min_change_pct * 100:.0f}%.")
    if s.anomalies:
        a = s.anomalies[0]
        out.append(
            f"**{len(s.anomalies)} anomal{'y' if len(s.anomalies) == 1 else 'ies'} flagged.** "
            f"Largest: {a.service} on {a.day.isoformat()} at {money(a.cost)} vs ~{money(a.baseline)} typical."
        )
    else:
        out.append("No cost anomalies flagged.")
    flag = (
        f" This is above the {cfg.untagged_target * 100:.0f}% target; see Data quality."
        if s.untagged_share > cfg.untagged_target
        else ""
    )
    out.append(f"Spend with no `{s.tag_key}` tag: {s.untagged_share * 100:.1f}% ({money(s.untagged_cost)}).{flag}")
    if s.mtd:
        out.append(
            f"Month-to-date {money(s.mtd.spend)}; naive projection to month end **{money(s.mtd.projected)}**."
        )
    over = [b for b in s.budgets if b.over]
    for b in over:
        out.append(
            f"**{PROVIDER_LABELS.get(b.provider, b.provider)} is projected {money(b.projected - b.budget)} over budget** "
            f"({money(b.projected)} vs {money(b.budget)})."
        )
    if s.budgets and not over:
        out.append("All budgeted clouds are projected to stay within their monthly budget.")
    return out


def render(
    s: Summary,
    top: int = 10,
    generated_note: str | None = None,
    settings: ReportSettings | None = None,
) -> str:
    cfg = settings or ReportSettings()
    L: list[str] = []
    L.append(f"# Weekly Cloud Cost Report: {s.window.start} to {s.window.end}")
    L.append("")
    L.append(
        f"Compared with {s.prior.start} to {s.prior.end}. All figures in USD. "
        f"Providers: {', '.join(d.name for d in s.providers)}."
    )
    if generated_note:
        L.append("")
        L.append(f"_{generated_note}_")
    L += ["", "## Headlines", ""]
    L += [f"- {h}" for h in headlines(s, cfg)]

    L += ["", "## Spend by cloud", ""]
    L.append(_table(["Cloud", "This week", "Prior week", "Change", "%", "Share"], _driver_rows(s.providers, 99)))

    hdr = ["", "This week", "Prior week", "Change", "%", "Share"]
    L += ["", "## Spend by category", ""]
    L.append(_table(["Category"] + hdr[1:], _driver_rows(s.categories, 99)))
    L += ["", "Categories group similar services across clouds. The mapping is editable (`service_categories.toml`)."]
    L += ["", f"## Top cost drivers: services (top {min(top, len(s.services))} of {len(s.services)})", ""]
    L.append(_table(["Service"] + hdr[1:], _driver_rows(s.services, top)))

    L += ["", f"## Top cost drivers: by `{s.tag_key}`", ""]
    L.append(_table([f"`{s.tag_key}`"] + hdr[1:], _driver_rows(s.tags, top)))

    ups = [d for d in s.increases if is_material(d, cfg)]
    downs = [d for d in s.decreases if is_material(d, cfg)]
    L += ["", "## Biggest movers", ""]
    if ups or downs:
        L.append(f"Changes of at least {money(cfg.min_change_usd)} and {cfg.min_change_pct * 100:.0f}% versus the prior week.")
        L.append("")
        n = min(5, max(len(ups), len(downs)))
        for title, items in (("Increases", ups[:n]), ("Decreases", downs[:n])):
            L += [f"**{title}**", ""]
            if items:
                L.append(_table(["Service", "Prior week", "This week", "Change", "%"],
                                [[d.name, money(d.previous), money(d.current), delta_money(d.delta), pct(d.pct)] for d in items]))
            else:
                L.append("None.")
            L.append("")
        L.pop()
    else:
        L.append(
            f"No service changed by at least {money(cfg.min_change_usd)} and {cfg.min_change_pct * 100:.0f}%."
        )

    L += ["", "## Anomalies", ""]
    if s.anomalies:
        L.append("Days at least 3 standard deviations above the trailing 14-day baseline for that service.")
        L.append("")
        L.append(_table(["Date", "Service", "Cost", "Typical", "Excess"],
                        [[a.day.isoformat(), f"{a.service} ({a.provider.upper()})", money(a.cost), money(a.baseline), delta_money(a.excess)]
                         for a in s.anomalies[:10]], right_from=2))
        if len(s.anomalies) > 10:
            L += ["", f"{len(s.anomalies) - 10} more not shown."]
    else:
        L.append("None flagged this week.")

    if s.mtd:
        m = s.mtd
        L += ["", "## Month to date", ""]
        L.append(f"- Spend {m.month_start} to {m.through}: **{money(m.spend)}** over {m.days_elapsed} days of data.")
        L.append(f"- Naive run-rate projection for the full {m.days_in_month}-day month: **{money(m.projected)}**.")
        L.append("- The projection is linear and ignores weekday patterns, commitments and known changes.")

    if s.budgets:
        L += ["", "## Budget", ""]
        L.append(
            _table(
                ["Cloud", "Monthly budget", "Month to date", "Used", "Projected", "vs budget", "Status"],
                [
                    [
                        PROVIDER_LABELS.get(b.provider, b.provider),
                        money(b.budget),
                        money(b.spend),
                        f"{b.used * 100:.0f}%",
                        money(b.projected),
                        delta_money(b.projected - b.budget),
                        "**Over**" if b.over else "On track",
                    ]
                    for b in s.budgets
                ],
            )
        )

    L += ["", "## Data quality", ""]
    L.append(f"- Untagged (`{s.tag_key}`): {money(s.untagged_cost)}, {s.untagged_share * 100:.1f}% of spend.")
    if s.uncategorized:
        shown = ", ".join(f"{n} ({money(c)})" for n, c in s.uncategorized[:5])
        more = f", and {len(s.uncategorized) - 5} more" if len(s.uncategorized) > 5 else ""
        L.append(f"- **Uncategorized services** (shown as Other; add them to the category map): {shown}{more}.")
    if s.missing_days:
        L.append(f"- **Missing days in the window:** {', '.join(str(d) for d in s.missing_days)}. Totals are understated.")
    else:
        L.append(f"- All {s.window.days} days present.")
    L.append("- Recent days can still be revised by the clouds (billing lag up to ~24h or more).")
    L.append("")
    return "\n".join(L)
