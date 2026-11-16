"""Pure analysis over normalized records. No I/O."""

from __future__ import annotations

import calendar
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Hashable, Iterable, Mapping

from .categories import OTHER, CategoryMap, load_default
from .models import PROVIDER_LABELS, CostRecord

UNTAGGED = "(untagged)"
WINDOW_DAYS = 7
ANOMALY_LOOKBACK = 14
ANOMALY_MIN_HISTORY = 7
ANOMALY_Z = 3.0


@dataclass(frozen=True)
class Window:
    start: date
    end: date  # inclusive

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def prior(self) -> "Window":
        return Window(self.start - timedelta(days=self.days), self.start - timedelta(days=1))

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end

    def dates(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range(self.days)]


@dataclass(frozen=True)
class Driver:
    name: str
    current: float
    previous: float
    share: float  # of current-window total

    @property
    def delta(self) -> float:
        return self.current - self.previous

    @property
    def pct(self) -> float | None:
        return None if self.previous <= 0 else self.delta / self.previous


@dataclass(frozen=True)
class Anomaly:
    day: date
    provider: str
    service: str
    cost: float
    baseline: float

    @property
    def excess(self) -> float:
        return self.cost - self.baseline


@dataclass(frozen=True)
class MonthToDate:
    month_start: date
    through: date
    spend: float
    projected: float
    days_elapsed: int
    days_in_month: int


@dataclass(frozen=True)
class BudgetStatus:
    provider: str
    budget: float
    spend: float       # month to date
    projected: float   # naive linear projection to month end

    @property
    def used(self) -> float:
        return self.spend / self.budget

    @property
    def over(self) -> bool:
        return self.projected > self.budget


@dataclass
class Summary:
    window: Window
    prior: Window
    tag_key: str
    total: float
    prior_total: float
    providers: list[Driver] = field(default_factory=list)
    services: list[Driver] = field(default_factory=list)  # all, sorted by current desc
    tags: list[Driver] = field(default_factory=list)
    increases: list[Driver] = field(default_factory=list)
    decreases: list[Driver] = field(default_factory=list)
    anomalies: list[Anomaly] = field(default_factory=list)
    mtd: MonthToDate | None = None
    categories: list[Driver] = field(default_factory=list)
    uncategorized: list[tuple[str, float]] = field(default_factory=list)  # (service label, current $)
    budgets: list[BudgetStatus] = field(default_factory=list)
    untagged_cost: float = 0.0
    missing_days: list[date] = field(default_factory=list)

    @property
    def delta(self) -> float:
        return self.total - self.prior_total

    @property
    def pct(self) -> float | None:
        return None if self.prior_total <= 0 else self.delta / self.prior_total

    @property
    def untagged_share(self) -> float:
        return self.untagged_cost / self.total if self.total > 0 else 0.0


def _sum_by(records: Iterable[CostRecord], key: Callable[[CostRecord], Hashable]) -> dict:
    out: dict = defaultdict(float)
    for r in records:
        out[key(r)] += r.cost
    return out


def build_drivers(cur: dict, prev: dict, label: Callable[[Hashable], str] = str) -> list[Driver]:
    total = sum(cur.values())
    drivers = [
        Driver(label(k), cur.get(k, 0.0), prev.get(k, 0.0), (cur.get(k, 0.0) / total) if total > 0 else 0.0)
        for k in set(cur) | set(prev)
    ]
    drivers.sort(key=lambda d: (-d.current, d.name))
    return drivers


def detect_anomalies(
    records: list[CostRecord], window: Window, min_abs: float = 50.0
) -> list[Anomaly]:
    """Flag days in `window` far above the trailing 14-day baseline of the same (provider, service)."""
    all_dates = sorted({r.date for r in records})
    date_set = set(all_dates)
    daily: dict[tuple[str, str], dict[date, float]] = defaultdict(lambda: defaultdict(float))
    for r in records:
        daily[(r.provider, r.service)][r.date] += r.cost

    found: list[Anomaly] = []
    for (provider, service), series in daily.items():
        for d in window.dates():
            if d not in date_set:
                continue
            hist_days = [
                d - timedelta(days=k)
                for k in range(1, ANOMALY_LOOKBACK + 1)
                if (d - timedelta(days=k)) in date_set
            ]
            if len(hist_days) < ANOMALY_MIN_HISTORY:
                continue
            hist = [series.get(h, 0.0) for h in hist_days]
            mean = statistics.fmean(hist)
            sigma = max(statistics.pstdev(hist), 0.10 * mean, 1.0)
            cost = series.get(d, 0.0)
            if (cost - mean) / sigma >= ANOMALY_Z and cost - mean >= min_abs:
                found.append(Anomaly(d, provider, service, cost, mean))
    found.sort(key=lambda a: (-a.excess, a.day))
    return found


