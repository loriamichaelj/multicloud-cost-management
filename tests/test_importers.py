"""Fixtures are shaped after vendor documentation, NOT captured from real exports."""

import contextlib
import gzip
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

from costreport import cli, storage
from costreport.importers import import_files, parse_date, resolve_columns, _tag_from_blob

CUR_LEGACY = """\
lineItem/UsageStartDate,lineItem/ProductCode,product/ProductName,lineItem/UsageAccountId,product/region,lineItem/UnblendedCost,resourceTags/user:team
2026-11-09T00:00:00Z,AmazonEC2,Amazon Elastic Compute Cloud,111,us-east-1,10.50,web
2026-11-09T01:00:00Z,AmazonEC2,Amazon Elastic Compute Cloud,111,us-east-1,4.50,web
2026-11-09T01:00:00Z,AmazonEC2,Amazon Elastic Compute Cloud,111,us-east-1,2.00,
2026-11-09T02:00:00Z,AmazonS3,Amazon Simple Storage Service,111,us-east-1,0,data
2026-11-10T00:00:00Z,AmazonS3,Amazon Simple Storage Service,111,us-east-1,3.00,data
"""

CUR_V2 = """\
line_item_usage_start_date,line_item_product_code,product_product_name,line_item_usage_account_id,product_region_code,line_item_unblended_cost,resource_tags_user_team
2026-11-09 00:00:00.000,AmazonRDS,Amazon Relational Database Service,222,eu-west-1,7.25,data
"""

AZURE_EA = '''\
Date,SubscriptionId,MeterCategory,ResourceLocation,CostInBillingCurrency,BillingCurrencyCode,Tags
11/09/2026,sub-1,Virtual Machines,eastus,20.00,USD,"""team"": ""web"",""env"": ""prod"""
11/09/2026,sub-1,Storage,eastus,5.00,USD,
11/25/2026,sub-1,Storage,eastus,1.00,USD,"{""team"":""data""}"
'''

FOCUS = """\
ChargePeriodStart,ServiceName,BilledCost,SubAccountId,RegionName,BillingCurrency,Tags
2026-11-09T00:00:00Z,Compute Engine,9.0,proj-1,us-central1,USD,"{""team"": ""ml""}"
"""

GCP_GENERIC = """\
day,service_description,cost,project_id,location_region,team
2026-11-09,BigQuery,12.0,proj-1,us,data
2026-11-09,BigQuery,3.0,proj-1,us,
"""


def tmp_file(d, name, text):
    p = Path(d) / name
    p.write_text(text, encoding="utf-8")
    return p


class ColumnResolutionTests(unittest.TestCase):
    def test_cur_variants_resolve_same_roles(self):
        a = resolve_columns(CUR_LEGACY.splitlines()[0].split(","), "team")
        b = resolve_columns(CUR_V2.splitlines()[0].split(","), "team")
        self.assertEqual(set(a), set(b))
        self.assertEqual(a["service"], "product/ProductName")  # display name beats product code
        self.assertEqual(a["tag_col"], "resourceTags/user:team")

    def test_tag_key_with_punctuation(self):
        cols = resolve_columns(["date", "service", "cost", "resourceTags/user:cost-center"], "cost-center")
        self.assertEqual(cols["tag_col"], "resourceTags/user:cost-center")


class DateParseTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_date("2026-11-09T00:00:00Z"), date(2026, 11, 9))
        self.assertEqual(parse_date("2026-11-09 00:00:00.000"), date(2026, 11, 9))
        self.assertEqual(parse_date("20261109"), date(2026, 11, 9))
        self.assertEqual(parse_date("11/09/2026"), date(2026, 11, 9))   # MM/DD
        self.assertEqual(parse_date("25/11/2026"), date(2026, 11, 25))  # only DD/MM possible
        self.assertEqual(parse_date("2026/11/09"), date(2026, 11, 9))

    def test_garbage(self):
        with self.assertRaises(ValueError):
            parse_date("last tuesday")


class TagBlobTests(unittest.TestCase):
    def test_shapes(self):
        self.assertEqual(_tag_from_blob('{"team": "web"}', "team"), "web")
        self.assertEqual(_tag_from_blob('"team": "web","env": "prod"', "team"), "web")  # Azure brace-less
        self.assertEqual(_tag_from_blob('{"user_team":"ml"}', "team"), "ml")
        self.assertEqual(_tag_from_blob('{"user:Team":"ml"}', "team"), "ml")
        self.assertEqual(_tag_from_blob('[{"key":"team","value":"data"}]', "team"), "data")
        self.assertEqual(_tag_from_blob("not json at all", "team"), "")
        self.assertEqual(_tag_from_blob("", "team"), "")
        self.assertEqual(_tag_from_blob('{"env":"prod"}', "team"), "")


