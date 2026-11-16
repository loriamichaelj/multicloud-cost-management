import unittest
from datetime import date, timedelta

from costreport.analysis import analyze
from costreport.config import ReportSettings
from costreport.models import CostRecord
from costreport.report import is_material, render
from costreport.analysis import Driver

END = date(2026, 11, 15)


def rec(d, cost, service="EC2", provider="aws", tags=None):
    return CostRecord(d, provider, "a", service, "r", cost, tags or {})


def week(cost_per_day, service, provider="aws", prior=False, tags=None):
    off = 7 if prior else 0
    return [rec(END - timedelta(days=off + i), cost_per_day, service, provider, tags) for i in range(7)]


class MaterialityTests(unittest.TestCase):
    cfg = ReportSettings(min_change_usd=100, min_change_pct=0.05)

    def test_needs_both_floors(self):
        self.assertFalse(is_material(Driver("x", 1040, 1000, 0.5), self.cfg))   # $40: under $ floor
        self.assertFalse(is_material(Driver("x", 10_100, 10_000, 0.5), self.cfg))  # 1%: under % floor
        self.assertTrue(is_material(Driver("x", 1200, 1000, 0.5), self.cfg))     # $200, 20%
        self.assertTrue(is_material(Driver("x", 800, 1000, 0.5), self.cfg))      # decreases count too

    def test_new_spend_exempt_from_pct_floor(self):
        self.assertTrue(is_material(Driver("x", 150, 0, 0.5), self.cfg))
        self.assertFalse(is_material(Driver("x", 50, 0, 0.5), self.cfg))

    def test_headline_skips_immaterial_saving(self):
        recs = week(100, "Big") + week(101, "Big", prior=True) + week(10, "Tiny") + week(12, "Tiny", prior=True)
        md = render(analyze(recs, END), settings=self.cfg)
        self.assertNotIn("Biggest saving", md)
        self.assertIn("No service changed by more than", md)

    def test_material_increase_called_out(self):
        recs = week(300, "Hot") + week(100, "Hot", prior=True)
        md = render(analyze(recs, END), settings=self.cfg)
        self.assertIn("Biggest increase: **AWS · Hot**", md)

    def test_thresholds_are_configurable(self):
        recs = week(100, "S") + week(98, "S", prior=True)  # +$14 / +2%
        loose = ReportSettings(min_change_usd=1, min_change_pct=0.0)
        self.assertIn("Biggest increase", render(analyze(recs, END), settings=loose))


class UntaggedTargetTests(unittest.TestCase):
    def test_target_drives_warning(self):
        recs = week(80, "S", tags={"team": "x"}) + week(20, "S")  # 20% untagged
        s = analyze(recs, END)
        self.assertIn("above the 10% target", render(s, settings=ReportSettings(untagged_target=0.10)))
        self.assertNotIn("above the", render(s, settings=ReportSettings(untagged_target=0.30)))


class CategoryReportTests(unittest.TestCase):
    def test_category_totals_and_section(self):
        recs = week(100, "Virtual Machines", "azure") + week(50, "Compute Engine", "gcp") + week(30, "Cloud Storage", "gcp")
        s = analyze(recs, END)
        by = {d.name: d.current for d in s.categories}
        self.assertAlmostEqual(by["Compute"], 1050)
        self.assertAlmostEqual(by["Storage"], 210)
        self.assertIn("## Spend by category", render(s))

    def test_uncategorized_listed(self):
        recs = week(100, "Quantum Widget") + week(10, "Compute Engine", "gcp")
        s = analyze(recs, END)
        self.assertEqual(s.uncategorized[0][0], "AWS · Quantum Widget")
        self.assertIn("Uncategorized services", render(s))
        self.assertNotIn("Uncategorized", render(analyze(week(10, "Compute Engine", "gcp"), END)))


class BudgetTests(unittest.TestCase):
    def _recs(self):
        # 10 days of data in November, $100/day on aws, $10/day on gcp
        return [rec(date(2026, 11, d), 100) for d in range(1, 11)] + [
            rec(date(2026, 11, d), 10, "Compute Engine", "gcp") for d in range(1, 11)
        ]

    def test_projection_and_status(self):
        s = analyze(self._recs(), date(2026, 11, 10), budgets={"aws": 2500, "gcp": 400})
        by = {b.provider: b for b in s.budgets}
        self.assertAlmostEqual(by["aws"].spend, 1000)
        self.assertAlmostEqual(by["aws"].projected, 3000)  # 100/day * 30
        self.assertTrue(by["aws"].over)
        self.assertFalse(by["gcp"].over)  # 300 projected vs 400

    def test_report_flags_over_budget(self):
        s = analyze(self._recs(), date(2026, 11, 10), budgets={"aws": 2500, "gcp": 400})
        md = render(s)
        self.assertIn("AWS is projected $500 over budget", md)
        self.assertIn("## Budget", md)
        self.assertIn("**Over**", md)

    def test_all_on_track_message(self):
        md = render(analyze(self._recs(), date(2026, 11, 10), budgets={"aws": 5000, "gcp": 5000}))
        self.assertIn("All budgeted clouds are projected to stay within", md)

    def test_no_budgets_no_section(self):
        md = render(analyze(self._recs(), date(2026, 11, 10)))
        self.assertNotIn("## Budget", md)


if __name__ == "__main__":
    unittest.main()
