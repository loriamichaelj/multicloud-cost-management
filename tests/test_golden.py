"""Snapshot of the full report for deterministic sample data.

If a change to wording or layout is intentional, regenerate with:
    UPDATE_GOLDEN=1 PYTHONPATH=src python3 -m unittest tests.test_golden
then review the diff of tests/golden/weekly_sample.md before committing.
"""

import difflib
import os
import unittest
from datetime import date
from pathlib import Path

from costreport.analysis import analyze
from costreport.config import ReportSettings
from costreport.providers import sample
from costreport.report import render

GOLDEN = Path(__file__).parent / "golden" / "weekly_sample.md"
END = date(2026, 11, 15)


def build() -> str:
    recs = sample.generate(END, days=70, seed=42)
    s = analyze(recs, END, budgets={"aws": 125_000, "azure": 80_000, "gcp": 75_000})
    return render(s, top=10, settings=ReportSettings())


class GoldenReportTests(unittest.TestCase):
    def test_matches_snapshot(self):
        actual = build()
        if os.environ.get("UPDATE_GOLDEN"):
            GOLDEN.parent.mkdir(exist_ok=True)
            GOLDEN.write_text(actual, encoding="utf-8")
        expected = GOLDEN.read_text(encoding="utf-8")
        if actual != expected:
            diff = "\n".join(
                difflib.unified_diff(expected.splitlines(), actual.splitlines(), "golden", "actual", lineterm="", n=2)
            )
            self.fail(f"report differs from snapshot:\n{diff}")


if __name__ == "__main__":
    unittest.main()
