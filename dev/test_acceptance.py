"""Task-book contracts and report mutations, no live model or tool calls."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "submission"))
from acceptance import _task_requirements, _task_checks, inspect_run

ROOT = Path(__file__).resolve().parents[1]


def requirements(case):
    return _task_requirements(ROOT / f"official/extracted/public_cases/task_2/case{case}/input/task_spec.md")


class AcceptanceTests(unittest.TestCase):
    def test_wrapper_policy_uses_explicit_port_lists_not_design_names(self):
        req = _task_requirements("端口 `payload`、`tag` 必须使用输入提供的 dedicated wrapper cell。\n"
                                 "Port `status` must use shared wrapper.\n端口 `control` 不使用 wrapper。")
        self.assertEqual(req["wrapper_ports"], [
            {"port": "payload", "style": "dedicated", "cell_type": "user_defined"},
            {"port": "tag", "style": "dedicated", "cell_type": "user_defined"},
            {"port": "status", "style": "shared", "cell_type": None},
            {"port": "control", "style": "none", "cell_type": None},
        ])

    def test_wrapper_port_policy_from_task_and_actual_implementation(self):
        # Existing real failure/success reports, read-only; no tool rerun.
        spec = (ROOT / "official/extracted/public_cases/task_1/case6/input/task_spec.md").read_text(encoding="utf-8")
        for folder, expected in (("demo_all04_task_1_case6", False), ("demo_rc4_c6_retry01_task_1_case6", True)):
            run = ROOT / "dev/results" / folder / "final_results"
            reports = {p.name: p.read_text(encoding="utf-8") for p in (run / "reports").glob("*.rpt")}
            # Rename identifiers in both task and observations: no case-name oracle.
            # Equal-width renames preserve the tool's fixed-column report layout.
            renamed_spec = spec.replace("key2", "busA").replace("key1", "busB")
            reports = {name: text.replace("key2", "busA").replace("key1", "busB") for name, text in reports.items()}
            req = _task_requirements(renamed_spec)
            details = {}
            checks, _, _ = _task_checks(run, reports, req, check_details=details)
            self.assertEqual(checks["task_wrapper_port_busA"], expected)
            self.assertTrue(checks["task_wrapper_port_busB"])
            if not expected:
                for ref in details["task_wrapper_port_busA"]["evidence"]:
                    self.assertEqual(reports[ref["source"]].splitlines()[ref["line"] - 1], ref["excerpt"])
                    self.assertIn("DFT Signal Port", ref["excerpt"])
            without_report = {name: text for name, text in reports.items() if "WrapperStyle" not in text}
            checks, _, _ = _task_checks(run, without_report, req)
            self.assertFalse(checks["task_wrapper_port_busA"])

    def test_split_partition_tables_follow_task_values(self):
        text = (ROOT / "official/extracted/public_cases/task_1/case2/input/task_spec.md").read_text(encoding="utf-8")
        req = _task_requirements(text)
        self.assertEqual([p["chain_count"] for p in req["partitions"]], [2, 4, 2, 1, 1, 1, 2])
        self.assertEqual([p["scan_enable"] for p in req["partitions"]], ["se_rx_fifo", "se_tx_fifo", "se_serdes", "se_serdes", "se_ctrl", "se_ctrl", "se_ctrl"])
        self.assertEqual(req["config"], {"add_lockup": "True", "mix_edges": "False", "mix_clocks": "False"})
        self.assertEqual(req["allowed_residuals"], ["DFTR10"])
        self.assertNotIn("DFTR10", req["allowed_ignores"])
        changed = _task_requirements(text.replace("统一为 300", "统一为 201"))
        self.assertTrue(all(p["max_length"] == 201 for p in changed["partitions"]))
        rows = []
        for part in req["partitions"]:
            for _ in range(part["chain_count"]):
                number = len(rows) + 1
                rows.append(f"I {number} 200 scan_data_in_{number} scan_data_out_{number} {part['scan_enable']} clk_i {part['name']} tool_created")
        rows.append("I 14 8 scan_data_in_14 scan_data_out_14 se_default clk_i Default_Partition tool_created")
        reports = {"chain.rpt": "ChainProperty ScanEnable\n" + "\n".join(rows),
                   "cfg.rpt": "ScanConfigurationParameter\nadd_lockup True\nmix_edges False\nmix_clocks False",
                   "drc.rpt": "Total violations: 0", "register.rpt": "ObjState",
                   "partition.rpt": "ByClock", "signals.rpt": "SignalType"}
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            (run / "deliverables").mkdir()
            for extension in ("v", "ctl", "def"):
                (run / "deliverables" / ("out." + extension)).write_text("test artifact")
            checks, _, _ = _task_checks(run, reports, req)
            self.assertTrue(all(checks.values()), checks)
            checks, _, _ = _task_checks(run, {n: t for n, t in reports.items() if n != "register.rpt"}, req)
            self.assertFalse(checks["task_report_Obj_?State"])
        for before, after, key in [("se_rx_fifo", "wrong_se", "task_partition_chains"),
                                   ("I 1 200", "I 1 301", "task_partition_chains"),
                                   ("mix_edges False", "mix_edges True", "task_config_mix_edges")]:
            modified = {name: value.replace(before, after) for name, value in reports.items()}
            checks, _, _ = _task_checks(ROOT, modified, req)
            self.assertFalse(checks[key])
        strict = _task_requirements(text.replace("自动归入默认分区", "保留在顶层"))
        checks, _, _ = _task_checks(ROOT, reports, strict)
        self.assertFalse(checks["task_no_extra_partition_chains"])

    def test_residual_permissions_require_explicit_task_and_complete_counts(self):
        req = _task_requirements("允许遗留标号为 DFTR10 的违例，无需处理。")
        for total, failures, accepted in [(0, "", True), (2, "There were 2 DRC rule 'DFTR10' fails", True),
                                           (3, "There were 2 DRC rule 'DFTR10' fails", False),
                                           (2, "There were 2 DRC rule 'DFTR11' fails", False)]:
            checks, _, _ = _task_checks(ROOT, {"drc.rpt": f"Total violations: {total}\n{failures}"}, req)
            self.assertEqual(checks["task_drc_allowed_residuals"], accepted)
        self.assertEqual(_task_requirements("必须修复 DFTR10 违例")["allowed_residuals"], [])

    def test_preprocessing_order_and_unmatched_selectors(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder) / "R1"
            (run / "reports").mkdir(parents=True)
            (run / "reports/drc.rpt").write_text("Total violations: 0")
            (run / "reports/chain.rpt").write_text("ChainProperty ScanEnable\nI 1 2 si so se clk Default_Partition tool_created")
            script = 'examine_scan_drc -file "$out_rpt_dir/drc.rpt"\nrpt_scan_chain -file "$out_rpt_dir/chain.rpt"'
            stages = ["examine_scan_drc", "examine_scan_chain", "insert_dft_logic"]
            for commands, expected in [(["insert_dft_logic -connect_icg_only"] + stages, True),
                                        (stages + ["insert_dft_logic -connect_icg_only"], False)]:
                (run / "R1.log").write_text("\n".join(f"[INFO] [CMD-0034] @{i}: {c}" for i, c in enumerate(commands)))
                result, _, _ = inspect_run(run, script, {"exit_status": "completed"}, full_insertion=True)
                self.assertEqual(result["accepted"], expected)
            (run / "R1.log").write_text("[WARNING] [   COM-2003] Nothing matched for wrong_path.")
            result, _, _ = inspect_run(run, script, {"exit_status": "completed"}, full_insertion=False)
            self.assertFalse(result["accepted"])

    def test_actual_task_books(self):
        one, two, three, four = [requirements(i) for i in range(1, 5)]
        self.assertEqual(one["chain_count"], 4)
        self.assertTrue(one["drc_zero"])
        self.assertNotIn("resetn", [s["port"] for s in one["signals"]])
        self.assertEqual([p["chain_count"] for p in two["partitions"]], [34, 1, 1])
        self.assertFalse(two["drc_zero"])
        self.assertIn({"port": "wb_rst_i", "role": "reset", "off_state": "0"}, two["signals"])
        self.assertEqual(three["max_count"], 5)
        self.assertNotIn("reset_n", [s["port"] for s in three["signals"]])
        self.assertEqual(three["formats"], ["scan_si_%d", "scan_so_%d"])
        self.assertEqual(four["max_length"], 100)
        self.assertEqual(len(four["outputs"]), 8)
        self.assertNotIn("input/netlist/cv32e40p.v", four["outputs"])

    def test_historical_correct_reports_and_mutations(self):
        for case, folder in [(1, "t2c1_arch_20260923_01"), (4, "t2c4_arch_20260923_01")]:
            base = ROOT / "dev/results" / folder / "final_results"
            reports = {"reports/" + p.name: p.read_text(encoding="utf-8") for p in (base / "reports").glob("*.rpt")}
            checks, _, _ = _task_checks(base, reports, requirements(case))
            self.assertTrue(all(checks.values()), checks)
            changed = dict(reports)
            key = "reports/scan_chain.rpt"
            changed[key] = changed[key].replace("test_se", "wrong_se") if case == 1 else changed[key].replace("scan_data_in_1", "bad_si_1")
            checks, _, _ = _task_checks(base, changed, requirements(case))
            self.assertFalse(all(checks.values()))
        reports = {"reports/scan_chain.rpt": "ChainProperty ScanEnable\nI 1 5 si so test_se clk Default tool_created\n",
                   "reports/drc.rpt": "Total violations: 0"}
        checks, _, _ = _task_checks(ROOT, reports, requirements(1))
        self.assertFalse(checks["task_chain_count"])

    def test_bad_partition_lengths_and_missing_outputs(self):
        base = ROOT / "dev/results/t2c2_stage_probe_01/runs/R3"
        reports = {"reports/" + p.name: p.read_text(encoding="utf-8") for p in (base / "reports").glob("*.rpt")}
        checks, _, _ = _task_checks(base, reports, requirements(2))
        self.assertFalse(checks["task_partition_chains"])
        with tempfile.TemporaryDirectory() as folder:
            checks, _, missing = _task_checks(Path(folder), {}, requirements(4))
            self.assertFalse(checks["task_required_outputs_present"])
            self.assertIn("post_scan.ctl", missing)

    def test_nonzero_drc_is_task_scoped_and_illegal_ignores_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder) / "R2"
            (run / "reports").mkdir(parents=True)
            (run / "reports/drc.rpt").write_text("Total violations: 21", encoding="utf-8")
            (run / "reports/chain.rpt").write_text("ChainProperty ScanEnable\nI 1 2 si so se clk Default_Partition tool_created", encoding="utf-8")
            log = "\n".join(f"[INFO] [CMD-0034] @{i}: {cmd}" for i, cmd in enumerate(("examine_scan_drc", "examine_scan_chain", "insert_dft_logic")))
            (run / "R2.log").write_text(log, encoding="utf-8")
            script = 'examine_scan_drc -file "$out_rpt_dir/drc.rpt"\nrpt_scan_chain -file "$out_rpt_dir/chain.rpt"'
            check, _, _ = inspect_run(run, script, {"exit_status": "completed"}, full_insertion=True, task_spec="执行 DRC 检查")
            self.assertTrue(check["accepted"])
            check, _, _ = inspect_run(run, script, {"exit_status": "completed"}, full_insertion=True, task_spec="DRC 检查无违例")
            self.assertFalse(check["accepted"])
            (run / "R2.log").write_text(log + "\n[INFO] [CMD-0034] @9: set_scan_drc_rule_handling {DFTR9} Ignore", encoding="utf-8")
            check, _, _ = inspect_run(run, script, {"exit_status": "completed"}, full_insertion=True, task_spec="执行 DRC 检查")
            self.assertFalse(check["checks"]["only_task_permitted_drc_downgrades"])


if __name__ == "__main__":
    unittest.main()
