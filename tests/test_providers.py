import unittest
from datetime import date

from costreport.providers import aws, azure, gcp
from costreport.providers.base import LiveAccessError


class AwsTests(unittest.TestCase):
    PAGE = {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": "2026-11-09", "End": "2026-11-10"},
                "Groups": [
                    {"Keys": ["Amazon EC2", "team$web"], "Metrics": {"UnblendedCost": {"Amount": "12.5", "Unit": "USD"}}},
                    {"Keys": ["Amazon S3", "team$"], "Metrics": {"UnblendedCost": {"Amount": "3.0", "Unit": "USD"}}},
                ],
            }
        ]
    }

    def test_request_shape(self):
        r = aws.build_request(date(2026, 11, 1), date(2026, 11, 8), "team")
        self.assertEqual(r["TimePeriod"], {"Start": "2026-11-01", "End": "2026-11-08"})
        self.assertEqual(r["GroupBy"][1], {"Type": "TAG", "Key": "team"})
        self.assertEqual(r["Granularity"], "DAILY")

    def test_parse_tagged_and_untagged(self):
        recs = aws.parse_pages([self.PAGE], "team")
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0].tags, {"team": "web"})
        self.assertEqual(recs[1].tags, {})
        self.assertEqual(recs[0].date, date(2026, 11, 9))
        self.assertEqual(recs[0].cost, 12.5)

    def test_non_usd_rejected(self):
        page = {"ResultsByTime": [{"TimePeriod": {"Start": "2026-11-09"}, "Groups": [
            {"Keys": ["X", "team$"], "Metrics": {"UnblendedCost": {"Amount": "1", "Unit": "EUR"}}}]}]}
        with self.assertRaises(LiveAccessError):
            aws.parse_pages([page], "team")

    def test_plan_mentions_cost(self):
        self.assertIn("$0.01", aws.AwsProvider("team").plan(date(2026, 11, 1), date(2026, 11, 8)))


class AzureTests(unittest.TestCase):
    COLS = [{"name": n} for n in ["Cost", "UsageDate", "ServiceName", "TagKey", "TagValue", "Currency"]]

    def test_body_end_is_inclusive_shifted(self):
        b = azure.build_body(date(2026, 11, 1), date(2026, 11, 8), "team")
        self.assertTrue(b["timePeriod"]["to"].startswith("2026-11-07"))
        self.assertEqual(b["dataset"]["grouping"][1], {"type": "TagKey", "name": "team"})

    def test_parse_rows_by_column_name(self):
        rows = [[10.0, 20261109, "Virtual Machines", "team", "web", "USD"],
                [2.0, 20261109, "Storage", "team", "", "USD"]]
        recs = azure.parse_rows(self.COLS, rows, "team")
        self.assertEqual(recs[0].date, date(2026, 11, 9))
        self.assertEqual(recs[0].tags, {"team": "web"})
        self.assertEqual(recs[1].tags, {})

    def test_column_order_does_not_matter(self):
        cols = [{"name": n} for n in ["ServiceName", "Currency", "Cost", "UsageDate", "TagValue"]]
        recs = azure.parse_rows(cols, [["SQL", "USD", 7.0, 20261110, "data"]], "team")
        self.assertEqual((recs[0].service, recs[0].cost, recs[0].tags), ("SQL", 7.0, {"team": "data"}))

    def test_non_usd_rejected(self):
        with self.assertRaises(LiveAccessError):
            azure.parse_rows(self.COLS, [[1.0, 20261109, "VM", "team", "x", "EUR"]], "team")

    def test_unexpected_columns(self):
        with self.assertRaises(LiveAccessError):
            azure.parse_rows([{"name": "Foo"}], [[1]], "team")


class GcpTests(unittest.TestCase):
    TABLE = "proj.billing.gcp_billing_export_v1_ABC"

    def test_query_parameterized_and_nets_credits(self):
        q = gcp.build_query(self.TABLE)
        self.assertIn(f"`{self.TABLE}`", q)
        self.assertIn("@tag_key", q)
        self.assertIn("credits", q)

    def test_hostile_table_rejected(self):
        for bad in ["a.b.c`; DROP TABLE x; --", "only.two", "a b.c.d", "a.b.c.d"]:
            with self.assertRaises(LiveAccessError, msg=bad):
                gcp.build_query(bad)

    def test_hostile_tag_key_rejected(self):
        with self.assertRaises(LiveAccessError):
            gcp.GcpProvider(self.TABLE, "x' OR 1=1")

    def test_budget_guard(self):
        gcp.check_budget(100, 1000)
        with self.assertRaises(LiveAccessError):
            gcp.check_budget(1001, 1000)

    def test_row_conversion(self):
        row = {"day": date(2026, 11, 9), "account": "p", "service": "BigQuery", "region": "us",
               "tag_value": "data", "currency": "USD", "cost": 4.2}
        r = gcp.row_to_record(row, "team")
        self.assertEqual((r.provider, r.tags, r.cost), ("gcp", {"team": "data"}, 4.2))
        with self.assertRaises(LiveAccessError):
            gcp.row_to_record(dict(row, currency="JPY"), "team")


if __name__ == "__main__":
    unittest.main()