def month_to_date(records: list[CostRecord], through: date) -> MonthToDate | None:
    month_start = through.replace(day=1)
    in_month = [r for r in records if month_start <= r.date <= through]
    if not in_month:
        return None
    first_data = min(r.date for r in in_month)
    elapsed = (through - first_data).days + 1
    dim = calendar.monthrange(through.year, through.month)[1]
    spend = sum(r.cost for r in in_month)
    return MonthToDate(month_start, through, spend, spend / elapsed * dim, elapsed, dim)


def budget_status(
    records: list[CostRecord], mtd: MonthToDate | None, budgets: Mapping[str, float]
) -> list[BudgetStatus]:
    if not mtd or not budgets:
        return []
    spend: dict[str, float] = defaultdict(float)
    for r in records:
        if mtd.month_start <= r.date <= mtd.through:
            spend[r.provider] += r.cost
    scale = mtd.days_in_month / mtd.days_elapsed
    return [
        BudgetStatus(p, budgets[p], spend[p], spend[p] * scale)
        for p in sorted(budgets)
    ]


def analyze(
    records: list[CostRecord],
    end: date | None = None,
    tag_key: str = "team",
    min_anomaly: float = 50.0,
    window_days: int = WINDOW_DAYS,
    category_map: CategoryMap | None = None,
    budgets: Mapping[str, float] | None = None,
) -> Summary:
    if not records:
        raise ValueError("no cost records to analyze")
    end = end or max(r.date for r in records)
    window = Window(end - timedelta(days=window_days - 1), end)
    prior = window.prior()
    cur = [r for r in records if window.contains(r.date)]
    prev = [r for r in records if prior.contains(r.date)]

    def tag_of(r: CostRecord) -> str:
        return r.tags.get(tag_key) or UNTAGGED

    svc_label = lambda k: f"{PROVIDER_LABELS.get(k[0], k[0])} · {k[1]}"
    services = build_drivers(
        _sum_by(cur, lambda r: (r.provider, r.service)),
        _sum_by(prev, lambda r: (r.provider, r.service)),
        svc_label,
    )
    movers = [d for d in services if d.delta != 0]
    total = sum(r.cost for r in cur)
    present = {r.date for r in cur}
    tags = build_drivers(_sum_by(cur, tag_of), _sum_by(prev, tag_of))

    cmap = category_map or load_default()
    cat_cache: dict[str, str] = {}

    def category_of(r: CostRecord) -> str:
        if r.service not in cat_cache:
            cat_cache[r.service] = cmap.categorize(r.service)
        return cat_cache[r.service]

    svc_cur = _sum_by(cur, lambda r: (r.provider, r.service))
    uncategorized = sorted(
        ((svc_label(k), v) for k, v in svc_cur.items() if cmap.categorize(k[1]) == OTHER),
        key=lambda t: (-t[1], t[0]),
    )
    mtd = month_to_date(records, end)

    return Summary(
        window=window,
        prior=prior,
        tag_key=tag_key,
        total=total,
        prior_total=sum(r.cost for r in prev),
        providers=build_drivers(
            _sum_by(cur, lambda r: r.provider),
            _sum_by(prev, lambda r: r.provider),
            lambda k: PROVIDER_LABELS.get(k, k),
        ),
        services=services,
        tags=tags,
        increases=sorted((d for d in movers if d.delta > 0), key=lambda d: -d.delta),
        decreases=sorted((d for d in movers if d.delta < 0), key=lambda d: d.delta),
        anomalies=detect_anomalies(records, window, min_anomaly),
        mtd=mtd,
        categories=build_drivers(_sum_by(cur, category_of), _sum_by(prev, category_of)),
        uncategorized=uncategorized,
        budgets=budget_status(records, mtd, budgets or {}),
        untagged_cost=sum(r.cost for r in cur if not r.tags.get(tag_key)),
        missing_days=[d for d in window.dates() if d not in present],
    )
