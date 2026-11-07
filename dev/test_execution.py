"""Linux-only focused checks for interrupted runs and failure finalization."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "submission"))
import scan_agent
import scan_runner
import repair_task
from acceptance import inspect_run, _task_requirements
import time


@unittest.skipUnless(os.name == "posix", "Submission runs inside the Linux image")

class EvidenceReportTests(unittest.TestCase):
    def test_missing_evidence_reports_are_added_before_exit_only_for_insertion(self):
        from scan_runner import with_evidence_reports
        body = ('set_scan_element false {u_a/*}\nadd_pseudo_pi {u_b/q}\nexamine_scan_drc\nexamine_scan_chain\n'
                'insert_dft_logic\nrpt_scan_chain -file "$out_rpt_dir/c.rpt"\nexit\n')
        out = with_evidence_reports(body)
        added = out[len(body) - len('exit\n'):-len('exit\n')]
        self.assertTrue(out.endswith('exit\n'))
        self.assertIn('rpt_scan_element -type all', added)
        self.assertNotIn('rpt_pseudo_pi', added)  # unverified after insertion on large designs
        self.assertNotIn('rpt_scan_chain -file', added)
        self.assertNotIn('rpt_wrapper_cfg', added)
        self.assertEqual(with_evidence_reports(out), out)
        preprocessing = 'set_scan_element false {u_a/*}\nexamine_scan_drc\nexit\n'
        self.assertEqual(with_evidence_reports(preprocessing), preprocessing)

class ExecutionTests(unittest.TestCase):
    def test_requirement_quote_correction_does_not_rerun_or_change_scan(self):
        line = 'if {[llength $indices] >= 10} {'
        body = line + '\n    puts ready\n}\nexit'
        plan = {'dofile': body, 'requirements': [{'requirement': 'At least ten indices',
                                                'command': 'if {[llength $indices] >= 10]'}]}
        for corrected in (line, ''):
            with self.subTest(corrected=corrected), tempfile.TemporaryDirectory() as folder:
                source, target = Path(folder) / 'input', Path(folder) / 'output'
                source.mkdir()
                (source / 'task_spec.md').write_text('仅做预处理，不构建扫描链。', encoding='utf-8')
                (source / 'limitations.md').write_text('不超过 150 秒', encoding='utf-8')
                def run(script, run_dir, timeout, **kwargs):
                    (run_dir / 'deliverables').mkdir(parents=True)
                    (run_dir / 'reports').mkdir()
                    (run_dir / 'deliverables/R1.dofile').write_text(script, encoding='utf-8')
                    (run_dir / 'R1.log').write_text('Completed', encoding='utf-8')
                    return {'exit_status': 'completed', 'errors': [], 'tool_call_id': 'R1'}
                with patch.object(sys, 'argv', ['agent_system', '-input', str(source), '-output', str(target)]), \
                        patch.object(scan_agent, 'generate', side_effect=[(plan, {}), ({'commands': [corrected]}, {})]) as model, \
                        patch.object(scan_agent, 'run_scan', side_effect=run) as runner, \
                        patch.object(scan_agent, 'design_outline', return_value=([], [])), \
                        patch.object(scan_agent, 'inspect_run', return_value=({'accepted': True, 'missing_outputs': [], 'checks': {}}, {}, [])):
                    self.assertEqual(scan_agent.main(), 0)  # an accepted circuit is never lost to a citation typo
                self.assertEqual(runner.call_count, 1)
                self.assertEqual(model.call_count, 2)
                self.assertTrue((target / 'model_requirement_mapping.json').is_file())
                if corrected:
                    final = (target / 'final_results/deliverables/final.dofile').read_text(encoding='utf-8')
                    self.assertEqual(final, runner.call_args.args[0])
                    decision = json.loads((target / 'decision_log.json').read_text(encoding='utf-8'))
                    self.assertEqual(decision['requirement_mapping'][0]['dft_config'], line)
                else:
                    self.assertTrue((target / 'final_results').exists())
                    self.assertTrue((target / 'unmapped_requirements.json').is_file())

    def test_task1_semantic_failure_retries_completed_tool_run(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input"
            source.mkdir()
            (source / "task_spec.md").write_text("完整插链。DRC 检查无违例", encoding="utf-8")
            (source / "limitations.md").write_text("不超过 150 秒", encoding="utf-8")
            target = Path(folder) / "output"
            body = ('examine_scan_drc -file "$out_rpt_dir/drc.rpt"\n'
                    'examine_scan_chain\ninsert_dft_logic\n'
                    'rpt_scan_chain -file "$out_rpt_dir/chain.rpt"\nexit')
            # Live Luna has omitted repair despite returning a usable script.
            # Retain truthful missing-explanation metadata without losing the run.
            plan = {"dofile": body, "requirements": [{"requirement": "DRC then insertion",
                     "command": 'examine_scan_drc -file "$out_rpt_dir/drc.rpt"\ninsert_dft_logic'}]}

            def run(script, run_dir, timeout, **kwargs):
                (run_dir / "reports").mkdir(parents=True)
                (run_dir / "deliverables").mkdir()
                (run_dir / "deliverables" / f"{run_dir.name}.dofile").write_text(script)
                (run_dir / "reports/drc.rpt").write_text("Total violations: " + ("2" if run_dir.name == "R1" else "0"))
                (run_dir / "reports/chain.rpt").write_text("ChainProperty ScanEnable\nI 1 2 si so se clk Default_Partition tool_created\n")
                (run_dir / f"{run_dir.name}.log").write_text("\n".join(
                    f"[INFO] [CMD-0034] @{i}: {command}" for i, command in enumerate(repair_task.STAGES, 1)))
                value = {"exit_status": "completed", "errors": [], "tool_call_id": run_dir.name}
                (run_dir / "execution.json").write_text(json.dumps(value))
                return value

            with patch.object(sys, "argv", ["agent_system", "-input", str(source), "-output", str(target)]), \
                    patch.object(scan_agent, "generate", return_value=(plan, {})) as model, \
                    patch.object(scan_agent, "extract_task_constraints", return_value=(
                        _task_requirements((source / 'task_spec.md').read_text(encoding='utf-8')), [])), \
                    patch.object(scan_agent, "run_scan", side_effect=run):
                self.assertEqual(scan_agent.main(), 0)
            self.assertEqual(model.call_count, 2)
            first, correction = model.call_args_list
            self.assertTrue(correction.args[0].startswith(first.args[0][:-1]))
            self.assertNotIn('tool_guide', json.loads(first.args[0]))
            self.assertTrue(first.kwargs['tool_guide'])
            self.assertEqual(first.kwargs['tool_guide'], correction.kwargs['tool_guide'])
            feedback = json.loads(model.call_args.args[0])["previous_run"]
            self.assertFalse(feedback["acceptance"]["accepted"])
            self.assertEqual(feedback["errors"], [])
            decision = json.loads((target / "decision_log.json").read_text())
            self.assertEqual(decision["final_run"], "R2")
            self.assertEqual(len(decision["requirement_mapping"]), 2)
            self.assertIn("未返回根因解释", decision["issue_resolutions"][0]["diagnosis"]["root_cause"])
            self.assertEqual([r["exit_status"] for r in decision["tool_runs"]], ["completed", "completed"])
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from verify_flow import verify
            self.assertTrue(all(verify(target)["checks"].values()))

    def test_task1_flow_selected_from_task_not_model(self):
        public = Path(__file__).resolve().parents[1] / "official/extracted/public_cases/task_1"
        for case in range(1, 7):
            spec = (public / f"case{case}/input/task_spec.md").read_text(encoding="utf-8")
            self.assertEqual(bool(scan_agent.task1_full_insertion(spec)), case not in (1, 4))
        _, instances = scan_agent.design_outline(public / "case1/input")
        identities = {(item["path"], item["type"]) for item in instances}
        self.assertIn(("x_cr_core_top/x_cr_sys_io_pll", "cr_sys_io_pll"), identities)
        self.assertNotIn(("x_cr_sys_io_pll", "cr_sys_io_pll"), identities)

    def test_shared_paths_and_preprocessing_acceptance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = root / "R1"
            (run / "deliverables").mkdir(parents=True)
            (run / "reports").mkdir()
            (run / "R1.log").write_text("actual preprocessing test log")
            (run / "deliverables/post.v").write_text("module test; endmodule")
            body = 'dump_netlist -file "$out_res_dir/post.v"'
            script = scan_runner.generated_script(root, run, body)
            for variable in ("input_dir", "out_res_dir", "out_rpt_dir"):
                with self.assertRaises(ValueError):
                    scan_runner.generated_script(root, run, f"set {variable} old_round\n" + body)
            result, _, _ = inspect_run(run, script, {"exit_status": "completed"}, full_insertion=False)
            self.assertTrue(result["accepted"])
            self.assertNotIn("reported_drc_zero", result["checks"])
            with self.assertRaises(ValueError):
                inspect_run(run, 'dump_netlist -file "$out_res_dir/../../outside.v"', {"exit_status": "completed"}, full_insertion=False)
            (run / "deliverables/post.v").write_text("")
            result, _, _ = inspect_run(run, script, {"exit_status": "completed"}, full_insertion=False)
            self.assertFalse(result["accepted"])

    def test_reported_coverage_cannot_hide_missing_chain_cells(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            (run / "reports").mkdir()
            (run / f"{run.name}.log").write_text("\n".join(
                f"[INFO] [CMD-0034] @{i}: {command}" for i, command in enumerate(repair_task.STAGES, 1)))
            (run / "reports/drc.rpt").write_text("Total violations: 0")
            (run / "reports/info.rpt").write_text("9 Scannable FF Count 100\n11 Scan Chain Cell Count 70\n")
            (run / "reports/chain.rpt").write_text("ChainProperty\nI 1 70 si so se clk default tool_created\n")
            script = '\n'.join(f'report -file "$out_rpt_dir/{name}.rpt"' for name in ("drc", "info", "chain"))
            result, _, _ = inspect_run(run, script, {"exit_status": "completed"}, full_insertion=True)
            self.assertFalse(result["accepted"])
            self.assertFalse(result["checks"]["reported_chain_coverage"])
    def test_drc_failure_is_independent_of_model_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / "runs/R1").mkdir(parents=True)
            (target / "runs/R1/R1.log").write_text("Original execution evidence", encoding="utf-8")
            run = target / "runs/R2"
            (run / "reports").mkdir(parents=True)
            (run / "deliverables").mkdir()
            (run / "reports/drc.rpt").write_text("Total violations: 2219\n")
            (run / "R2.log").write_text("\n".join(
                f"[INFO] [CMD-0034] @{i}: {command}" for i, command in enumerate(repair_task.STAGES, 1)))
            issue = {"phenomenon": "test", "located_object": "test", "root_cause": "test",
                     "violated_requirement": "DRC zero", "original_command": "original", "fix_action": "test"}
            approval = {"all_requirements_met": True, "summary": "model approves",
                        "issues": {'I1': {"issue_number": 1, "resolved": True, "original_defect_supported": True, "evidence_id": "E1"}}}
            with patch.object(scan_agent, "generate", return_value=(approval, {})):
                _, _, _, feedback = repair_task.verify_round(
                    target, run, 'examine_scan_drc -file "$out_rpt_dir/drc.rpt"',
                    {"exit_status": "completed"}, {"issues": [issue]}, "original", "DRC 检查无违例", "", time.monotonic() + 10, "F1", scan_agent.generate)
            self.assertTrue(feedback["independent_checks"]["workflow_order"])
            self.assertFalse(feedback["independent_checks"]["reported_drc_zero"])

    def test_scan_timeout_and_global_deadline_leave_records(self):
        real_popen = subprocess.Popen
        for alarm in (False, True):
            with self.subTest(global_deadline=alarm), tempfile.TemporaryDirectory() as folder:
                children = []

                def launch(*args, **kwargs):
                    child = real_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
                    children.append(child)
                    return child

                def expired(*args):
                    raise TimeoutError("test deadline")

                previous = signal.signal(signal.SIGALRM, expired)
                try:
                    if alarm:
                        signal.setitimer(signal.ITIMER_REAL, 0.1)
                    with patch.object(scan_runner.subprocess, "Popen", side_effect=launch):
                        if alarm:
                            with self.assertRaises(TimeoutError):
                                scan_runner.run_scan("exit", Path(folder) / "R1", 20)
                        else:
                            scan_runner.run_scan("exit", Path(folder) / "R1", 0.1)
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                    signal.signal(signal.SIGALRM, previous)
                result = json.loads((Path(folder) / "R1/execution.json").read_text())
                self.assertEqual(result["exit_status"], "aborted")
                self.assertIsNotNone(children[0].poll())

        for failure in (subprocess.TimeoutExpired('yosys', 0.1), TimeoutError('test deadline')):
            with self.subTest(check_failure=type(failure).__name__), tempfile.TemporaryDirectory() as folder:
                source, run = Path(folder), Path(folder) / 'R1'
                def finish_scan(*args, **kwargs):
                    (run / 'deliverables/post.v').write_text('module top; endmodule\n')
                    return real_popen([sys.executable, '-c', 'pass'], **kwargs)
                with patch.object(scan_runner.subprocess, 'Popen', side_effect=finish_scan), \
                     patch('control_context.constant_output_check', side_effect=failure):
                    if isinstance(failure, TimeoutError):
                        with self.assertRaises(TimeoutError):
                            scan_runner.run_scan('dump_netlist -file "$out_res_dir/post.v"', run, 20, source=source)
                    else:
                        scan_runner.run_scan('dump_netlist -file "$out_res_dir/post.v"', run, 20, source=source)
                result = json.loads((run / 'execution.json').read_text())
                self.assertEqual(result['exit_status'], 'aborted')
                self.assertFalse(result['constant_output_integrity']['checked'])
                self.assertEqual(result['failure_type'], type(failure).__name__)

    def test_missing_scan_binary_is_recorded(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(scan_runner.subprocess, "Popen", side_effect=FileNotFoundError):
                with self.assertRaises(FileNotFoundError):
                    scan_runner.run_scan("exit", Path(folder) / "R1", 1)
            result = json.loads((Path(folder) / "R1/execution.json").read_text())
            self.assertEqual(result["exit_status"], "error")
            self.assertEqual(result["failure_type"], "FileNotFoundError")

    def test_api_failure_decision_and_old_output_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input"
            source.mkdir()
            (source / "task_spec.md").write_text("Failure handling test", encoding="utf-8")
            (source / "limitations.md").write_text("不超过 150 秒", encoding="utf-8")
            target = Path(folder) / "output"
            target.mkdir()
            (target / '.dockerenv').write_text('platform metadata')
            (target / 'container.log').write_text('platform stdout stream')
            with patch.object(sys, "argv", ["agent_system", "-input", str(source), "-output", str(target)]):
                with patch.object(scan_agent, "generate", side_effect=RuntimeError("secret-marker")):
                    self.assertEqual(scan_agent.main(), 1)
                saved = (target / "decision_log.json").read_bytes()
                decision = json.loads(saved)
                self.assertIsNone(decision["final_run"])
                self.assertEqual(decision["failure_type"], "RuntimeError")
                self.assertNotIn(b"secret-marker", saved)
                self.assertEqual((target / '.dockerenv').read_text(), 'platform metadata')
                self.assertEqual((target / 'container.log').read_text(), 'platform stdout stream')
                with self.assertRaises(ValueError):
                    scan_agent.main()
                self.assertEqual(saved, (target / "decision_log.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
