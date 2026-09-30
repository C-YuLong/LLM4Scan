"""Regression checks for the phase-three independent public-case audits."""

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_flow import verify

RESULTS = Path(__file__).resolve().parent / "results"


class FlowAuditTests(unittest.TestCase):
    def test_ac97_real_outputs(self):
        result = verify(RESULTS / "stage3_ac97_final01_task_1_case2", ac97=True)
        self.assertTrue(all(result["checks"].values()), result)
        self.assertEqual(result["chains"], 14)
        self.assertEqual(result["unique_cells"], 2229)

    def test_wrong_hierarchy_rejected_even_with_unchanged_chain_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            shutil.copytree(RESULTS / "stage3_ac97_final01_task_1_case2", root)
            report = root / "final_results/reports/scan_chain_cell.rpt"
            text = report.read_text(encoding="utf-8")
            self.assertIn("u0/", text)
            changed = text.replace("u0/", "u9/", 1)
            report.write_text(changed, encoding="utf-8")
            # Mutate both copies so the semantic check is tested independently
            # of the already-covered byte-for-byte provenance check.
            final_id = json.loads((root / "decision_log.json").read_text(encoding="utf-8"))["final_run"]
            (root / "runs" / final_id / "reports/scan_chain_cell.rpt").write_text(changed, encoding="utf-8")
            checks = verify(root, ac97=True)["checks"]
            self.assertTrue(checks["final_bytes_match_run"])
            self.assertTrue(checks["ac97_partition_counts_enables_lengths"])
            self.assertFalse(checks["ac97_cell_hierarchy_partition"])
            self.assertFalse(checks["ac97_ctl_cells_match_report"])

    def test_picorv32_cell_report_name_is_not_fixed(self):
        root = RESULTS / "stage3_core_01_task_2_case1"
        self.assertFalse((root / "final_results/reports/scan_cell.rpt").exists())
        result = verify(root, picorv32=True)
        self.assertTrue(all(result["checks"].values()), result)


if __name__ == "__main__":
    unittest.main()
