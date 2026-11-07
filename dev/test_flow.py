"""Regression checks for the phase-three independent public-case audits."""

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_flow import verify
from challenge_suite import audit as audit_challenge
from verify_wrapper import scandef_members_match
from verify_opensource import functional_pins

RESULTS = Path(__file__).resolve().parent / "results"


class FlowAuditTests(unittest.TestCase):
    def test_functional_connectivity_uses_endpoints_not_wire_alias_names(self):
        from copy import deepcopy
        before = {'cells': {'u': {'type': 'sky130_fd_sc_hd__dfxtp_1',
                    'connections': {'CLK': [1], 'D': [2], 'Q': [3]}}},
                  'ports': {'clk': {'direction': 'input', 'bits': [1]},
                            'd': {'direction': 'input', 'bits': [2]},
                            'q': {'direction': 'output', 'bits': [3]}},
                  'netnames': {'clk': {'bits': [1]}, 'd': {'bits': [2]}, 'q': {'bits': [3]}}}
        after = deepcopy(before)
        after['cells']['u']['type'] = 'sky130_fd_sc_hd__sdfxtp_1'
        after['cells']['u']['connections'].update(SCD=[5], SCE=[6])
        self.assertTrue(all(functional_pins(before, after)[0].values()))
        # Keep the cell-side q alias while disconnecting the actual output.
        after['ports']['q']['bits'] = [4]
        self.assertFalse(functional_pins(before, after)[0]['all_functional_pins_preserved'])
        self.assertTrue(all(functional_pins(before, before, {'u'})[0].values()))
        broken = deepcopy(before)
        broken['cells']['u']['connections']['D'] = [3]
        self.assertFalse(functional_pins(before, broken, {'u'})[0]['all_functional_pins_preserved'])

    def test_scandef_lockup_splits_require_every_physical_scan_member(self):
        text = ('  - SubScanChain_7_SUB1\n'
                '    + FLOATING\n'
                '      reg\\[0\\] ( IN SCD ) ( OUT Q ) ( BITS 1 )\n'
                '      mux0 ( IN A0 ) ( OUT X ) ( BITS 0 )\n'
                '    + STOP lockup D ;\n'
                '  - SubScanChain_7_SUB2\n'
                '    + FLOATING\n'
                '      reg1 ( IN SCD ) ( OUT Q ) ( BITS 1 )\n'
                '    + STOP PIN so ;\n')
        expected = {'7': Counter({'reg[0]': 1, 'reg1': 1})}
        self.assertTrue(scandef_members_match(text, expected))
        self.assertFalse(scandef_members_match(text[:text.index('  - SubScanChain_7_SUB2')], expected))
        self.assertFalse(scandef_members_match(text.replace('reg1 (', 'reg\\[0\\] ('), expected))
        self.assertFalse(scandef_members_match(text.replace('7_SUB2', '8_SUB2'), expected))

    def test_challenge_score_rejects_broken_serial_wire_despite_correct_reports(self):
        oracle = json.loads((RESULTS / "capability_inputs01/d_clock_count/oracle.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            shutil.copytree(RESULTS / "capability_probe02_d_clock_count", root)
            final_id = json.loads((root / "decision_log.json").read_text(encoding="utf-8"))["final_run"]
            for base in (root / "final_results", root / "runs" / final_id):
                netlist = base / "deliverables/post_scan.v"
                text = netlist.read_text()
                start = text.index(".SCD(") + len(".SCD(")
                end = text.index(")", start)
                netlist.write_text(text[:start] + "data[0]" + text[end:])
            score = audit_challenge(root, oracle)
            self.assertTrue(score["checks"]["final_bytes_match_run"])
            self.assertTrue(score["checks"]["ctl_report_agree"])
            self.assertFalse(score["checks"]["netlist_serial_connectivity"])
            self.assertFalse(score["artifact_passed"])

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
