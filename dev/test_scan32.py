"""Bounded adversarial checks for the new benchmark's independent grader."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from scan32 import SUITE, audit


class Scan32GraderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SUITE / "qualification/fixture02/C06_cascade_gate"
        cls.oracle = json.loads((SUITE / "oracles/C06_cascade_gate/oracle.json").read_text())

    def test_reference_execution_never_counts_as_model_success(self):
        qualified = audit(self.source, self.oracle, fixture=True)
        self.assertTrue(qualified["passed"], qualified)
        model_score = audit(self.source, self.oracle)
        self.assertFalse(model_score["checks"]["execution_mode_matches"])
        self.assertFalse(model_score["passed"])

    def test_actual_serial_wire_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            shutil.copytree(self.source, root)
            for relative in ("runs/R1/deliverables/post_scan.v", "final_results/deliverables/post_scan.v"):
                path = root / relative
                text = path.read_text()
                start = text.index(".SCD(") + len(".SCD(")
                end = text.index(")", start)
                path.write_text(text[:start] + "din[0]" + text[end:])
            result = audit(root, self.oracle, fixture=True)
            self.assertTrue(result["checks"]["final_bytes_match_run"])
            self.assertTrue(result["checks"]["ctl_report_agreement"])
            self.assertFalse(result["checks"]["netlist_serial_connections"])
            self.assertFalse(result["passed"])

    def test_functional_gate_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            shutil.copytree(self.source, root)
            for relative in ("runs/R1/deliverables/post_scan.v", "final_results/deliverables/post_scan.v"):
                path = root / relative
                text = path.read_text().replace("mix0_0 (.A(din[0])", "mix0_0 (.A(din[1])", 1)
                path.write_text(text)
            result = audit(root, self.oracle, fixture=True)
            self.assertTrue(result["checks"]["netlist_serial_connections"])
            self.assertFalse(result["checks"]["netlist_functional_xor_network"])
            self.assertFalse(result["passed"])


if __name__ == "__main__":
    unittest.main()