class ImportTests(unittest.TestCase):
    def test_cur_aggregates_to_daily_and_skips_zero(self):
        with tempfile.TemporaryDirectory() as d:
            res = import_files([tmp_file(d, "cur.csv", CUR_LEGACY)], "aws")
        by = {(r.date, r.service, r.tags.get("team")): r.cost for r in res.records}
        self.assertAlmostEqual(by[(date(2026, 11, 9), "Amazon Elastic Compute Cloud", "web")], 15.0)  # 10.5 + 4.5
        self.assertAlmostEqual(by[(date(2026, 11, 9), "Amazon Elastic Compute Cloud", None)], 2.0)
        self.assertAlmostEqual(by[(date(2026, 11, 10), "Amazon Simple Storage Service", "data")], 3.0)
        self.assertEqual(len(res.records), 3)  # the $0 line is dropped
        self.assertEqual((res.files[0].rows_read, res.files[0].rows_skipped), (5, 1))
        self.assertTrue(all(r.provider == "aws" and r.account == "111" for r in res.records))

    def test_cur_v2(self):
        with tempfile.TemporaryDirectory() as d:
            res = import_files([tmp_file(d, "v2.csv", CUR_V2)], "aws")
        r = res.records[0]
        self.assertEqual((r.service, r.region, r.tags, r.cost), ("Amazon Relational Database Service", "eu-west-1", {"team": "data"}, 7.25))

    def test_azure_ea_us_dates_and_tags(self):
        with tempfile.TemporaryDirectory() as d:
            res = import_files([tmp_file(d, "az.csv", AZURE_EA)], "azure")
        by = {(r.date, r.service): r for r in res.records}
        self.assertEqual(by[(date(2026, 11, 9), "Virtual Machines")].tags, {"team": "web"})
        self.assertEqual(by[(date(2026, 11, 9), "Storage")].tags, {})
        self.assertEqual(by[(date(2026, 11, 25), "Storage")].tags, {"team": "data"})

    def test_focus(self):
        with tempfile.TemporaryDirectory() as d:
            res = import_files([tmp_file(d, "f.csv", FOCUS)], "gcp")
        r = res.records[0]
        self.assertEqual((r.service, r.account, r.region, r.tags), ("Compute Engine", "proj-1", "us-central1", {"team": "ml"}))

    def test_gcp_generic_with_tag_column(self):
        with tempfile.TemporaryDirectory() as d:
            res = import_files([tmp_file(d, "g.csv", GCP_GENERIC)], "gcp")
        tagged = [r for r in res.records if r.tags]
        self.assertEqual((len(res.records), len(tagged), tagged[0].tags), (2, 1, {"team": "data"}))

    def test_gzip_and_bom(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cur.csv.gz"
            with gzip.open(p, "wb") as fh:
                fh.write(b"\xef\xbb\xbf" + CUR_V2.encode())
            res = import_files([p], "aws")
        self.assertEqual(len(res.records), 1)

    def test_multiple_files_merge(self):
        with tempfile.TemporaryDirectory() as d:
            res = import_files([tmp_file(d, "a.csv", CUR_V2), tmp_file(d, "b.csv", CUR_V2)], "aws")
        self.assertEqual(len(res.records), 1)
        self.assertAlmostEqual(res.records[0].cost, 14.5)

    def test_non_usd_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = tmp_file(d, "eur.csv", AZURE_EA.replace("USD", "EUR"))
            with self.assertRaisesRegex(ValueError, "only USD"):
                import_files([p], "azure")

    def test_missing_columns_error_is_actionable(self):
        with tempfile.TemporaryDirectory() as d:
            p = tmp_file(d, "bad.csv", "foo,bar\n1,2\n")
            with self.assertRaisesRegex(ValueError, r"(?s)could not find column.*headers seen"):
                import_files([p], "aws")

    def test_bad_cost_names_file_and_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = tmp_file(d, "x.csv", "date,service,cost\n2026-11-09,A,1\n2026-11-09,B,abc\n")
            with self.assertRaisesRegex(ValueError, r"x\.csv line 3"):
                import_files([p], "aws")

    def test_thousands_separator(self):
        with tempfile.TemporaryDirectory() as d:
            p = tmp_file(d, "x.csv", 'date,service,cost\n2026-11-09,A,"1,234.50"\n')
            self.assertAlmostEqual(import_files([p], "aws").records[0].cost, 1234.5)

    def test_unknown_provider(self):
        with self.assertRaises(ValueError):
            import_files([], "oracle")


class ImportCliTests(unittest.TestCase):
    def test_import_then_report(self):
        with tempfile.TemporaryDirectory() as d:
            src = tmp_file(d, "cur.csv", CUR_LEGACY)
            out, md = Path(d) / "n.csv", Path(d) / "r.md"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(cli.main(["import", str(src), "--provider", "aws", "--out", str(out)]), 0)
                self.assertEqual(cli.main(["report", "--input", str(out), "--out", str(md)]), 0)
            self.assertIn("columns used:", buf.getvalue())
            self.assertIn("no cloud calls made", buf.getvalue())
            self.assertEqual(len(storage.read_csv(out)), 3)
            self.assertIn("Weekly Cloud Cost Report", md.read_text())

    def test_import_errors_exit_nonzero_without_output(self):
        with tempfile.TemporaryDirectory() as d:
            src = tmp_file(d, "bad.csv", "foo\n1\n")
            out = Path(d) / "n.csv"
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["import", str(src), "--provider", "aws", "--out", str(out)]), 1)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
