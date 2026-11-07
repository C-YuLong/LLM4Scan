"""Task-book contracts and report mutations, no live model or tool calls."""
import json
import re
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "submission"))
from acceptance import _task_requirements, _task_checks, inspect_run, merge_task_constraints, extract_task_constraints, membership_polarity
from model_contract import ModelResponseError
from scan_runner import declared_artifacts

ROOT = Path(__file__).resolve().parents[1]


def requirements(case):
    return _task_requirements(ROOT / f"official/extracted/public_cases/task_2/case{case}/input/task_spec.md")


class AcceptanceTests(unittest.TestCase):
    def test_membership_exception_polarity_is_checked_against_cited_sentence(self):
        spec = '对 Hinstance：`top/u_a` 下的所有可扫描单元，除了 `top/u_a/keep*` 可匹配到的扫描单元，其余均不允许参与扫描链串链。'
        def extracted(outer, inner):
            return {'constraints': [{'quote': spec, 'kind': 'scan_membership', 'target': 'top/u_a', 'value': outer},
                                    {'quote': spec, 'kind': 'scan_membership', 'target': 'top/u_a/keep*', 'value': inner}],
                    'uncovered': []}
        rules = merge_task_constraints(spec, extracted('exclude', 'include'))['scan_membership']
        self.assertEqual([(r['target'], r['value']) for r in rules], [('top/u_a', 'exclude'), ('top/u_a/keep*', 'include')])
        with self.assertRaisesRegex(ValueError, 'polarity'):
            merge_task_constraints(spec, extracted('include', 'exclude'))
        for sentence, target, expected in [
                ('`u_a` 下的单元除了 `u_a/b*` 以外都需要参与扫描链。', 'u_a', 'include'),
                ('`u_a` 下的单元除了 `u_a/b*` 以外都需要参与扫描链。', 'u_a/b*', 'exclude'),
                ('u_core 参与扫描，u_dbg 除外，其余均串入扫描链。', 'u_dbg', 'exclude'),
                ('该模块 u_x 需标记为不可扫描。', 'u_x', 'exclude'),
                ('模块 u_m 的功能描述。', 'u_m', None)]:
            with self.subTest(sentence=sentence, target=target):
                self.assertEqual(membership_polarity(sentence, target), expected)

    def test_plain_delivery_line_is_checked_without_a_markdown_output_heading(self):
        task = '读取 lib/sky130.lib 与 netlist/design.v。\n交付 post_scan.v、post_scan.ctl、post_scan.def；导出 drc.rpt。\n不得导出 forbidden.v。'
        requirements = _task_requirements(task)
        self.assertEqual(requirements['outputs'], ['post_scan.v', 'post_scan.ctl', 'post_scan.def', 'drc.rpt'])
        with tempfile.TemporaryDirectory() as folder:
            _, _, missing = _task_checks(Path(folder), {}, requirements)
            self.assertIn('post_scan.def', missing)

    def test_membership_accepts_only_exact_members_of_an_explicit_numeric_range(self):
        spec = '只有顶层 keep_0 到 keep_4 这 5 个寄存器不参与扫描。'
        def extracted(target):
            return {'constraints': [{'quote': spec, 'kind': 'scan_membership', 'target': target,
                                      'value': 'exclude'}], 'uncovered': []}
        for target in ('keep_0', 'keep_1', 'keep_2', 'keep_3', 'keep_4'):
            rules = merge_task_constraints(spec, extracted(target))['scan_membership']
            self.assertEqual(rules, [{'target': target, 'value': 'exclude'}])
        for target in ('keep_5', 'keep_*', 'kept_2', 'keep_02', 'keep_0 到 keep_4'):
            with self.assertRaises(ValueError):
                merge_task_constraints(spec, extracted(target))
        spec = 'old_keep_0 到 keep_4 不参与扫描。'
        with self.assertRaises(ValueError):
            merge_task_constraints(spec, extracted('keep_2'))

    def test_abstract_exclusion_uses_actual_hierarchy_before_repairs(self):
        spec = '调试状态机不纳入扫描，普通数据存储寄存器仍须扫描。'
        outline = [{'selector': '[[H3]]', 'path': 'bridge/state_machine', 'type': 'debug_fsm'}]
        extracted = {'constraints': [{'quote': spec, 'kind': 'scan_membership', 'target': '[[H3]]', 'value': 'exclude'}], 'uncovered': []}
        rule = merge_task_constraints(spec, extracted, design_context=outline)['scan_membership'][0]
        self.assertEqual(rule['target'], 'bridge/state_machine')
        self.assertEqual(rule['task_quote'], spec)
        self.assertEqual(rule['source_selector'], '[[H3]]')
        with self.assertRaises(ValueError):
            merge_task_constraints(spec, extracted)
        extracted['constraints'][0]['target'] = '[[H999]]'
        with self.assertRaises(ValueError):
            merge_task_constraints(spec, extracted, design_context=outline)

    def test_exclusion_audit_requires_all_objects_not_scannable_subset(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            (run / 'reports').mkdir()
            (run / (run.name + '.log')).write_text('')
            (run / 'reports/objects.rpt').write_text('Name Type Length InstanceName SiSoClocks ClockEdge ObjState\nr dff 1 tap/r user_defined_nonscannable\n')
            req = _task_requirements('完成插链。')
            req['scan_membership'] = [{'target': 'tap', 'value': 'exclude'}]
            script = 'set_scan_element false {tap}\nrpt_scan_element -type scannable -file "$out_rpt_dir/objects.rpt"\n'
            result, _, _ = inspect_run(run, script, {'exit_status': 'completed'}, full_insertion=True, task_requirements=req)
            self.assertFalse(result['checks']['only_task_authorized_ff_exclusions'])
            result, _, _ = inspect_run(run, script.replace('-type scannable', '-type all'),
                {'exit_status': 'completed'}, full_insertion=True, task_requirements=req)
            self.assertTrue(result['checks']['only_task_authorized_ff_exclusions'])

    def test_pseudo_clock_failure_distinguishes_missing_report_from_wrong_object(self):
        req = _task_requirements('检查实际伪主输入时钟。')
        req['floating_clock_domains'] = True
        context = [{'path': 'block/gate', 'unconnected_pins': ['CLK'], 'output_pins': ['Q']}]
        reports = {'signals.rpt': 'SignalType\nblock/gate/Q 0 clock\n'}
        details = {}
        def check():
            return _task_checks(ROOT, reports, req, design_context=context, check_details=details)[0]
        self.assertFalse(check()['task_pseudo_clocks_match_floating_gate_outputs'])
        detail = details['task_pseudo_clocks_match_floating_gate_outputs']
        self.assertFalse(detail['pseudo_report_present'])
        self.assertEqual(detail['expected_pins'], ['block/gate/Q'])
        self.assertEqual(detail['missing_clock_declarations'], [])
        reports['pseudo.rpt'] = 'PseudoPIName\nblock/wrong/Q(O)\n'
        self.assertFalse(check()['task_pseudo_clocks_match_floating_gate_outputs'])
        self.assertTrue(details['task_pseudo_clocks_match_floating_gate_outputs']['pseudo_report_present'])
        reports['pseudo.rpt'] = 'PseudoPIName\nblock/gate/Q(O)\n'
        self.assertTrue(check()['task_pseudo_clocks_match_floating_gate_outputs'])

    def test_explicit_prose_outputs_and_cell_mapping_are_frozen_constraints(self):
        spec = '输出 result.ctl。将 lib__DFF 映射为 SFF，同一库。'
        extracted = {'constraints': [
            {'quote': spec, 'kind': 'output_file', 'target': '', 'value': 'result.ctl'},
            {'quote': spec, 'kind': 'cell_mapping', 'target': 'lib__DFF', 'value': 'SFF'}], 'uncovered': []}
        req = merge_task_constraints(spec, extracted)
        self.assertIn('result.ctl', req['outputs'])
        self.assertEqual(req['cell_mappings'], {'lib__DFF': 'SFF'})
        extracted['constraints'][0]['value'] = 'invented.ctl'
        with self.assertRaisesRegex(ValueError, 'literal task-specified'):
            merge_task_constraints(spec, extracted)

    def test_source_clock_count_does_not_imply_an_internal_clock_policy(self):
        quote = '设计包含两个顶层时钟域，通过 Clock Mux 切换。'
        item = {'quote': quote, 'kind': 'config', 'target': 'internal_clocks', 'value': 'multi'}
        with self.assertRaisesRegex(ValueError, 'do not specify'):
            merge_task_constraints(quote, {'constraints': [item], 'uncovered': []})
        explicit = '内部时钟设置为 none。'
        req = merge_task_constraints(explicit, {'constraints': [
            {**item, 'quote': explicit, 'value': 'none'}], 'uncovered': []})
        self.assertEqual(req['config']['internal_clocks'], 'none')

        # A real extraction repeated multi after vague feedback, although the
        # task parser had already established none from a separate requirement.
        topology = '内部门控时钟配置为伪主输入，各自构成独立时钟域。'
        spec = topology + '\n不使用内部门控时钟（伪主输入声明者除外）作为扫描链时钟'
        with self.assertRaisesRegex(ValueError, "'multi'.*'none'.*already_parsed_requirements"):
            merge_task_constraints(spec, {'constraints': [{**item, 'quote': topology}], 'uncovered': []})
        req = merge_task_constraints(spec, {'constraints': [], 'uncovered': [topology]})
        self.assertEqual(req['config']['internal_clocks'], 'none')

    def test_partial_signal_and_wrapper_facts_preserve_stronger_task_requirements(self):
        spec = '复位端口 `cold_rst`。\n端口 `payload` 必须使用输入提供的 dedicated wrapper cell。'
        extracted = {'constraints': [
            {'quote': '复位端口 `cold_rst`。', 'kind': 'reset', 'target': 'cold_rst', 'value': ''},
            {'quote': spec.splitlines()[1], 'kind': 'wrapper_port', 'target': 'payload', 'value': 'dedicated'}], 'uncovered': []}
        req = merge_task_constraints(spec, extracted)
        self.assertEqual(next(s for s in req['signals'] if s['port'] == 'cold_rst'), {'port': 'cold_rst', 'role': 'reset'})
        self.assertEqual(req['wrapper_ports'][0]['cell_type'], 'user_defined')
        extracted['constraints'][1]['value'] = 'shared'
        with self.assertRaises(ValueError):
            merge_task_constraints(spec, extracted)

    def test_multiple_extraction_errors_and_bad_json_shape_use_existing_correction(self):
        spec = '共 4 条链。DRC 检查无违例。'
        with self.assertRaises(ValueError) as caught:
            merge_task_constraints(spec, {'constraints': [
                {'quote': spec, 'kind': 'chain_count', 'target': '', 'value': '2'},
                {'quote': spec, 'kind': 'drc_zero', 'target': '', 'value': 'false'}], 'uncovered': []})
        self.assertIn('constraints[0]', str(caught.exception))
        self.assertIn('constraints[1]', str(caught.exception))
        calls = []
        def extract(prompt, timeout, instruction, **kwargs):
            calls.append(prompt)
            if len(calls) == 1:
                raise ModelResponseError('invalid kind', {'constraints': [{'kind': 'uncovered'}]}, {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2})
            self.assertIn('invalid kind', prompt)
            return {'constraints': [], 'uncovered': []}, {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2}
        with tempfile.TemporaryDirectory() as folder:
            req, _ = extract_task_constraints(spec, extract, 120, Path(folder))
            self.assertEqual(req['chain_count'], 4)
            self.assertTrue(req['drc_zero'])
            record = json.loads((Path(folder) / 'task_contract.json').read_text(encoding='utf-8'))
            self.assertEqual(len(record['attempts']), 2)
            self.assertEqual(record['usage']['total_tokens'], 4)

    def test_braced_tcl_artifact_variables_match_bare_variables(self):
        bare = 'dump_netlist -file $out_res_dir/chip.v\nrpt_scan_chain -file $out_rpt_dir/chain.rpt'
        braced = bare.replace('$out_res_dir', '${out_res_dir}').replace('$out_rpt_dir', '${out_rpt_dir}')
        self.assertEqual(declared_artifacts(braced), declared_artifacts(bare))
        self.assertEqual(declared_artifacts(braced), ['deliverables/chip.v', 'reports/chain.rpt'])

    def test_file_join_reports_reach_original_effective_setting_checks(self):
        script = ('rpt_scan_cfg -file [file join $out_rpt_dir chip_cfg.rpt]\n'
                  'rpt_scan_signal -file [file join ${out_rpt_dir} chip_signal.rpt]\n'
                  'dump_netlist -file [file join $out_res_dir chip.v]\n')
        self.assertEqual(declared_artifacts(script),
                         ['deliverables/chip.v', 'reports/chip_cfg.rpt', 'reports/chip_signal.rpt'])
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder) / 'R1'
            (run / 'reports').mkdir(parents=True)
            (run / 'deliverables').mkdir()
            (run / 'R1.log').write_text('original completed', encoding='utf-8')
            (run / 'reports/chip_cfg.rpt').write_text('ScanConfigurationParameter Value\nadd_lockup True\n', encoding='utf-8')
            (run / 'reports/chip_signal.rpt').write_text('SignalType\nshift pre_existing scan_enable(spec) 0 all Default_Partition\n', encoding='utf-8')
            (run / 'deliverables/chip.v').write_text('module chip; endmodule\n', encoding='utf-8')
            req = _task_requirements('完成扫描配置。')
            req['config'] = {'add_lockup': 'True'}
            req['signals'] = [{'port': 'shift', 'role': 'scan_enable', 'off_state': '0', 'usage': 'all'}]
            result, artifacts, _ = inspect_run(run, script, {'exit_status': 'completed'},
                                                full_insertion=True, task_requirements=req)
            self.assertTrue(result['checks']['task_config_add_lockup'])
            self.assertTrue(result['checks']['task_signal_shift_scan_enable'])
            self.assertTrue(result['checks']['task_signal_shift_usage'])
            self.assertIn('reports/chip_cfg.rpt', artifacts)
            (run / 'reports/chip_signal.rpt').unlink()
            result, _, _ = inspect_run(run, script, {'exit_status': 'completed'},
                                       full_insertion=True, task_requirements=req)
            self.assertFalse(result['checks']['declared_outputs_present'])
            self.assertIn('reports/chip_signal.rpt', result['missing_outputs'])

    def test_explicit_scan_usage_and_optional_functional_reset(self):
        spec = '扫描使能端口 `shift_go` 必须同时控制扫描寄存器和时钟门控。'
        req = merge_task_constraints(spec, {'constraints': [
            {'quote': spec, 'kind': 'scan_enable_usage', 'target': 'shift_go', 'value': 'all'}], 'uncovered': []})
        self.assertEqual(req['signals'], [{'port': 'shift_go', 'role': 'scan_enable', 'usage': 'all'}])
        optional = '`warm_reset` 是低有效功能复位，但无需配置 reset 扫描信号。'
        with self.assertRaisesRegex(ValueError, 'not required'):
            merge_task_constraints(optional, {'constraints': [
                {'quote': optional, 'kind': 'reset', 'target': 'warm_reset', 'value': 'active_low'}], 'uncovered': []})

    def test_clock_gating_role_cannot_be_reinterpreted_as_constant(self):
        spec = "`bypass_test` 为高有效门控测试旁路（clock_gating usage），扫描时所有门必须打开。"
        req = _task_requirements(spec)
        self.assertEqual(req["signals"], [{"port": "bypass_test", "role": "scan_enable", "usage": "clock_gating", "off_state": "0"}])
        with self.assertRaisesRegex(ValueError, "role conflicts"):
            merge_task_constraints(spec, {"constraints": [{"quote": spec, "kind": "constant", "target": "bypass_test", "value": "1"}], "uncovered": []})
        reports = {"signals.rpt": "SignalType\nbypass_test pre_existing scan_enable(spec) 0 clock_gating"}
        checks, _, _ = _task_checks(ROOT, reports, req)
        self.assertTrue(checks["task_signal_bypass_test_scan_enable"])
        self.assertTrue(checks["task_signal_bypass_test_usage"])
        self.assertNotIn("task_chain_enable_bypass_test", checks)
        reports["signals.rpt"] = reports["signals.rpt"].replace("clock_gating", "scan")
        checks, _, _ = _task_checks(ROOT, reports, req)
        self.assertFalse(checks["task_signal_bypass_test_usage"])

    def test_wrapper_scoped_counts_cannot_become_global_counts(self):
        spec = "wrapper chain 数目要求为 4；unwrapper chain 数目要求为 48。"
        req = merge_task_constraints(spec, {"constraints": [], "uncovered": []})
        self.assertEqual(req["wrapper"], {"wrapper_count": 4, "internal_count": 48})
        self.assertIsNone(req["chain_count"])
        with self.assertRaisesRegex(ValueError, "empty target"):
            merge_task_constraints(spec, {"constraints": [
                {"quote": spec, "kind": "chain_count", "target": "wrapper", "value": "4"}], "uncovered": []})

    def test_wrapper_controls_keep_their_own_signal_roles(self):
        spec = "wrapper 时钟 wclk 的 off_state 为 0；输入 shift enable go 为高有效。"
        req = merge_task_constraints(spec, {"constraints": [
            {"quote": spec, "kind": "wrapper_clock", "target": "wclk", "value": "inactive_0"},
            {"quote": spec, "kind": "wrp_in_shift_en", "target": "go", "value": "active_high"}], "uncovered": []})
        reports = {"signals.rpt": "SignalType\nwclk pre_existing wrapper_clock 0\ngo pre_existing wrp_in_shift_en 0"}
        checks, _, _ = _task_checks(ROOT, reports, req)
        self.assertTrue(checks["task_signal_wclk_wrapper_clock"])
        self.assertTrue(checks["task_signal_go_wrp_in_shift_en"])
        self.assertFalse(any(key.startswith("task_chain_enable") for key in checks))
        reports["signals.rpt"] = reports["signals.rpt"].replace("wrapper_clock", "clock")
        checks, _, _ = _task_checks(ROOT, reports, req)
        self.assertFalse(checks["task_signal_wclk_wrapper_clock"])

    def test_live_prose_contract_rejects_overlong_chain_without_changing_count(self):
        contract = json.loads((ROOT / "dev/results/capability_prose06_h_prose/task_contract.json").read_text(encoding="utf-8"))
        base = ROOT / "dev/results/capability_prose04_h_prose/final_results"
        reports = {p.name: p.read_text(encoding="utf-8") for p in (base / "reports").glob("*.rpt")}
        checks, _, _ = _task_checks(base, reports, contract["requirements"])
        self.assertTrue(checks["task_chain_count"])
        self.assertTrue(checks["task_max_chain_length"])
        changed, count = re.subn(r"(?m)^(I\s+\d+\s+)4(\s+)", r"\g<1>5\2", reports["scan_chain.rpt"], count=1)
        self.assertEqual(count, 1)
        reports["scan_chain.rpt"] = changed
        checks, _, _ = _task_checks(base, reports, contract["requirements"])
        self.assertTrue(checks["task_chain_count"])
        self.assertFalse(checks["task_max_chain_length"])

    def test_invalid_quote_gets_one_bounded_correction_without_weakening(self):
        spec = "DRC 检查无违例；\n不改变功能。"
        calls = []
        def extract(prompt, timeout, instruction, **kwargs):
            calls.append(prompt)
            return {"constraints": [{"line_start": 1, "line_end": 1, "kind": "drc_zero", "target": "", "value": "true"}],
                    "uncovered": [{"line_start": 9 if len(calls) == 1 else 2, "line_end": 9 if len(calls) == 1 else 2}]}, {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
        with tempfile.TemporaryDirectory() as directory:
            req, uncovered = extract_task_constraints(spec, extract, 120, Path(directory))
            self.assertTrue(req["drc_zero"])
            self.assertEqual(len(calls), 2)
            self.assertTrue(calls[1].startswith(calls[0][:-1]))
            self.assertEqual(uncovered, ["不改变功能。"])
            record = json.loads((Path(directory) / "task_contract.json").read_text(encoding="utf-8"))
            self.assertIn("validation_error", record["attempts"][0])
            self.assertEqual(record["usage"]["total_tokens"], 4)

        # Both failures occurred in a real response. Report them together, not
        # range-only first and then semantic failure after the sole correction.
        calls.clear()
        spec = '设计是单时钟域。\n必须输出全部日志。'
        def mixed_errors(prompt, timeout, instruction, **kwargs):
            calls.append(prompt)
            if len(calls) == 1:
                return {'constraints': [{'kind': 'config', 'target': 'internal_clocks', 'value': 'single',
                                         'line_start': 1, 'line_end': 1}],
                        'uncovered': [{'line_start': 2, 'line_end': 1}]}, {}
            self.assertIn('line_start <= line_end', prompt)
            self.assertIn('do not specify an internal_clocks policy', prompt)
            return {'constraints': [], 'uncovered': [{'line_start': 2, 'line_end': 2}]}, {}
        with tempfile.TemporaryDirectory() as folder:
            req, uncovered = extract_task_constraints(spec, mixed_errors, 120, Path(folder))
            self.assertNotIn('internal_clocks', req['config'])
            self.assertEqual(uncovered, ['必须输出全部日志。'])
            self.assertEqual(len(calls), 2)
            calls.clear()
            with self.assertRaises(ValueError):
                extract_task_constraints(spec, extract, 10, Path(folder))
            self.assertEqual(len(calls), 1)

    def test_signal_polarity_is_not_confused_with_inactive_level(self):
        req = merge_task_constraints("高有效 shift，低有效 reset。", {"constraints": [
            {"quote": "高有效 shift", "kind": "scan_enable", "target": "shift", "value": "active_high"},
            {"quote": "低有效 reset", "kind": "reset", "target": "reset", "value": "active_low"}], "uncovered": []})
        self.assertEqual([(s["port"], s["off_state"]) for s in req["signals"]], [("shift", "0"), ("reset", "1")])

    def test_task_only_constraints_supplement_prose_and_reject_relaxation(self):
        spec = "扫描链总数必须等于 3。DRC 违例数必须为 0。"
        extracted = {"constraints": [
            {"quote": "扫描链总数必须等于 3。", "kind": "chain_count", "target": "", "value": "3"},
            {"quote": "DRC 违例数必须为 0。", "kind": "drc_zero", "target": "", "value": "true"}], "uncovered": []}
        req = merge_task_constraints(spec, extracted)
        checks, _, _ = _task_checks(ROOT, {"chain.rpt": "ChainProperty ScanEnable\nI 1 8 si so se clk Default_Partition tool_created",
                                           "drc.rpt": "Total violations: 1"}, req)
        self.assertFalse(checks["task_chain_count"])
        self.assertFalse(checks["task_drc_zero"])
        with self.assertRaises(ValueError):
            merge_task_constraints("共 4 条链。" + spec, extracted)
        with self.assertRaises(ValueError):
            merge_task_constraints("不同任务", extracted)

    def test_constraint_units_distinguish_chain_number_and_per_chain_cells(self):
        spec = "至多 6 条扫描链，每条最多 11 个单元。"
        req = merge_task_constraints(spec, {"constraints": [
            {"quote": "至多 6 条扫描链", "kind": "max_chain_count", "target": "", "value": "6"},
            {"quote": "每条最多 11 个单元", "kind": "max_cells_per_chain", "target": "", "value": "11"}], "uncovered": []})
        self.assertEqual((req["max_count"], req["max_length"]), (6, 11))
        checks, _, _ = _task_checks(ROOT, {"chain.rpt": "ChainProperty ScanEnable\nI 1 12 si so se clk Default_Partition tool_created"}, req)
        self.assertTrue(checks["task_max_chain_count"])
        self.assertFalse(checks["task_max_chain_length"])

    def test_partition_table_without_partition_suffix_keeps_all_rows(self):
        spec = (ROOT / "dev/cases/dual_clock/input/task_spec.md").read_text(encoding="utf-8")
        req = _task_requirements(spec)
        self.assertEqual(req["partitions"], [
            {"name": "ingress", "clock": "tick_a", "chain_count": 2, "max_length": 4, "scan_enable": "shift_a"},
            {"name": "egress", "clock": "tick_b", "chain_count": 1, "max_length": 4, "scan_enable": "shift_b"},
        ])

    def test_partition_table_uses_header_not_hierarchy_column(self):
        text = ('| 分区 | 层级范围 | 源时钟 | chain_count | max_length | scan_enable |\n'
                '|---|---|---|---|---|---|\n'
                '| region | socket | tick | 2 | 64 | shift |\n')
        self.assertEqual(_task_requirements(text)['partitions'], [
            {'name': 'region', 'clock': 'tick', 'chain_count': 2, 'max_length': 64, 'scan_enable': 'shift'}])
        reordered = ('| scan_enable | max_length | 分区 | 源时钟 | chain_count |\n'
                     '|---|---|---|---|---|\n| shift | 64 | region | tick | 2 |\n')
        self.assertEqual(_task_requirements(reordered)['partitions'], _task_requirements(text)['partitions'])
        unrelated = '\n| 模块 | 数量 | 位宽 | 类型 |\n| socket | 2 | 64 | data |\n'
        self.assertEqual(_task_requirements(text + unrelated)['partitions'], _task_requirements(text)['partitions'])

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
            self.assertEqual(details["task_wrapper_port_busB"]["mismatching_port_bits"], 0)
            self.assertTrue(details["task_wrapper_port_busB"]["evidence"])
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
            if before == 'se_rx_fifo':
                details = {}
                _task_checks(ROOT, modified, req, check_details=details)
                first = details['task_partition_chains'][0]
                self.assertFalse(first['checks']['scan_enable'])
                self.assertTrue(first['checks']['chain_count'])
                self.assertEqual(first['observed']['scan_enables'], ['wrong_se'])
        strict = _task_requirements(text.replace("自动归入默认分区", "保留在顶层"))
        checks, _, _ = _task_checks(ROOT, reports, strict)
        self.assertFalse(checks["task_no_extra_partition_chains"])

    def test_residual_permissions_require_explicit_task_and_complete_counts(self):
        mixed = _task_requirements("忽略 `DFTR-TIE0` / `DFTR-TIE1` 违例；允许存在 `DFTR10` / `DFTR11` / `DFTR12` 3 种固有违例，其余违例必须清零")
        self.assertEqual(mixed["allowed_ignores"], ["DFTR-TIE0", "DFTR-TIE1"])
        self.assertEqual(mixed["allowed_residuals"], ["DFTR10", "DFTR11", "DFTR12"])
        forbidden = _task_requirements("不得忽略 DFTR9；不允许存在 DFTR10。")
        self.assertEqual(forbidden["allowed_ignores"], [])
        self.assertEqual(forbidden["allowed_residuals"], [])
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
            for message, expected in [
                ("[WARNING] [   COM-2003] Nothing matched for wrong_path.", False),
                ("[WARNING] [  SCAN-4948] Port 'missing_port' does not exist in current design. Verify that the specified port is correct.", False),
                ("[WARNING] [  SCAN-4610] New scan_enable port will be created.", True)]:
                (run / "R1.log").write_text(message)
                result, _, _ = inspect_run(run, script, {"exit_status": "completed"}, full_insertion=False)
                self.assertEqual(result["accepted"], expected)

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
