import unittest
from datetime import date, timedelta

from costreport.analysis import UNTAGGED, Window, analyze, detect_anomalies, month_to_date
from costreport.models import CostRecord

END = date(2026, 11, 15)


def rec(d, cost, service="EC2", provider="aws", tags=None):
    return CostRecord(d, provider, "acct", service, "r", cost, tags or {})


def flat(days, per_day, **kw):
    return [rec(END - timedelta(days=i), per_day, **kw) for i in range(days)]


class WindowTests(unittest.TestCase):
    def test_prior_window_is_adjacent_and_same_length(self):
        w = Window(date(2026, 11, 9), date(2026, 11, 15))
        p = w.prior()
        self.assertEqual((p.start, p.end), (date(2026, 11, 2), date(2026, 11, 8)))
        self.assertEqual(w.days, p.days)


class AnalyzeTests(unittest.TestCase):
    def test_totals_and_wow(self):
        recs = [rec(END - timedelta(days=i), 100.0) for i in range(7)]  # this week 700
        recs += [rec(END - timedelta(days=7 + i), 50.0) for i in range(7)]  # prior 350
        s = analyze(recs, END)
        self.assertAlmostEqual(s.total, 700)
        self.assertAlmostEqual(s.prior_total, 350)
        self.assertAlmostEqual(s.delta, 350)
        self.assertAlmostEqual(s.pct, 1.0)

    def test_service_drivers_sorted_and_shares(self):
        recs = flat(7, 30.0, service="A") + flat(7, 10.0, service="B")
        s = analyze(recs, END)
        self.assertEqual([d.name for d in s.services], ["AWS · A", "AWS · B"])
        self.assertAlmostEqual(s.services[0].share, 0.75)
        self.assertIsNone(s.services[0].pct)  # no prior week data -> "new"

    def test_tag_breakdown_includes_untagged(self):
        recs = flat(7, 60.0, tags={"team": "web"}) + flat(7, 40.0, service="S3")
        s = analyze(recs, END)
        by = {d.name: d.current for d in s.tags}
        self.assertAlmostEqual(by["web"], 420)
        self.assertAlmostEqual(by[UNTAGGED], 280)
        self.assertAlmostEqual(s.untagged_share, 0.4)

    def test_empty_tag_value_counts_as_untagged(self):
        s = analyze(flat(7, 10.0, tags={"team": ""}), END)
        self.assertAlmostEqual(s.untagged_share, 1.0)

    def test_increases_and_decreases_ranked(self):
        recs = []
        for i in range(7):
            recs += [rec(END - timedelta(days=i), 20.0, "up"), rec(END - timedelta(days=7 + i), 10.0, "up")]
            recs += [rec(END - timedelta(days=i), 5.0, "down"), rec(END - timedelta(days=7 + i), 15.0, "down")]
        s = analyze(recs, END)
        self.assertEqual(s.increases[0].name, "AWS · up")
        self.assertEqual(s.decreases[0].name, "AWS · down")

    def test_missing_days_reported(self):
        recs = [r for r in flat(7, 10.0) if r.date != date(2026, 11, 12)]
        s = analyze(recs, END)
        self.assertEqual(s.missing_days, [date(2026, 11, 12)])

    def test_default_end_is_latest_date(self):
        self.assertEqual(analyze(flat(7, 1.0)).window.end, END)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            analyze([])


class AnomalyTests(unittest.TestCase):
    def test_spike_flagged_once(self):
        recs = flat(28, 100.0)
        spike_day = END - timedelta(days=2)
        recs = [rec(r.date, 1000.0) if r.date == spike_day else r for r in recs]
        found = detect_anomalies(recs, Window(END - timedelta(days=6), END))
        self.assertEqual([a.day for a in found], [spike_day])
        self.assertAlmostEqual(found[0].baseline, 100.0)

    def test_flat_series_no_anomaly(self):
        self.assertEqual(detect_anomalies(flat(28, 100.0), Window(END - timedelta(days=6), END)), [])

    def test_small_absolute_spike_ignored(self):
        recs = flat(28, 1.0)
        recs = [rec(r.date, 20.0) if r.date == END else r for r in recs]  # 20x but only $19
        self.assertEqual(detect_anomalies(recs, Window(END - timedelta(days=6), END), min_abs=50), [])

    def test_insufficient_history_ignored(self):
        recs = flat(3, 100.0)
        recs[0] = rec(END, 5000.0)
        self.assertEqual(detect_anomalies(recs, Window(END - timedelta(days=6), END)), [])


class MtdTests(unittest.TestCase):
    def test_projection(self):
        recs = [rec(date(2026, 11, d), 100.0) for d in range(1, 11)]
        m = month_to_date(recs, date(2026, 11, 10))
        self.assertAlmostEqual(m.spend, 1000)
        self.assertAlmostEqual(m.projected, 3000)  # 100/day * 30

    def test_partial_month_data_uses_first_data_day(self):
        recs = [rec(date(2026, 11, d), 100.0) for d in range(6, 11)]  # data starts the 6th
        m = month_to_date(recs, date(2026, 11, 10))
        self.assertEqual(m.days_elapsed, 5)
        self.assertAlmostEqual(m.projected, 3000)


if __name__ == "__main__":
    unittest.main()
