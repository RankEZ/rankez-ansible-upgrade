"""Unit tests for scripts/plan_upgrade.py (pure planner, no target contact).

Run:  python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import plan_upgrade as P  # noqa: E402
import manifest as M  # noqa: E402

FIXTURE = HERE / "fixtures" / "manifest.json"


def _load():
    return M.load(str(FIXTURE))


# Cross-major gap: 4.12.0 bridges to 5.6.0, but 4.11.0 has no documented edge.
_GAP = {
    "schema_version": 1,
    "releases": {
        "5.10.0": {"upgrade_from": ["5.6.0"]},
        "5.6.0": {"upgrade_from": ["4.12.0"]},
        "4.12.0": {"upgrade_from": []},
        "4.11.0": {"upgrade_from": []},
    },
}


class TestPlanBasic(unittest.TestCase):
    def test_direct(self):
        p = P.plan("5.9.0", "5.10.0", _load())
        self.assertEqual(p["status"], "ok")
        self.assertEqual(p["mode"], "direct")
        self.assertEqual(p["num_hops"], 1)
        self.assertEqual(p["inferred_hops"], 0)
        self.assertEqual(p["hops"][0]["from"], "5.9.0")
        self.assertEqual(p["hops"][0]["to"], "5.10.0")
        self.assertFalse(p["hops"][0]["inferred"])

    def test_multihop_documented(self):
        p = P.plan("4.11.0", "5.10.0", _load())
        self.assertEqual(p["mode"], "multi-hop")
        self.assertEqual(p["num_hops"], 3)
        self.assertEqual(p["inferred_hops"], 0)
        self.assertEqual([h["from"] for h in p["hops"]], ["4.11.0", "4.12.0", "5.6.0"])
        self.assertEqual(p["hops"][-1]["to"], "5.10.0")

    def test_cross_major_hop_present(self):
        p = P.plan("4.11.0", "5.10.0", _load())
        hop = [h for h in p["hops"] if h["from"] == "4.12.0" and h["to"] == "5.6.0"]
        self.assertEqual(len(hop), 1)
        self.assertFalse(hop[0]["inferred"])

    def test_downgrade(self):
        self.assertEqual(P.plan("5.10.0", "5.9.0", _load())["status"], "downgrade")

    def test_same(self):
        self.assertEqual(P.plan("5.9.0", "5.9.0", _load())["status"], "same")

    def test_unknown_target(self):
        self.assertEqual(P.plan("5.9.0", "9.9.9", _load())["status"], "unknown_target")


class TestPlanInferred(unittest.TestCase):
    def test_inferred_hop_flagged(self):
        p = P.plan("4.11.0", "5.10.0", _GAP)
        self.assertEqual(p["status"], "ok")
        self.assertEqual(p["mode"], "inferred")
        self.assertEqual(p["inferred_hops"], 1)
        # The 4.11.0 -> 4.12.0 hop is inferred; the bridge and final hop are not.
        byfrom = {h["from"]: h for h in p["hops"]}
        self.assertTrue(byfrom["4.11.0"]["inferred"])
        self.assertFalse(byfrom["4.12.0"]["inferred"])
        self.assertFalse(byfrom["5.6.0"]["inferred"])

    def test_hop_has_tarballs(self):
        p = P.plan("4.11.0", "5.10.0", _GAP)
        for h in p["hops"]:
            self.assertIn("vault", h["component_versions"])
            self.assertTrue(any(t.startswith("pam-vault-") for t in h["tarballs"]))


class TestPlanNoPath(unittest.TestCase):
    def test_unreachable_cross_major(self):
        # 5.6.0's only source is 5.5.0 (no 4.x bridge), so 4.x cannot reach 5.x.
        m = {
            "schema_version": 1,
            "releases": {
                "5.6.0": {"upgrade_from": ["5.5.0"]},
                "5.5.0": {"upgrade_from": ["5.4.0"]},
                "4.12.0": {"upgrade_from": ["4.11.0"]},
                "4.11.0": {"upgrade_from": []},
                "5.10.0": {"upgrade_from": ["5.6.0"]},
            },
        }
        p = P.plan("4.11.0", "5.10.0", m)
        self.assertEqual(p["status"], "no_path")


if __name__ == "__main__":
    unittest.main()