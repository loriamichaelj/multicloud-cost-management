import tempfile
import unittest
from pathlib import Path

from costreport import categories
from costreport.config import load_config


class CategoryTests(unittest.TestCase):
    def setUp(self):
        self.m = categories.load_default()

    def test_known_services_across_clouds(self):
        cases = {
            "Amazon Elastic Compute Cloud - Compute": "Compute",
            "Virtual Machines": "Compute",
            "Compute Engine": "Compute",
            "Amazon Simple Storage Service": "Storage",
            "Cloud Storage": "Storage",
            "Azure SQL Database": "Database",
            "Amazon Relational Database Service": "Database",
            "Azure Cosmos DB": "Database",
            "BigQuery": "Analytics",
            "Vertex AI": "AI/ML",
            "AmazonCloudWatch": "Monitoring & Security",
            "AWS Data Transfer": "Networking",
        }
        for svc, cat in cases.items():
            self.assertEqual(self.m.categorize(svc), cat, svc)

    def test_specific_beats_broad(self):
        # Containers and AI/ML are listed above Compute/Database, so they win.
        self.assertEqual(self.m.categorize("Azure Kubernetes Service"), "Containers")
        self.assertEqual(self.m.categorize("Amazon Elastic Container Service"), "Containers")
        self.assertEqual(self.m.categorize("Azure Machine Learning compute"), "AI/ML")

    def test_unknown_is_other_and_case_insensitive(self):
        self.assertEqual(self.m.categorize("Quantum Widget Service"), categories.OTHER)
        self.assertEqual(self.m.categorize("BIGQUERY"), "Analytics")

    def test_custom_map_first_match_wins(self):
        m = categories.parse('[[rule]]\ncategory="A"\nmatch=["foo"]\n[[rule]]\ncategory="B"\nmatch=["foo","bar"]\n')
        self.assertEqual(m.categorize("a FOO b"), "A")
        self.assertEqual(m.categorize("bar"), "B")

    def test_invalid_maps_rejected(self):
        for bad in [
            "",
            '[[rule]]\nmatch=["x"]\n',
            '[[rule]]\ncategory="A"\nmatch=[]\n',
            '[[rule]]\ncategory="Other"\nmatch=["x"]\n',
        ]:
            with self.assertRaises(ValueError, msg=bad):
                categories.parse(bad)


class ConfigTests(unittest.TestCase):
    def _load(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.toml"
            p.write_text(text)
            return load_config(p)

    def test_defaults_without_file(self):
        c = load_config(None)
        self.assertEqual((c.report.min_change_usd, c.report.untagged_target, c.budgets), (100.0, 0.10, {}))

    def test_full_config(self):
        c = self._load('tag_key="env"\n[report]\nmin_change_usd=250\nmin_change_pct=0.1\nuntagged_target=0.2\n'
                       '[budgets]\naws=1000\ngcp=50.5\n')
        self.assertEqual(c.tag_key, "env")
        self.assertEqual((c.report.min_change_usd, c.report.min_change_pct, c.report.untagged_target), (250, 0.1, 0.2))
        self.assertEqual(c.budgets, {"aws": 1000.0, "gcp": 50.5})

    def test_category_map_path_relative_to_config(self):
        c = self._load('[report]\ncategory_map="cats.toml"\n')
        self.assertTrue(c.report.category_map.endswith("/cats.toml"))

    def test_bad_values_rejected(self):
        for bad in [
            '[budgets]\nvmware=10\n',            # unknown provider
            '[budgets]\naws=-5\n',               # non-positive
            '[budgets]\naws="lots"\n',           # not a number
            '[report]\nuntagged_target=1.5\n',   # out of range
            '[report]\nmin_change_usd=-1\n',
        ]:
            with self.assertRaises(ValueError, msg=bad):
                self._load(bad)


if __name__ == "__main__":
    unittest.main()
