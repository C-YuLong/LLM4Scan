"""Offline checks for Task2 diagnosis evidence filtering."""

import sys
from copy import deepcopy
import json
import unittest
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "submission"))
import repair_task
from repair_task import sanitize_diagnoses, verify_round, explicit_diagnosis_gaps, diagnosis_location


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


def candidate_evidence(prompt, source, text):
    return next(line.split(':', 1)[0] for line in json.loads(prompt)['actual_files'][source].splitlines()
                if line.split(':', 1)[1].strip() == text.strip())


class DiagnosisFilterTests(unittest.TestCase):
    def test_optional_ignore_permission_does_not_make_a_legal_subset_faulty(self):
        quote = 'DFTR-TIE0/DFTR-TIE1 属设计固有特性，允许忽略。'
        command = 'set_scan_drc_rule_handling {DFTR-TIE1} Ignore'
        finding = {**issue(command), 'category': 'configuration', 'defective_parameter': 'arg1',
                   'violated_requirement': quote}
        plan = sanitize_diagnoses({'issues': [finding]}, command, quote)
        self.assertEqual(plan['issues'], [])
        self.assertIn('does not require', plan['rejected_diagnoses'][0]['reason'])
        self.assertFalse(plan['rejected_diagnoses'][0]['requires_correction'])
        uncited = sanitize_diagnoses({'issues': [{**finding, 'original_command': 'absent'}]}, command, quote)
        self.assertTrue(uncited['rejected_diagnoses'][0]['requires_correction'])
        invalid = 'set_scan_drc_rule_handling {TIE0 TIE1} Ignore'
        plan = sanitize_diagnoses({'issues': [{**finding, 'original_command': invalid}]}, invalid, quote)
        self.assertEqual(len(plan['issues']), 1)
        mandatory = '必须忽略 DFTR-TIE0/DFTR-TIE1。'
        plan = sanitize_diagnoses({'issues': [{**finding, 'violated_requirement': mandatory}]}, command, mandatory)
        self.assertEqual(len(plan['issues']), 1)

    def test_reported_format_faults_require_separate_parameter_findings(self):
        script = 'set_scan_cfg -max_length 50'
        req = {'signals': [], 'formats': ['in_%d', 'out_%d'], 'config': {'add_lockup': 'True'}}
        checks = {'task_port_formats': False, 'task_config_add_lockup': True}
        reports = {'cfg.rpt': 'ScanConfigurationParameter Value\nsi_port_format test_si%d\nso_port_format test_so%d\nadd_lockup True\n'}
        finding = {**issue(script), 'defective_parameter': '-si_port_format'}
        gaps = explicit_diagnosis_gaps(script, req, checks, [finding], original_reports=reports)
        self.assertEqual([g['required_field'] for g in gaps], ['so_port_format'])
        self.assertEqual(explicit_diagnosis_gaps(script, req, checks, [finding]), [])
        correct_output = {'cfg.rpt': reports['cfg.rpt'].replace('test_so%d', 'out_%d')}
        self.assertEqual(explicit_diagnosis_gaps(script, req, checks, [finding], original_reports=correct_output), [])

    def test_missing_requested_export_is_not_hidden_by_other_drc_defects(self):
        script = 'insert_dft_logic\ndump_netlist -file "$out_res_dir/post_scan.v"\nexit'
        req = {'signals': [], 'outputs': ['post_scan.v', 'post_scan.def']}
        checks = {'execution_completed': True, 'task_required_outputs_present': False}
        gaps = explicit_diagnosis_gaps(script, req, checks, [])
        self.assertEqual([g['required_command'] for g in gaps], ['dump_def'])
        diagnosed = {**issue('exit'), 'fix_action': 'Export the requested SCANDEF after insertion.'}
        self.assertEqual(explicit_diagnosis_gaps(script, req, checks, [diagnosed]), [])
        self.assertEqual(explicit_diagnosis_gaps(script + '\ndump_def output.def', req, checks, []), [])
        self.assertEqual(explicit_diagnosis_gaps(script, req, {**checks, 'execution_completed': False}, []), [])
        self.assertEqual(explicit_diagnosis_gaps(script, {'signals': [], 'outputs': []}, checks, []), [])

    def test_observed_invalid_wrapper_port_is_not_a_channel_budget_diagnosis(self):
        command = 'set_wrapper_cfg \\\n    -port nonexistent_pin'
        log = "[WARNING] [  SCAN-4948] Port 'nonexistent_pin' does not exist in current design."
        checks = {'execution_completed': True, 'no_unmatched_object_selectors': False}
        req = {'signals': []}
        channel = {**issue(command), 'defective_parameter': '-chain_count'}
        gaps = explicit_diagnosis_gaps(command, req, checks, [channel], original_log=log)
        self.assertEqual([g['original_value'] for g in gaps], ['nonexistent_pin'])
        diagnosed = {**issue('-port nonexistent_pin'), 'defective_parameter': '-port'}
        self.assertEqual(explicit_diagnosis_gaps(command, req, checks, [diagnosed], original_log=log), [])
        for script, trace, completed in ((command, log.replace('SCAN-4948', 'SCAN-4929'), True),
                                         (command.replace('nonexistent_pin', 'valid_pin'), log, True),
                                         (command, log, False)):
            self.assertEqual(explicit_diagnosis_gaps(script, req, {'execution_completed': completed}, [], original_log=trace), [])

    def test_explicit_register_report_omission_needs_diagnosis_not_just_repair(self):
        script = 'rpt_scan_chain_cell -file "$out_rpt_dir/cells.rpt"\nexit\n'
        req = {'signals': [], 'report_headers': ['Obj_?State']}
        checks = {'task_report_Obj_?State': False}
        gaps = explicit_diagnosis_gaps(script, req, checks, [])
        self.assertEqual([g['required_command'] for g in gaps], ['rpt_scan_element -type all'])
        diagnosed = {**issue(script), 'defective_parameter': '',
                     'fix_action': 'Export the requested register report with rpt_scan_element -type all.'}
        self.assertEqual(explicit_diagnosis_gaps(script, req, checks, [diagnosed]), [])
        # A real run named the missing command correctly, but labeled its
        # -type operand; that metadata does not erase the stated root cause.
        typed = {**diagnosed, 'defective_parameter': '-type',
                 'root_cause': 'The required rpt_scan_element -type all command is absent.'}
        self.assertEqual(explicit_diagnosis_gaps(script, req, checks, [typed]), [])
        incidental = {**typed, 'root_cause': 'Wrong clock polarity.', 'defective_parameter': '-off_state'}
        self.assertTrue(explicit_diagnosis_gaps(script, req, checks, [incidental]))
        self.assertEqual(explicit_diagnosis_gaps(script + 'rpt_scan_element -type all\n', req, checks, []), [])
        self.assertEqual(explicit_diagnosis_gaps(script, {'signals': [], 'report_headers': []}, checks, []), [])
        self.assertEqual(explicit_diagnosis_gaps(script, req, {'task_report_Obj_?State': True}, []), [])

    def test_diagnosis_cannot_turn_source_clock_count_into_internal_clock_policy(self):
        command = 'set_scan_cfg -internal_clocks single'
        quote = '设计为单时钟域，由顶层时钟端口 clk 驱动。'
        finding = {**issue(command), 'category': 'configuration',
                   'defective_parameter': '-internal_clocks', 'violated_requirement': quote}
        plan = sanitize_diagnoses({'issues': [finding]}, command, quote)
        self.assertEqual(plan['issues'], [])
        self.assertIn('Source-clock count', plan['rejected_diagnoses'][0]['reason'])
        explicit = '内部时钟模式必须为 none。'
        finding['violated_requirement'] = explicit
        self.assertEqual(len(sanitize_diagnoses({'issues': [finding]}, command, explicit)['issues']), 1)

    def test_later_original_defect_is_cited_and_keeps_earlier_attempt_history(self):
        original = ('set_scan_cfg -chain_count 1\n'
                    'set_scan_signal -type reset -port clear_n -off_state 0\n'
                    'examine_scan_drc\nexamine_scan_chain\ninsert_dft_logic\n')
        first_issue = {**issue('set_scan_cfg -chain_count 1', 'wrong count'),
                       'original_source': 'original.dofile', 'category': 'configuration',
                       'defective_parameter': '-chain_count'}
        additional = {**issue('set_scan_signal -type reset -port clear_n -off_state 0', 'wrong reset polarity'),
                      'original_source': 'original.dofile', 'category': 'configuration',
                      'defective_parameter': '-off_state', 'violated_requirement': 'L1'}
        for failure in (None, 'retain_identity', 'diagnosis_only', 'citation', 'update_index', 'unchanged'):
            valid = failure in (None, 'retain_identity', 'diagnosis_only')
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                source, target = Path(folder) / 'input', Path(folder) / 'output'
                source.mkdir()
                target.mkdir()
                (source / 'original.dofile').write_text(original, encoding='utf-8')
                (source / 'task_spec.md').write_text('task requirement', encoding='utf-8')
                calls, verified, reviews = [], [], []
                def generate(prompt, timeout, instruction, **kwargs):
                    calls.append(json.loads(prompt))
                    if len(calls) == 1:
                        return {'dofile': original.replace('chain_count 1', 'chain_count 2'),
                                'netlist_edits': [], 'issues': [deepcopy(first_issue)]}, {}
                    self.assertEqual(len(calls[-1]['existing_issues']), 1)
                    pending = json.loads((target / 'decision_log.json').read_text(encoding='utf-8'))
                    self.assertTrue(pending['summary'].startswith('未发布最终结果'))
                    self.assertIn('remaining_reset', pending['summary'])
                    self.assertIn('additional_issues', kwargs['output_schema']['properties'])
                    if failure == 'unchanged':
                        return {'dofile': original.replace('chain_count 1', 'chain_count 2') + '\n',
                                'netlist_edits': [], 'repair_action': 'no further repair proposed',
                                'additional_issues': [], 'issue_updates': []}, {}
                    extra = {**additional, 'violated_requirement': 'invented requirement' if failure == 'citation' else 'L1'}
                    updated = {**first_issue, 'root_cause': 'corrected cause from the later evidence'}
                    if failure == 'diagnosis_only':
                        return {'dofile': original.replace('chain_count 1', 'chain_count 2'),
                                'netlist_edits': [], 'repair_action': 'correct the causal description only',
                                'additional_issues': [], 'issue_updates': [{'issue_number': 1, 'diagnosis': updated}]}, {}
                    return {'dofile': original.replace('chain_count 1', 'chain_count 2').replace('off_state 0', 'off_state 1'),
                            'netlist_edits': [], 'repair_action': 'release reset', 'additional_issues': [extra],
                            'issue_updates': [] if failure == 'retain_identity' else [{'issue_number': 99 if failure == 'update_index' else 1, 'diagnosis': updated}]}, {}
                def run(script, directory, *args, **kwargs):
                    directory.mkdir(parents=True)
                    (directory / (directory.name + '.log')).write_text('actual test execution', encoding='utf-8')
                    return {'tool_call_id': directory.name, 'exit_status': 'completed'}
                def verify(target, directory, fixed, execution, plan, *args, **kwargs):
                    pending = json.loads((target / 'decision_log.json').read_text(encoding='utf-8'))
                    self.assertIsNone(pending['final_run'])
                    self.assertIn('等待', pending['summary'])
                    self.assertEqual(pending['tool_runs'][-1]['tool_call_id'], directory.name)
                    self.assertTrue(pending['file_changes'])
                    verified.append(deepcopy(plan['issues']))
                    reviews.append(kwargs.get('review_id'))
                    complete = directory.name == 'R3' or (failure == 'diagnosis_only' and reviews[-1] == 'R2_review1')
                    findings = [{'issue_id': f'I{i}', 'attempts': [{'verify': {'resolved': complete}}]}
                                for i, finding in enumerate(plan['issues'], 1)]
                    return findings, {'all_requirements_met': complete, 'summary': 'test verification'}, [], {
                        'independent_checks': {'remaining_reset': complete}, 'rejected_diagnoses': []}
                with patch.object(repair_task, 'run_scan', side_effect=run) as runner, \
                        patch.object(repair_task, 'extract_task_constraints', return_value=({'signals': []}, [])), \
                        patch.object(repair_task, 'inspect_run', return_value=({'checks': {}}, {}, {})), \
                        patch.object(repair_task, 'component_sources', return_value={}), \
                        patch.object(repair_task, 'retrieve', return_value=[]), \
                        patch.object(repair_task, 'verify_round', side_effect=verify), \
                        patch.object(repair_task, 'publish_run') as publish:
                    if valid:
                        repair_task.repair(source, target, time.monotonic(), time.monotonic()+120,
                                           generate, lambda source: ([], []))
                        self.assertEqual(runner.call_count, 2 if failure == 'diagnosis_only' else 3)
                        if failure != 'diagnosis_only':
                            self.assertEqual(verified[-1][1]['violated_requirement'], 'task requirement')
                        else:
                            self.assertEqual(reviews, [None, 'R2_review1'])
                        self.assertEqual(verified[-1][0]['root_cause'], first_issue['root_cause'] if failure == 'retain_identity' else 'corrected cause from the later evidence')
                        self.assertEqual(verified[-1][0]['fix_action'], first_issue['fix_action'])
                        decision = json.loads((target / 'decision_log.json').read_text(encoding='utf-8'))
                        self.assertEqual([len(i['attempts']) for i in decision['issue_resolutions']], [2] if failure == 'diagnosis_only' else [2, 1])
                        self.assertEqual(decision['final_run'], 'R2' if failure == 'diagnosis_only' else 'R3')
                        publish.assert_called_once()
                    elif failure == 'unchanged':
                        self.assertEqual(repair_task.repair(source, target, time.monotonic(), time.monotonic()+120,
                                         generate, lambda source: ([], [])), 1)
                        self.assertEqual(runner.call_count, 2)
                        self.assertEqual(len(calls), 2)
                        decision = json.loads((target / 'decision_log.json').read_text(encoding='utf-8'))
                        self.assertIsNone(decision['final_run'])
                        self.assertIn('停止重复执行', decision['summary'])
                        self.assertTrue((target / 'model_repair_R3.json').is_file())
                        publish.assert_not_called()
                    else:
                        with self.assertRaisesRegex(ValueError, 'invalid or duplicate evidence'):
                            repair_task.repair(source, target, time.monotonic(), time.monotonic()+120,
                                               generate, lambda source: ([], []))
                        self.assertEqual(runner.call_count, 2)
                        publish.assert_not_called()
                    self.assertEqual(len(calls), 2)
                    saved = json.loads((target / ('model_repair_R2_review1.json' if failure == 'diagnosis_only' else 'model_repair_R3.json')).read_text(encoding='utf-8'))
                    self.assertEqual(bool(saved['additional_diagnosis_validation']['rejected_diagnoses']
                        or saved['issue_update_validation']['validated']['rejected_diagnoses']
                        or saved['issue_update_validation']['errors']), failure in {'citation', 'update_index'})

    def test_disproved_permission_claim_does_not_regenerate_whole_plan(self):
        quote = '允许忽略 DFTR-TIE0/DFTR-TIE1。'
        command = 'set_scan_drc_rule_handling {DFTR-TIE1} Ignore'
        body = command + '\nexamine_scan_drc\nexamine_scan_chain\ninsert_dft_logic\n'
        finding = {**issue(command), 'violated_requirement': quote,
                   'category': 'configuration', 'defective_parameter': 'arg1'}
        def scan(script, run, timeout, **kwargs):
            if run.name == 'R2':
                raise RuntimeError('reached repaired Scan without a redundant model call')
            run.mkdir(parents=True)
            (run / 'R1.log').write_text('Recorded initial run', encoding='utf-8')
            return {'tool_call_id': 'R1', 'exit_status': 'error'}
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / 'input', Path(folder) / 'output'
            source.mkdir()
            target.mkdir()
            (source / 'original.dofile').write_text(body, encoding='utf-8')
            (source / 'task_spec.md').write_text(quote, encoding='utf-8')
            supported = {**issue('examine_scan_drc', 'Another original fault'), 'category': 'command',
                         'violated_requirement': ''}
            plan = {'dofile': body, 'netlist_edits': [], 'issues': [finding, supported]}
            with patch.object(repair_task, 'run_scan', side_effect=scan), \
                    patch.object(repair_task, 'extract_task_constraints', return_value=({'signals': []}, [])), \
                    patch.object(repair_task, 'inspect_run', return_value=({'checks': {}}, {}, {})):
                model = Mock(return_value=(deepcopy(plan), {}))
                with self.assertRaisesRegex(RuntimeError, 'reached repaired Scan'):
                    repair_task.repair(source, target, time.monotonic(), time.monotonic()+120,
                                       model, lambda source: ([], []))
                self.assertEqual(model.call_count, 1)
            saved = json.loads((target / 'model_plan.json').read_text(encoding='utf-8'))
            self.assertEqual(len(saved['plan']['issues']), 1)
            self.assertFalse(saved['plan']['rejected_diagnoses'][0]['requires_correction'])

    def test_rejected_non_signal_diagnosis_gets_one_correction_before_scan(self):
        # A chain-count finding has no signal-value gap to trigger correction.
        # Its bad quotation must not make the original finding silently vanish.
        body = 'set_scan_cfg -chain_count 2\nexamine_scan_drc\nexamine_scan_chain\ninsert_dft_logic\n'
        finding = {**issue('set_scan_cfg -chain_count 2', 'wrong chain count'),
                   'violated_requirement': 'invented quote', 'original_source': 'original.dofile',
                   'category': 'configuration'}
        edit = {'path': 'netlist/design.v', 'before': "assign q  = 1'b0;", 'after': "assign q = 1'b1;"}
        plan = {'dofile': body, 'netlist_edits': [edit], 'issues': [finding]}
        calls = []
        raw_prompts, guides = [], []
        def generate(prompt, *args, **kwargs):
            calls.append(json.loads(prompt))
            raw_prompts.append(prompt)
            guides.append(kwargs['tool_guide'])
            fields = kwargs['output_schema']['properties']['issues']['items']['properties']
            self.assertNotIn('description', fields['root_cause'])
            self.assertNotEqual(fields['original_command'], fields['violated_requirement'])
            return deepcopy(plan), {}
        def scan(script, run, timeout, **kwargs):
            run.mkdir(parents=True)
            (run / 'R1.log').write_text('Recorded initial failure', encoding='utf-8')
            return {'tool_call_id': 'R1', 'exit_status': 'error'}
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / 'input', Path(folder) / 'output'
            source.mkdir()
            target.mkdir()
            (source / 'original.dofile').write_text(body, encoding='utf-8')
            (source / 'task_spec.md').write_text('共 3 条链。', encoding='utf-8')
            (source / 'netlist').mkdir()
            netlist = "module top(output q);\nassign q = 1'b0;\nendmodule\n"
            (source / 'netlist/design.v').write_text(netlist, encoding='utf-8')
            with patch.object(repair_task, 'run_scan', side_effect=scan) as runner, \
                    patch.object(repair_task, 'extract_task_constraints', return_value=({'signals': []}, [])), \
                    patch.object(repair_task, 'inspect_run', return_value=({'checks': {}}, {}, {})), \
                    patch.object(repair_task, 'verify_round', side_effect=AssertionError('Invalid diagnosis bypassed correction')):
                with self.assertRaisesRegex(ValueError, 'Diagnosis evidence rejected'):
                    repair_task.repair(source, target, time.monotonic(), time.monotonic()+120,
                                       generate, lambda source: ([], []))
            self.assertEqual(len(calls), 2)
            self.assertTrue(raw_prompts[1].startswith(raw_prompts[0][:-1]))
            self.assertNotIn('original_context', calls[1])
            self.assertNotIn('tool_guide', calls[0])
            self.assertTrue(guides[0])
            self.assertEqual(guides[0], guides[1])
            self.assertEqual(calls[1]['diagnosis_coverage_gaps'], [])
            self.assertIn('violated_requirement', calls[1]['candidate_error'])
            self.assertIn('netlist_edits[0]', calls[1]['candidate_error'])
            self.assertIn('0 exact matches', calls[1]['candidate_error'])
            self.assertEqual(runner.call_count, 1)
            recorded = json.loads((target / 'model_plan.json').read_text(encoding='utf-8'))
            self.assertEqual(len(recorded['diagnostic_attempts']), 2)
            self.assertEqual(recorded['diagnostic_attempts'][0]['plan']['rejected_diagnoses'][0]['issue'], finding)
            originals, modified = repair_task.edited_netlists(source, [{**edit, 'before': "assign q = 1'b0;"}])
            self.assertEqual(originals['netlist/design.v'], netlist)
            self.assertIn("assign q = 1'b1;", modified['netlist/design.v'])
            self.assertEqual((source / 'netlist/design.v').read_text(encoding='utf-8'), netlist)

    def test_task_line_citation_preserves_markdown_and_rejects_invalid_source(self):
        spec = '要求\n- `cold_rst` 为低有效。\n- 保持其他功能。'
        finding = {**issue('set_scan_cfg -chain_count 2'), 'violated_requirement': 'L2-L2',
                   'category': 'configuration', 'original_source': 'original.dofile'}
        plan = {'issues': [finding.copy()]}
        sanitize_diagnoses(plan, SCRIPT, spec)
        self.assertEqual(plan['issues'][0]['violated_requirement'], '- `cold_rst` 为低有效。')
        self.assertEqual(plan['issues'][0]['model_violated_requirement'], 'L2-L2')
        for changes in [{'violated_requirement': 'L0-L99'}, {'original_source': '/output/runs/R1/deliverables/R1.dofile'}]:
            plan = {'issues': [{**finding, **changes}]}
            sanitize_diagnoses(plan, SCRIPT, spec)
            self.assertEqual(plan['issues'], [])

    def test_whitespace_citation_resolves_only_to_unique_actual_text(self):
        task = '必须覆盖\n   扫描单元与门控两类。'
        finding = {**issue('set_scan_cfg -chain_count 2'), 'violated_requirement': '必须覆盖\n扫描单元与门控两类。'}
        plan = {'issues': [finding.copy()]}
        sanitize_diagnoses(plan, SCRIPT, task)
        self.assertEqual(plan['issues'][0]['violated_requirement'], task)
        self.assertEqual(plan['issues'][0]['model_violated_requirement'], finding['violated_requirement'])
        for invalid_task in [task + '\n' + task, task.replace('两类', '一种')]:
            plan = {'issues': [finding.copy()]}
            sanitize_diagnoses(plan, SCRIPT, invalid_task)
            self.assertEqual(plan['issues'], [])

    def test_separate_task_locations_preserve_one_real_source_span(self):
        task = '任务\n- `rst_a` 为复位。\n相关说明\n- 扫描时复位应去激活。\n结束'
        finding = {**issue('set_scan_cfg -chain_count 2'),
                   'category': 'configuration', 'violated_requirement': 'L2; L4'}
        plan = {'issues': [finding.copy()]}
        sanitize_diagnoses(plan, SCRIPT, task)
        self.assertEqual(plan['issues'][0]['violated_requirement'], '\n'.join(task.splitlines()[1:4]))
        self.assertEqual(plan['issues'][0]['model_violated_requirement'], 'L2; L4')
        for quote in ('L0; L4', 'L2; L999', 'L4-L2; L3', 'L2; invented requirement'):
            plan = {'issues': [{**finding, 'violated_requirement': quote}]}
            sanitize_diagnoses(plan, SCRIPT, task)
            self.assertEqual(plan['issues'], [], quote)

    def test_wrong_explicit_usage_requires_its_own_diagnosis(self):
        command = 'set_scan_signal -type scan_enable -port shift_go -usage scan'
        req = {'signals': [{'port': 'shift_go', 'role': 'scan_enable', 'usage': 'all'}]}
        checks = {'task_signal_shift_go_scan_enable': True, 'task_signal_shift_go_usage': False}
        gaps = explicit_diagnosis_gaps(command, req, checks, [])
        self.assertEqual([g['required_field'] for g in gaps], ['usage'])
        self.assertEqual(explicit_diagnosis_gaps(command, req, checks,
                         [{**issue(command), 'defective_parameter': '-usage'}]), [])
        self.assertEqual(explicit_diagnosis_gaps(command.replace(' -usage scan', ''), req, checks, []), [])
        scoped = 'set_current_scan_partition bank\nset_scan_cfg -chain_count 2\n' + command
        self.assertEqual(explicit_diagnosis_gaps(scoped, req, checks,
                         [{**issue(scoped), 'defective_parameter': '-usage'}]), [])

    def test_repeated_original_command_requires_distinguishing_scope(self):
        command = 'set_scan_signal -type scan_enable -port shared -off_state 0 -usage scan'
        wrong = 'set_current_scan_partition right\nset_scan_cfg -chain_count 2\n' + command
        script = 'set_current_scan_partition left\n' + command + '\n' + wrong + '\n'
        plan = sanitize_diagnoses({'issues': [issue(command)]}, script)
        self.assertEqual(plan['issues'], [])
        self.assertIn('2, 5', plan['rejected_diagnoses'][0]['reason'])
        self.assertIn('set_current_scan_partition', plan['rejected_diagnoses'][0]['reason'])
        plan = sanitize_diagnoses({'issues': [issue(wrong)]}, script)
        self.assertEqual(len(plan['issues']), 1)
        self.assertEqual(diagnosis_location(plan['issues'][0], {'original.dofile': script})[1:3], (3, 5))

        # A source line selector binds the actual repeated occurrence, without
        # asking the model to retype every intervening line or using the first match.
        selected = sanitize_diagnoses({'issues': [issue('L2'), issue('L5')]}, script)
        self.assertEqual(len(selected['issues']), 2)
        self.assertEqual([finding['original_command'] for finding in selected['issues']], [command, command])
        self.assertEqual([diagnosis_location(finding, {'original.dofile': script})[1:3]
                          for finding in selected['issues']], [(2, 2), (5, 5)])
        multiline = sanitize_diagnoses({'issues': [issue('L3-L5')]}, script)
        self.assertEqual(multiline['issues'][0]['original_command'], wrong)
        self.assertEqual(diagnosis_location(multiline['issues'][0], {'original.dofile': script})[1:3], (3, 5))
        for invalid in ('L0', 'L6', 'L5-L3'):
            self.assertEqual(sanitize_diagnoses({'issues': [issue(invalid)]}, script)['issues'], [])
        duplicate = sanitize_diagnoses({'issues': [issue('L5'), issue('L5')]}, script)
        self.assertEqual(len(duplicate['issues']), 1)

    def test_one_signal_issue_cannot_cover_two_independent_parameter_faults(self):
        command = 'set_scan_signal -type scan_enable -port shift_go -usage scan -off_state 1'
        req = {'signals': [{'port': 'shift_go', 'role': 'scan_enable', 'usage': 'all', 'off_state': '0'}]}
        checks = {'task_signal_shift_go_scan_enable': False, 'task_signal_shift_go_usage': False}
        usage = {**issue(command), 'defective_parameter': '-usage'}
        polarity = {**issue(command, 'wrong inactive level'), 'defective_parameter': '-off_state'}
        gaps = explicit_diagnosis_gaps(command, req, checks, [usage])
        self.assertEqual([g['required_field'] for g in gaps], ['off_state'])
        self.assertEqual(explicit_diagnosis_gaps(command, req, checks, [usage, polarity]), [])
        combined = {**usage, 'defective_parameter': '-usage, -off_state'}
        plan = sanitize_diagnoses({'issues': [combined]}, command)
        self.assertEqual(plan['issues'], [])
        self.assertIn('one option', plan['rejected_diagnoses'][0]['reason'])

    def test_independent_positional_operands_remain_separate_findings(self):
        command = 'set_scan_drc_rule_handling {TIE0 TIE1} Warning'
        findings = [{**issue(command, 'unrecognized rule names'), 'defective_parameter': 'arg1'},
                    {**issue(command, 'level contradicts the requested suppression'), 'defective_parameter': 'arg2'}]
        plan = sanitize_diagnoses({'issues': findings}, command)
        self.assertEqual(len(plan['issues']), 2)
        self.assertEqual(plan['rejected_diagnoses'], [])

    def test_actual_source_location_and_official_drc_quote_rule(self):
        text = 'module m;\nBUF g7 (.A(a), .Y(b));\nendmodule\n'
        claim = issue('BUF g7 (.A(a), .Y(b));')
        claim.update(original_source='netlist/m.v', category='drc', violated_requirement='', located_object='m/wrong/wrong/g7')
        plan = {'issues': [claim]}
        sanitize_diagnoses(plan, SCRIPT, '完成扫描。', {'netlist/m.v': text})
        self.assertEqual(len(plan['issues']), 1)
        name, start, end, location = diagnosis_location(claim, {'netlist/m.v': text})
        self.assertEqual((name, start, end), ('netlist/m.v', 2, 2))
        self.assertNotIn('wrong', location)
        self.assertIn('BUF g7', location)
        claim['category'] = 'configuration'
        sanitize_diagnoses(plan, SCRIPT, '完成扫描。', {'netlist/m.v': text})
        self.assertEqual(plan['issues'], [])

    def test_masked_signal_defect_cannot_be_silently_fixed(self):
        reset = 'set_scan_signal -type reset -port clear_h -off_state 1'
        mode = 'set_scan_signal -type constant -port mode_pin -constant_value 1'
        req = {'signals': [{'port': 'clear_h', 'role': 'reset', 'off_state': '0'},
                           {'port': 'mode_pin', 'role': 'constant', 'constant_value': '0'}]}
        checks = {'task_signal_clear_h_reset': False, 'task_signal_mode_pin_constant': False}
        mode_issue = {**issue(mode), 'defective_parameter': '-constant_value'}
        reset_issue = {**issue(reset), 'defective_parameter': '-off_state'}
        missing = explicit_diagnosis_gaps(reset+'\n'+mode, req, checks, [mode_issue])
        self.assertEqual([g['original_command'] for g in missing], [reset])
        self.assertEqual(explicit_diagnosis_gaps(reset+'\n'+mode, req, checks, [mode_issue, reset_issue]), [])
        self.assertEqual(explicit_diagnosis_gaps(reset+'\n'+mode, req, dict.fromkeys(checks, True), []), [])

    def test_zero_findings_can_be_verified_without_inventing_a_defect(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            run = target / "runs/R2"
            (run / "reports").mkdir(parents=True)
            (target / "runs/R1").mkdir()
            (target / "runs/R1/R1.log").write_text("original completed", encoding="utf-8")
            (run / "reports/drc.rpt").write_text("Total violations: 0", encoding="utf-8")
            (run / "R2.log").write_text("candidate completed", encoding="utf-8")
            def approve(*args, **kwargs):
                self.assertEqual(kwargs["output_schema"]["properties"]["issues"]["required"], [])
                return {"all_requirements_met": True, "summary": "No original defects", "issues": {}}, {}
            issues, verification, _, _ = verify_round(target, run, 'examine_scan_drc -file "$out_rpt_dir/drc.rpt"',
                {"exit_status": "completed"}, {"issues": []}, SCRIPT, "DRC 检查无违例", "", time.monotonic()+10, "F1", approve)
            self.assertEqual(issues, [])
            self.assertTrue(verification["all_requirements_met"])

    def test_successful_execution_does_not_validate_false_original_defect(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            run = target / "runs/R2"
            (run / "reports").mkdir(parents=True)
            (run / "deliverables").mkdir()
            (target / 'runs/R1').mkdir()
            (target / 'runs/R1/R1.log').write_text('Original execution evidence', encoding='utf-8')
            (run / "reports/drc.rpt").write_text("Total violations: 0", encoding="utf-8")
            (run / "R2.log").write_text("\n".join(f"[INFO] [CMD-0034] @{i}: {c}" for i, c in enumerate(("examine_scan_drc", "examine_scan_chain", "insert_dft_logic"))), encoding="utf-8")
            def verify(*args, **kwargs):
                return {"all_requirements_met": True, "summary": "real output passes",
                        "issues": {f'I{number}': {"issue_number": number, "original_defect_supported": supported, "support_basis": "default is valid" if not supported else "wrong chain count",
                                    "resolved": True, "evidence_id": candidate_evidence(args[0], 'R2.log', '[INFO] [CMD-0034] @0: examine_scan_drc')}
                                   for number, supported in ((3, False), (2, True), (1, False))}}, {}
            workflow = {**issue('insert_dft_logic', 'documented stage order'), 'program_evidence': 'workflow_order'}
            plan = {"issues": [issue("load_lib [file join $input_dir lib sky130.lib]", "path renaming"), issue("set_scan_cfg -chain_count 2", "wrong chain count"), workflow]}
            found, _, _, feedback = verify_round(target, run, 'examine_scan_drc -file "$out_rpt_dir/drc.rpt"',
                {"exit_status": "completed"}, plan, SCRIPT, "DRC 检查无违例", "", time.monotonic() + 10, "F1", verify)
            self.assertEqual([i["issue_id"] for i in found], ["I2", "I3"])
            self.assertTrue(found[-1]['attempts'][-1]['verify']['resolved'])
            self.assertIn('Program-derived', found[-1]['attempts'][-1]['verify']['support_basis'])
            self.assertEqual(feedback["rejected_diagnoses"][0]["issue_id"], "I1")
            def repeated_identity(*args, **kwargs):
                response, usage = verify(*args, **kwargs)
                for verdict in response['issues'].values():
                    verdict['issue_number'] = 1
                return response, usage
            with self.assertRaisesRegex(ValueError, 'issue identities'):
                verify_round(target, run, 'examine_scan_drc -file "$out_rpt_dir/drc.rpt"',
                    {"exit_status": "completed"}, plan, SCRIPT, "DRC 检查无违例", "",
                    time.monotonic() + 10, "F1", repeated_identity)

    def test_duplicate_fact_is_not_scored_twice_or_allowed_to_hide_an_unsupported_root(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            run = target / 'runs/R2'
            (run / 'reports').mkdir(parents=True)
            (target / 'runs/R1').mkdir()
            (target / 'runs/R1/R1.log').write_text('original execution', encoding='utf-8')
            (run / 'reports/drc.rpt').write_text('Total violations: 0', encoding='utf-8')
            (run / 'R2.log').write_text('[INFO] candidate evidence', encoding='utf-8')
            plan = {'issues': [issue('set_scan_cfg -chain_count 2', 'wrong value'),
                               issue('set_scan_cfg -chain_count 2', 'required value absent')]}
            for duplicate, earlier_supported, different_parameters in ((1, True, False), (2, True, False), (1, False, False), (1, True, True)):
                plan['issues'][0]['defective_parameter'] = '-usage'
                plan['issues'][1]['defective_parameter'] = '-off_state' if different_parameters else '-usage'
                with self.subTest(duplicate=duplicate, earlier_supported=earlier_supported, different_parameters=different_parameters):
                    def verify(*args, **kwargs):
                        schema = kwargs['output_schema']['properties']['issues']['properties']
                        self.assertEqual(schema['I1']['properties']['duplicate_of']['enum'], [0])
                        self.assertEqual(schema['I2']['properties']['duplicate_of']['enum'], [0] if different_parameters else [0, 1])
                        return {'all_requirements_met': True, 'summary': 'candidate complete', 'issues': {
                            'I1': {'issue_number': 1, 'resolved': True, 'original_defect_supported': earlier_supported, 'duplicate_of': 0,
                             'support_basis': 'original wrong value', 'evidence_id': candidate_evidence(args[0], 'R2.log', '[INFO] candidate evidence')},
                            'I2': {'issue_number': 2, 'resolved': True, 'original_defect_supported': True, 'duplicate_of': duplicate,
                             'support_basis': 'same substitution fixes both descriptions', 'evidence_id': ''}}}, {}
                    findings, _, _, feedback = verify_round(target, run,
                        'examine_scan_drc -file "$out_rpt_dir/drc.rpt"', {'exit_status': 'completed'},
                        plan, SCRIPT, 'DRC 检查无违例', '', time.monotonic()+10, 'F1', verify)
                    if duplicate == 1 and earlier_supported and not different_parameters:
                        self.assertEqual([i['issue_id'] for i in findings], ['I1'])
                        self.assertEqual(feedback['rejected_diagnoses'][0]['duplicate_of_plan_issue'], 'I1')
                    else:
                        # A bad duplicate pointer cannot silently erase a real finding.
                        later = next(i for i in findings if i['issue_id'] == 'I2')
                        self.assertFalse(later['attempts'][-1]['verify']['resolved'])

    def test_evidence_selection_binds_source_line_and_text(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            run = target / 'runs/R2'
            (run / 'reports').mkdir(parents=True)
            (target / 'runs/R1').mkdir()
            (target / 'runs/R1/R1.log').write_text('original execution', encoding='utf-8')
            (run / 'reports/drc.rpt').write_text('Total violations: 0', encoding='utf-8')
            quoted = '[INFO] [CMD-0034] @1: set_scan_cfg -chain_count 2'
            (run / 'R2.log').write_text('[INFO] loading library\n[INFO] presenting design\n' + quoted, encoding='utf-8')
            for correct in (True, False):
                calls = []
                def verify(prompt, *args, **kwargs):
                    calls.append(json.loads(prompt))
                    selected = candidate_evidence(prompt, 'R2.log', quoted) if correct and len(calls) == 2 else '@1'
                    fields = kwargs['output_schema']['properties']['issues']['properties']['I1']['properties']
                    self.assertNotIn('source', fields)
                    self.assertNotIn('line_start', fields)
                    self.assertNotIn('line_start_text', fields)
                    return {'all_requirements_met': True, 'summary': 'candidate complete', 'issues': {
                        'I1': {'issue_number': 1, 'resolved': True, 'original_defect_supported': True, 'duplicate_of': 0,
                         'support_basis': 'chain count repaired', 'evidence_id': selected}}}, {}
                findings, _, _, _ = verify_round(target, run,
                    'examine_scan_drc -file "$out_rpt_dir/drc.rpt"', {'exit_status': 'completed'},
                    {'issues': [issue('set_scan_cfg -chain_count 2')]}, SCRIPT, 'DRC 检查无违例', '',
                    time.monotonic()+120, 'F1', verify, review_id=None if correct else 'R2_review1')
                self.assertEqual(len(calls), 2)
                problem = calls[1]['invalid_references'][0]
                self.assertEqual(problem['evidence_id'], '@1')
                result = findings[0]['attempts'][0]['verify']
                self.assertEqual(result['resolved'], correct)
                self.assertEqual(result['excerpt'], quoted if correct else '')
                if correct:
                    self.assertEqual(result['source'], 'runs/R2/R2.log')
                    self.assertEqual(result['locator'], 'L3-L3')
            self.assertTrue((target / 'verification_R2.json').is_file())
            self.assertTrue((target / 'verification_R2_review1.json').is_file())
            saved = json.loads((target / 'verification_R2.json').read_text())
            self.assertEqual(saved['verification']['issues'][0]['line_start'], 3)
            selected = saved['attempts'][-1]['verification']['issues']['I1']['evidence_id']
            self.assertEqual(saved['attempts'][-1]['selected_evidence'][selected]['line_start_text'], quoted)

    def test_keeps_exact_scan_evidence(self):
        plan = {"issues": [issue("set_scan_cfg -chain_count 2")]}
        sanitize_diagnoses(plan, SCRIPT)
        self.assertEqual(len(plan["issues"]), 1)
        self.assertEqual(plan["rejected_diagnoses"], [])

    def test_rejects_invented_requirement_without_erasing_audit(self):
        claim = issue("set_scan_cfg -chain_count 2", "unrequired extra control")
        claim["violated_requirement"] = "必须额外固定一个输入"
        plan = {"issues": [claim]}
        sanitize_diagnoses(plan, SCRIPT, "共 2 条链。")
        self.assertEqual(plan["issues"], [])
        self.assertIn("exact task quote", plan["rejected_diagnoses"][0]["reason"])

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

    def test_rejected_finding_verdict_is_reviewed_without_overriding_a_real_failure(self):
        for final_approval in (True, False):
            with self.subTest(final_approval=final_approval), tempfile.TemporaryDirectory() as folder:
                target = Path(folder)
                run = target / 'runs/R2'
                run.mkdir(parents=True)
                (target / 'runs/R1').mkdir()
                (target / 'runs/R1/R1.log').write_text('original evidence', encoding='utf-8')
                acceptance = {'checks': {'execution_completed': True}, 'missing_outputs': []}
                (run / 'acceptance.json').write_text(json.dumps(acceptance), encoding='utf-8')
                prompts = []
                def review(prompt, *args, **kwargs):
                    prompts.append(json.loads(prompt))
                    return {'all_requirements_met': final_approval if len(prompts) == 2 else False,
                            'summary': 'Rejected hypothesis only' if final_approval else 'A separate real requirement remains',
                            'issues': {'I1': {'issue_number': 1, 'original_defect_supported': False,
                                             'resolved': False, 'duplicate_of': 0, 'evidence_id': '',
                                             'support_basis': 'Original effective value already matches.'}}}, {}
                with patch.object(repair_task, 'inspect_run', return_value=(acceptance, {}, {})):
                    found, verdict, _, feedback = verify_round(target, run, 'exit', {'exit_status': 'completed'},
                        {'issues': [issue('set_scan_cfg -chain_count 2')]}, SCRIPT, '', '',
                        time.monotonic() + 120, 'F1', review)
                self.assertEqual(len(prompts), 2)
                self.assertEqual(prompts[1]['rejected_findings_are_automatically_omitted'], [1])
                self.assertTrue(prompts[1]['overall_judgment_feedback'])
                self.assertEqual(verdict['all_requirements_met'], final_approval)
                self.assertEqual(found, [])
                self.assertEqual(len(feedback['rejected_diagnoses']), 1)

    def test_missing_verifier_reference_is_unresolved_not_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            run = target / "runs/R2"
            (run / "reports").mkdir(parents=True)
            (run / "deliverables").mkdir()
            (target / 'runs/R1/reports').mkdir(parents=True)
            (target / 'runs/R1/R1.log').write_text('Original execution evidence', encoding='utf-8')
            (target / 'runs/R1/reports/cfg.rpt').write_text('add_lockup True\n', encoding='utf-8')
            (run / "reports/drc.rpt").write_text("Total violations: 0", encoding="utf-8")
            (run / "R2.log").write_text("real test log", encoding="utf-8")
            (target / 'diffs').mkdir()
            diff = '--- original\n+++ candidate\n-old\n+new\n'
            (target / 'diffs/dofile_R1_to_R2.diff').write_text(diff, encoding='utf-8')
            (target / 'decision_log.json').write_text('{"final_run": null, "file_changes": []}', encoding='utf-8')
            calls = []
            def verify(*args, **kwargs):
                calls.append(args[0])
                self.assertEqual(kwargs['tool_guide'], 'shared tool reference')
                return {"all_requirements_met": True, "summary": "model approval lacks evidence",
                        "issues": {'I1': {"issue_number": 1, "resolved": True, "original_defect_supported": True, "support_basis": "prose only"}}}, {}
            found, _, _, _ = verify_round(target, run, 'examine_scan_drc -file "$out_rpt_dir/drc.rpt"',
                {"exit_status": "completed"}, {"issues": [issue("set_scan_cfg -chain_count 2")]},
                SCRIPT, "DRC 检查无违例", "shared tool reference", time.monotonic() + 120, "F1", verify)
            self.assertFalse(found[0]["attempts"][0]["verify"]["resolved"])
            self.assertEqual(len(calls), 2)
            self.assertTrue(calls[1].startswith(calls[0][:-1]))
            context = json.loads(calls[1])
            self.assertNotIn('original_verification_context', context)
            self.assertNotIn('tool_guide', context)
            self.assertEqual(context['candidate_sources'], list(context['actual_files']))
            self.assertEqual(context['runner_evidence']['diffs/dofile_R1_to_R2.diff'], {'text': diff, 'truncated': False})
            self.assertIsNone(json.loads(context['runner_evidence']['decision_log.json']['text'])['final_run'])
            self.assertNotIn('diffs/dofile_R1_to_R2.diff', context['candidate_sources'])
            self.assertEqual(context['original_evidence']['reports']['cfg.rpt'], 'add_lockup True\n')
            self.assertTrue(context['invalid_references'])

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
