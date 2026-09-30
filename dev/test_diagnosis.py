"""Offline checks for Task2 diagnosis evidence filtering."""

import sys
import unittest
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "submission"))
from repair_task import sanitize_diagnoses, verify_round


SCRIPT = """set script_dir {/input}
load_lib [file join $input_dir lib sky130.lib]
present_design top
set_scan_signal -type clock -port clk -off_state 0
set_scan_cfg -chain_count 2
insert_dft_logic
"""


def issue(command, root="distinct root"):
    return {
        "phenomenon": "observed problem",
        "located_object": "flow",
        "root_cause": root,
        "violated_requirement": "task requirement",
        "original_command": command,
        "fix_action": "correct command",
    }


class DiagnosisFilterTests(unittest.TestCase):
    def test_successful_execution_does_not_validate_false_original_defect(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            run = target / "runs/R2"
            (run / "reports").mkdir(parents=True)
            (run / "deliverables").mkdir()
            (run / "reports/drc.rpt").write_text("Total violations: 0", encoding="utf-8")
            (run / "R2.log").write_text("\n".join(f"[INFO] [CMD-0034] @{i}: {c}" for i, c in enumerate(("examine_scan_drc", "examine_scan_chain", "insert_dft_logic"))), encoding="utf-8")
            def verify(*args):
                return {"all_requirements_met": True, "summary": "real output passes",
                        "issues": [{"original_defect_supported": supported, "support_basis": "default is valid" if not supported else "wrong chain count",
                                    "resolved": True, "source": "R2.log", "line_start": 1, "line_end": 3}
                                   for supported in (False, True)]}, {}
            plan = {"issues": [issue("load_lib [file join $input_dir lib sky130.lib]", "path renaming"), issue("set_scan_cfg -chain_count 2", "wrong chain count")]}
            found, _, _, feedback = verify_round(target, run, 'examine_scan_drc -file "$out_rpt_dir/drc.rpt"',
                {"exit_status": "completed"}, plan, SCRIPT, "DRC 检查无违例", "", time.monotonic() + 10, "F1", verify)
            self.assertEqual([i["issue_id"] for i in found], ["I2"])
            self.assertEqual(feedback["rejected_diagnoses"][0]["issue_id"], "I1")

    def test_keeps_exact_scan_evidence(self):
        plan = {"issues": [issue("set_scan_cfg -chain_count 2")]}
        sanitize_diagnoses(plan, SCRIPT)
        self.assertEqual(len(plan["issues"]), 1)
        self.assertEqual(plan["rejected_diagnoses"], [])

    def test_rejects_runner_path_and_missing_citation(self):
        plan = {"issues": [
            issue("set script_dir {/input}", "path root"),
            issue("set_scan_cfg -chain_count 999", "invented root"),
            issue("set_scan_cfg -chain_count 2", "valid root"),
        ]}
        sanitize_diagnoses(plan, SCRIPT)
        self.assertEqual(len(plan["issues"]), 1)
        self.assertEqual(len(plan["rejected_diagnoses"]), 2)
        self.assertIn("runner-owned", plan["rejected_diagnoses"][0]["reason"])

    def test_same_command_can_have_distinct_defects(self):
        plan = {"issues": [issue("set_scan_cfg -chain_count 2", "wrong chain count"),
                           issue("set_scan_cfg -chain_count 2", "missing required lockup")]}
        sanitize_diagnoses(plan, SCRIPT)
        self.assertEqual(len(plan["issues"]), 2)

    def test_missing_verifier_reference_is_unresolved_not_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            run = target / "runs/R2"
            (run / "reports").mkdir(parents=True)
            (run / "deliverables").mkdir()
            (run / "reports/drc.rpt").write_text("Total violations: 0", encoding="utf-8")
            (run / "R2.log").write_text("real test log", encoding="utf-8")
            def verify(*args):
                return {"all_requirements_met": True, "summary": "model approval lacks evidence",
                        "issues": [{"resolved": True, "original_defect_supported": True, "support_basis": "prose only"}]}, {}
            found, _, _, _ = verify_round(target, run, 'examine_scan_drc -file "$out_rpt_dir/drc.rpt"',
                {"exit_status": "completed"}, {"issues": [issue("set_scan_cfg -chain_count 2")]},
                SCRIPT, "DRC 检查无违例", "", time.monotonic() + 10, "F1", verify)
            self.assertFalse(found[0]["attempts"][0]["verify"]["resolved"])

    def test_rejects_duplicate_root_without_losing_audit_record(self):
        plan = {"issues": [
            issue("set_scan_cfg -chain_count 2", "same root"),
            issue("set_scan_cfg -chain_count 2", "same root"),
        ]}
        sanitize_diagnoses(plan, SCRIPT)
        self.assertEqual(len(plan["issues"]), 1)
        self.assertEqual(plan["rejected_diagnoses"][0]["reason"], "duplicate command and root cause")

if __name__ == "__main__":
    unittest.main()
