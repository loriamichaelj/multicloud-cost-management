import contextlib
import io
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from costreport import cli, delivery, storage
from costreport.config import load_config
from costreport.providers import sample

END = date(2026, 11, 15)


class FakeProvider:
    """Stands in for a cloud provider. Records calls; returns synthetic rows for its own cloud."""

    def __init__(self, name):
        self.name = name
        self.fetch_calls = []

    def plan(self, start, end):
        return f"PLAN {self.name} {start} -> {end}"

    def fetch(self, start, end):
        self.fetch_calls.append((start, end))
        return [r for r in sample.generate(END, 70) if r.provider == self.name and start <= r.date < end]


CONFIG = """
[delivery]
email_from = "r@example.com"
email_to = ["a@example.com"]
smtp_host = "smtp.example.com"
"""


class WeeklyTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = self.tmp / "c.toml"
        self.cfg.write_text(CONFIG)
        self.fakes = {}

        def make(name, cfg):
            return self.fakes.setdefault(name, FakeProvider(name))

        self.enterContext(mock.patch.object(cli, "_provider", side_effect=make))
        self.enterContext(mock.patch.dict(os.environ, {}, clear=False))

    def run_weekly(self, *extra, expect=0):
        argv = ["weekly", "--providers", "aws", "gcp", "--config", str(self.cfg), "--end-date", "2026-11-15",
                "--data-dir", str(self.tmp / "data"), "--reports-dir", str(self.tmp / "reports"), *extra]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = cli.main(argv)
        self.assertEqual(rc, expect)
        return buf.getvalue()

    def test_dry_run_fetches_nothing_and_writes_nothing(self):
        out = self.run_weekly()
        self.assertIn("DRY RUN", out)
        self.assertIn("PLAN aws", out)
        self.assertIn("PLAN gcp", out)
        self.assertTrue(all(f.fetch_calls == [] for f in self.fakes.values()))
        self.assertFalse((self.tmp / "data").exists() or (self.tmp / "reports").exists())

    def test_live_fetches_window_writes_csvs_and_archives_by_iso_week(self):
        self.run_weekly("--live")
        for name in ("aws", "gcp"):
            start, end = self.fakes[name].fetch_calls[0]
            self.assertEqual(end, END + timedelta(days=1))              # exclusive end
            self.assertEqual((end - start).days, cli.LOOKBACK_DAYS)
            rows = storage.read_csv(self.tmp / "data" / f"{name}.csv")
            self.assertTrue(rows and {r.provider for r in rows} == {name})
        y, w, _ = END.isocalendar()
        md = (self.tmp / "reports" / f"{y}-W{w:02d}.md").read_text()
        self.assertIn("2026-11-09 to 2026-11-15", md)
        self.assertIn("AWS", md)
        self.assertIn("GCP", md)

    def test_default_end_date_respects_billing_lag(self):
        # With no --end-date the dry-run plan ends (exclusive) the day after today minus the lag.
        expected_end = date.today() - timedelta(days=cli.BILLING_LAG_DAYS) + timedelta(days=1)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(["weekly", "--providers", "aws", "--config", str(self.cfg)])
        self.assertIn(f"-> {expected_end}", buf.getvalue())

    def test_send_requires_live_and_deliver(self):
        for extra in (["--deliver", "slack", "--send"], ["--live", "--send"]):
            with self.assertRaises(SystemExit):
                self.run_weekly(*extra)

    def test_delivery_misconfig_caught_before_paid_fetch(self):
        self.cfg.write_text("")  # no [delivery] -> email impossible
        self.run_weekly("--live", "--deliver", "email", expect=1)
        self.assertTrue(all(f.fetch_calls == [] for f in self.fakes.values()))

    def test_missing_slack_env_caught_before_paid_fetch_when_sending(self):
        os.environ.pop(delivery.ENV_SLACK_WEBHOOK, None)
        self.run_weekly("--live", "--deliver", "slack", "--send", expect=1)
        self.assertTrue(all(f.fetch_calls == [] for f in self.fakes.values()))

    def test_live_with_send_delivers_once_each(self):
        os.environ[delivery.ENV_SLACK_WEBHOOK] = "https://hooks.slack.com/services/T/B/X"
        with mock.patch.object(delivery, "post_slack") as slack, mock.patch.object(delivery, "send_email") as email:
            out = self.run_weekly("--live", "--deliver", "slack", "email", "--send")
        slack.assert_called_once()
        email.assert_called_once()
        self.assertIn("SENT", out)
        self.assertNotIn("hooks.slack.com/services", out)

    def test_live_without_send_only_previews(self):
        with mock.patch.object(delivery, "post_slack") as slack, mock.patch.object(delivery, "send_email") as email:
            out = self.run_weekly("--live", "--deliver", "slack", "email")
        slack.assert_not_called()
        email.assert_not_called()
        self.assertIn("PREVIEW, not sent", out)


class ReportDeliverCliTests(unittest.TestCase):
    def test_report_send_without_deliver_exits(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.csv"
            storage.write_csv(p, sample.generate(END, 30))
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cli.main(["report", "--input", str(p), "--send"])

    def test_report_deliver_preview_default(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.csv"
            storage.write_csv(p, sample.generate(END, 30))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), mock.patch.object(delivery, "post_slack") as slack:
                rc = cli.main(["report", "--input", str(p), "--out", str(Path(d) / "r.md"), "--deliver", "slack"])
            self.assertEqual(rc, 0)
            slack.assert_not_called()
            self.assertIn("PREVIEW, not sent", buf.getvalue())


class DeliveryConfigTests(unittest.TestCase):
    def _load(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.toml"
            p.write_text(text)
            return load_config(p)

    def test_parsed(self):
        c = self._load(CONFIG + 'smtp_port = 465\nslack_top = 3\n')
        self.assertEqual((c.delivery.smtp_host, c.delivery.smtp_port, c.delivery.slack_top), ("smtp.example.com", 465, 3))
        self.assertEqual(c.delivery.email_to, ["a@example.com"])

    def test_defaults(self):
        c = self._load("")
        self.assertEqual((c.delivery.smtp_port, c.delivery.slack_top, c.delivery.email_to), (587, 5, []))

    def test_invalid(self):
        for bad in ['[delivery]\nemail_to = "a@b.com"\n', '[delivery]\nemail_to = ["nope"]\n',
                    '[delivery]\nemail_from = "nope"\n', '[delivery]\nsmtp_port = 0\n', '[delivery]\nslack_top = 99\n']:
            with self.assertRaises(ValueError, msg=bad):
                self._load(bad)


if __name__ == "__main__":
    unittest.main()
