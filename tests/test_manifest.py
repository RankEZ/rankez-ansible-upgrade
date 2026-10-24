"""Unit tests for scripts/lib/manifest.py.

Run:  python -m unittest discover -s tests -v
(or:  python -m pytest tests  if pytest is installed)
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts" / "lib"))

import manifest  # noqa: E402

FIXTURE = HERE / "fixtures" / "manifest.json"


def _load():
    return manifest.load(str(FIXTURE))


class TestLatest(unittest.TestCase):
    def test_latest_overall(self):
        self.assertEqual(manifest.latest(_load()), "5.10.0")

    def test_latest_by_major(self):
        m = _load()
        self.assertEqual(manifest.latest(m, "5"), "5.10.0")
        self.assertEqual(manifest.latest(m, "4"), "4.12.0")

    def test_latest_unknown_major(self):
        self.assertIsNone(manifest.latest(_load(), "3"))

    def test_versions_sorted_desc(self):
        self.assertEqual(manifest.versions(_load())[0], "5.10.0")


class TestComponents(unittest.TestCase):
    def test_components_merges_extra(self):
        comps = manifest.components(_load(), "5.10.0")
        self.assertEqual(comps["vault"], "5.10.0")
        self.assertEqual(comps["remoteapp"], "5.9.0")  # from extra

    def test_tarball_version_exact(self):
        self.assertEqual(manifest.tarball_version(_load(), "5.9.0", "cp"), "5.9.0")

    def test_tarball_version_missing_no_fallback(self):
        self.assertIsNone(manifest.tarball_version(_load(), "5.10.0", "cp"))

    def test_tarball_version_missing_with_fallback(self):
        self.assertEqual(
            manifest.tarball_version(_load(), "5.10.0", "cp", fallback="5.10.0"),
            "5.10.0",
        )

    def test_unknown_version_empty(self):
        self.assertEqual(manifest.components(_load(), "9.9.9"), {})


class TestUpgradePath(unittest.TestCase):
    def test_can_upgrade_direct(self):
        m = _load()
        self.assertTrue(manifest.can_upgrade(m, "5.9.0", "5.10.0"))
        self.assertTrue(manifest.can_upgrade(m, "5.8.0", "5.10.0"))

    def test_can_upgrade_self(self):
        self.assertTrue(manifest.can_upgrade(_load(), "5.10.0", "5.10.0"))

    def test_can_upgrade_backward_false(self):
        self.assertFalse(manifest.can_upgrade(_load(), "5.10.0", "5.9.0"))

    def test_list_targets(self):
        targets = manifest.list_targets(_load(), "5.6.0")
        self.assertIn("5.9.0", targets)
        self.assertIn("5.10.0", targets)
        self.assertNotIn("5.6.0", targets)

    def test_upgrade_path_direct(self):
        self.assertEqual(
            manifest.upgrade_path(_load(), "5.9.0", "5.10.0"), ["5.9.0", "5.10.0"]
        )

    def test_upgrade_path_multihop_cross_major(self):
        # 4.12.0 -> 5.6.0 (cross-major edge) -> 5.10.0
        self.assertEqual(
            manifest.upgrade_path(_load(), "4.12.0", "5.10.0"),
            ["4.12.0", "5.6.0", "5.10.0"],
        )

    def test_upgrade_path_two_hops(self):
        self.assertEqual(
            manifest.upgrade_path(_load(), "4.11.0", "5.10.0"),
            ["4.11.0", "4.12.0", "5.6.0", "5.10.0"],
        )

    def test_upgrade_path_none(self):
        self.assertIsNone(manifest.upgrade_path(_load(), "5.10.0", "4.11.0"))


class TestStaleness(unittest.TestCase):
    def test_age_days(self):
        days = manifest.age_days(_load(), now=datetime(2026, 10, 24, 13, 0, tzinfo=timezone.utc))
        self.assertEqual(days, 1)

    def test_age_missing(self):
        self.assertIsNone(manifest.age_days({"schema_version": 1}))


class TestSchema(unittest.TestCase):
    def test_schema_ok(self):
        self.assertTrue(manifest.schema_ok(_load()))


# Manifest with a cross-major gap: 4.12.0 bridges to 5.6.0, but 4.11.0 has no
# documented outgoing edge (mirrors the real RankEZ data).
_GAP = {
    "schema_version": 1,
    "releases": {
        "5.10.0": {"upgrade_from": ["5.6.0"]},
        "5.6.0": {"upgrade_from": ["4.12.0"]},
        "4.12.0": {"upgrade_from": []},
        "4.11.0": {"upgrade_from": []},
    },
}


class TestSuggestUpgradeSteps(unittest.TestCase):
    def test_documented_direct(self):
        self.assertEqual(
            manifest.suggest_upgrade_steps(_GAP, "5.6.0", "5.10.0"),
            ["5.6.0", "5.10.0"],
        )

    def test_documented_bridge_from_pivot(self):
        self.assertEqual(
            manifest.suggest_upgrade_steps(_GAP, "4.12.0", "5.10.0"),
            ["4.12.0", "5.6.0", "5.10.0"],
        )

    def test_cross_major_inferred(self):
        # 4.11.0 has no documented edge, so the heuristic steps to the latest of
        # major 4 (4.12.0) then follows the documented bridge.
        self.assertEqual(
            manifest.suggest_upgrade_steps(_GAP, "4.11.0", "5.10.0"),
            ["4.11.0", "4.12.0", "5.6.0", "5.10.0"],
        )

    def test_same_major_inferred(self):
        self.assertEqual(
            manifest.suggest_upgrade_steps(_GAP, "4.11.0", "4.12.0"),
            ["4.11.0", "4.12.0"],
        )


if __name__ == "__main__":
    unittest.main()