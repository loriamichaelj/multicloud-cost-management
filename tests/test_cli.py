import contextlib
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from costreport import cli, storage
from costreport.providers import sample
from costreport.providers.aws import AwsProvider

END = date(2026, 11, 15)


class SampleTests(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(sample.generate(END, 21, seed=1), sample.generate(END, 21, seed=1))

    def test_covers_all_clouds_and_untagged(self):
        recs = sample.generate(END, 14)
        self.assertEqual({r.provider for r in recs}, {"aws", "azure", "gcp"})
        self.assertTrue(any(not r.tags for r in recs))
        self.assertEqual(max(r.date for r in recs), END)


class StorageTests(unittest.TestCase):
    def test_roundtrip(self):
        recs = sample.generate(END, 10)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.csv"
            storage.write_csv(p, recs)
            back = storage.read_csv(p)
        self.assertEqual(len(back), len(recs))
        self.assertEqual(back[0].tags, recs[0].tags)
        self.assertAlmostEqual(sum(r.cost for r in back), sum(r.cost for r in recs), places=3)

    def test_missing_column(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.csv"
            p.write_text("date,cost\n2026-01-01,1\n")
            with self.assertRaises(ValueError):
                storage.read_csv(p)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def test_end_to_end_report(self):
        with tempfile.TemporaryDirectory() as d:
            csv_path, md_path = Path(d) / "s.csv", Path(d) / "r.md"
            self.assertEqual(cli.main(["sample", "--out", str(csv_path), "--end-date", "2026-11-15"]), 0)
            self.assertEqual(cli.main(["report", "--input", str(csv_path), "--out", str(md_path)]), 0)
            md = md_path.read_text()
        for needle in ["# Weekly Cloud Cost Report: 2026-11-09 to 2026-11-15", "## Headlines",
                       "GCP · BigQuery", "(untagged)", "## Anomalies", "## Data quality"]:
            self.assertIn(needle, md)
        self.assertNotIn("Claude", md)

    def test_report_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.csv"
            cli.main(["sample", "--out", str(p), "--end-date", "2026-11-15"])
            outs = []
            for _ in range(2):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    cli.main(["report", "--input", str(p)])
                outs.append(buf.getvalue())
        self.assertEqual(outs[0], outs[1])

    def test_fetch_without_live_never_calls_cloud(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "c.toml"
            cfg.write_text('tag_key = "team"\n')
            with mock.patch.object(AwsProvider, "fetch", side_effect=AssertionError("network!")) as f:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = cli.main(["fetch", "aws", "--start", "2026-11-01", "--end", "2026-11-08", "--config", str(cfg)])
            f.assert_not_called()
        self.assertEqual(rc, 0)
        self.assertIn("DRY RUN", buf.getvalue())

    def test_live_requires_out(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "c.toml"
            cfg.write_text('tag_key = "team"\n')
            with mock.patch.object(AwsProvider, "fetch", side_effect=AssertionError("network!")):
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                    cli.main(["fetch", "aws", "--start", "2026-11-01", "--end", "2026-11-08",
                              "--config", str(cfg), "--live"])

    def test_missing_config_is_clean_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            rc = cli.main(["fetch", "aws", "--start", "2026-11-01", "--end", "2026-11-08", "--config", "/nope.toml"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
