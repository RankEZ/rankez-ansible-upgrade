"""Unit tests for scripts/detect_installed.py (pure parsing)."""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import detect_installed as D  # noqa: E402


class TestParseDocker(unittest.TestCase):
    def test_extracts_component_and_version(self):
        text = "vault rankez/pam-vault:5.8.0\npac rankez/pam-pac:5.8.0\n"
        self.assertEqual(D.parse_docker(text), {"vault": "5.8.0", "pac": "5.8.0"})

    def test_ignores_unrelated_containers(self):
        text = "dr-manager rankez/dr-manager:1.0.0\nvault rankez/pam-vault:5.8.0\n"
        self.assertEqual(D.parse_docker(text), {"vault": "5.8.0"})


class TestMerge(unittest.TestCase):
    def test_uniform_across_hosts(self):
        hosts = {
            "a": {"docker": "vault x/pam-vault:5.8.0"},
            "b": {"docker": "vault x/pam-vault:5.8.0"},
        }
        installed, conflicts = D.merge(hosts)
        self.assertEqual(installed, {"vault": "5.8.0"})
        self.assertEqual(conflicts, {})

    def test_conflicting_versions(self):
        hosts = {
            "a": {"docker": "vault x/pam-vault:5.8.0"},
            "b": {"docker": "vault x/pam-vault:5.9.0"},
        }
        installed, conflicts = D.merge(hosts)
        self.assertEqual(installed, {})
        self.assertEqual(conflicts, {"vault": ["5.8.0", "5.9.0"]})


class TestOverallVersion(unittest.TestCase):
    def test_all_agree(self):
        self.assertEqual(D.overall_version({"vault": "5.8.0", "pac": "5.8.0"}, {}), "5.8.0")

    def test_components_differ(self):
        self.assertIsNone(D.overall_version({"vault": "5.8.0", "pac": "5.7.0"}, {}))

    def test_conflicts_block(self):
        self.assertIsNone(D.overall_version({"pac": "5.8.0"}, {"vault": ["5.8.0", "5.9.0"]}))

    def test_nothing_detected(self):
        self.assertIsNone(D.overall_version({}, {}))


if __name__ == "__main__":
    unittest.main()