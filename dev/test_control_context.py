"""Focused control-context and online member-audit checks; no model/Scan calls."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from control_context import control_context, library_roles, mapping_input_losses, required_mapping_check, required_membership_check, explicit_exclusion_check
from acceptance import chain_member_checks, merge_task_constraints, _task_checks

ROOT = Path(__file__).resolve().parents[1]


class ControlContextTests(unittest.TestCase):
    def test_data_ff_exclusion_cannot_hide_behind_allowed_domain_or_latch(self):
        text = ('Name Type Length InstanceName SiSoClocks ClockEdge ObjState\n'
                'r dff 1 tap/r user_defined_nonscannable\n'
                'r sff 1 storage/r user_defined_nonscannable\n'
                'l latch 1 clock_latch user_defined_nonscannable\n')
        rules = [{'target': 'tap', 'value': 'exclude'}]
        ok, detail = explicit_exclusion_check({'objects.rpt': text}, rules, required=True)
        self.assertFalse(ok)
        self.assertEqual(detail['user_excluded_ff_rows'], 2)
        self.assertEqual(detail['unauthorized_ff_rows'], 1)
        self.assertEqual(detail['examples'][0]['instance'], 'storage/r')
        rules.append({'target': 'storage', 'value': 'exclude'})
        self.assertTrue(explicit_exclusion_check({'objects.rpt': text}, rules, required=True)[0])
        rules.append({'target': 'storage/r', 'value': 'include'})
        self.assertFalse(explicit_exclusion_check({'objects.rpt': text}, rules, required=True)[0])
        self.assertFalse(explicit_exclusion_check({}, rules, required=True)[0])

    def test_small_independent_reset_is_not_hidden_by_repeated_clock_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'netlist').mkdir()
            (source / 'cells.lib').write_text('''library(x) {
cell(FF) { ff(qi,qn) { clocked_on : "C"; clear : "!R"; }
 pin(C) { direction : input; } pin(R) { direction : input; }
 pin(D) { direction : input; } pin(Q) { direction : output; } }
}''')
            rows = ['module top(input c, main_reset, a, d);']
            for index in range(240):
                rows += [f'assign z{index} = c;',
                         f'FF r{index} (.C(z{index}), .R(main_reset), .D(d), .Q(q{index}));']
            rows += ['FF rare (.C(c), .R(a), .D(d), .Q(last));', 'endmodule']
            (source / 'netlist/design.v').write_text('\n'.join(rows))
            result = control_context(source)
            self.assertIn('FF rare (.C(c), .R(a)', result['sources']['netlist/design.v'])
            self.assertTrue(any(s['net'] == 'a' and s['example_pin'] == 'R' for s in result['control_samples']))
            self.assertTrue(result['truncated'])

    def test_long_hierarchy_shows_traversed_tail_and_multiline_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'netlist').mkdir()
            (source / 'cells.lib').write_text('''library(x) {
cell(FF) { ff(qi,qn) { clocked_on : "C"; clear : "!R"; }
 pin(C) { direction : input; } pin(R) { direction : input; }
 pin(D) { direction : input; } pin(Q) { direction : output; } }
}''')
            rows = ['module top(input tick, release_n, data, output value);', 'island unit (']
            rows += [f' .p{i}(data),' for i in range(60)]
            rows += [' .c(tick),', ' .r(', ' release_n),', ' .d(data), .q(value));', 'endmodule',
                     'module island(input c,r,d, output q, input ' + ','.join(f'p{i}' for i in range(60)) + ');',
                     'FF state (.C(c), .R(r), .D(d), .Q(q));', 'endmodule']
            (source / 'netlist/design.v').write_text('\n'.join(rows))
            result = control_context(source)
            text = result['sources']['netlist/design.v']
            for number in (2, 63, 64, 65, 68, 69):
                self.assertIn(f'L{number}: {rows[number-1]}', text)
            self.assertNotIn('.p59(data)', text)
            self.assertEqual(result['library_cell_functions']['FF']['state']['clear'], '!R')

    def test_mapping_accepts_retained_shift_ff_only_after_reported_scan_head(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'netlist').mkdir()
            (source / 'cells.lib').write_text('''library(x) {
cell(DFF) { ff(q,qn) { next_state : "D"; } }
cell(SFF) { ff(q,qn) { next_state : "D"; }
 pin(SE) { direction : input; signal_type : "test_scan_enable"; } }
cell(WRONG) { ff(q,qn) { next_state : "D"; } }
}''')
            (source / 'netlist/design.v').write_text('''module top(input d, output q);
DFF a (.D(d), .Q(w));
DFF b (.D(w), .Q(q));
endmodule
''')
            report = 'CellNo InstanceName RefName ShiftRegID/CellNo\nI 1 0 a clk (+) SFF Q 4/0\n1 b clk (+) DFF Q 4/1\n'
            def check(text):
                return required_mapping_check(source, 'present_design top\n', {'cells.rpt': text}, {'DFF': 'SFF'})
            ok, detail = check(report)
            self.assertTrue(ok)
            self.assertEqual(detail['retained_shift_cells'], 1)
            for wrong in (report.replace('4/1', '-/-'), report.replace('4/0', '5/0'),
                          report.replace('I 1 0 a', 'I 1 9 a'), report.replace('SFF Q', 'DFF Q'),
                          report.replace('DFF Q 4/1', 'WRONG Q 4/1')):
                self.assertFalse(check(wrong)[0])

    def test_scan_exclusion_exception_checks_actual_original_membership(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'netlist').mkdir()
            (source / 'cells.lib').write_text('library(x) { cell(FF) { ff(q,qn) { next_state : "D"; } } }')
            (source / 'netlist/design.v').write_text('''module top(input d, output q);
bank block (.D(d), .Q(q));
endmodule
module bank(input D, output Q);
FF keep_reg[0] (.D(D), .Q(Q));
FF keep_reg[1] (.D(D), .Q(Q));
FF other (.D(D), .Q(Q));
endmodule
''')
            spec = 'block 层级不参与扫描，但 block/keep_reg* 必须保留扫描。'
            rules = merge_task_constraints(spec, {'constraints': [
                {'quote': spec, 'kind': 'scan_membership', 'target': 'block', 'value': 'exclude'},
                {'quote': spec, 'kind': 'scan_membership', 'target': 'block/keep_reg*', 'value': 'include'}],
                'uncovered': []})['scan_membership']
            ctl = 'ScanChain "1" { ScanCells "block/keep_reg[0]" "block/keep_reg[1]"; }'
            script = 'present_design top\n'
            self.assertTrue(required_membership_check(source, script, [ctl], rules)[0])
            missing_ok, missing = required_membership_check(source, script, [ctl.replace('"block/keep_reg[1]"', '')], rules)
            self.assertFalse(missing_ok)
            self.assertEqual(missing['missing_examples'], ['block/keep_reg[1]'])
            wrong_ok, wrong = required_membership_check(source, script, [ctl.replace(';', ' "block/other";')], rules)
            self.assertFalse(wrong_ok)
            self.assertEqual(wrong['forbidden_examples'], ['block/other'])
            self.assertFalse(required_membership_check(source, script, [], rules)[0])
            report = {'cells.rpt': 'CellNo InstanceName RefName\nI 1 0 block/keep_reg[0] clk (+) FF Q\n1 block/keep_reg[1] clk (+) FF Q'}
            self.assertTrue(required_membership_check(source, script, [], rules, reports=report)[0])
            self.assertFalse(required_membership_check(source, script, [ctl], [{'target': 'absent', 'value': 'exclude'}])[0])
            literal = [{'target': 'block/keep_reg[0]', 'value': 'include'}]
            self.assertTrue(required_membership_check(source, script, [ctl], literal)[0])

    def test_required_mapping_checks_reported_original_hierarchy_not_just_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'netlist').mkdir()
            (source / 'cells.lib').write_text('''library(x) {
cell(lib__DFF) {} cell(lib__SFF) {} cell(lib__ALTERNATIVE) {}
}''')
            (source / 'netlist/design.v').write_text('''module top(input d, output q);
bank box (.D(d), .Q(q));
endmodule
module bank(input D, output Q);
lib__DFF r (.D(D), .Q(Q));
endmodule
''')
            script = 'present_design top\n'
            report = 'CellNo InstanceName RefName\nI 1 0 box/r clk (+) lib__ALTERNATIVE Q -/-\n'
            ok, details = required_mapping_check(source, script, {'cells.rpt': report}, {'lib__DFF': 'SFF'})
            self.assertFalse(ok)
            self.assertEqual(details['mismatches'][0]['instance'], 'box/r')
            self.assertEqual(details['mismatches'][0]['expected'], 'lib__SFF')
            self.assertTrue(required_mapping_check(source, script,
                {'cells.rpt': report.replace('lib__ALTERNATIVE', 'lib__SFF')}, {'DFF': 'SFF'})[0])
            self.assertFalse(required_mapping_check(source, script, {}, {'DFF': 'SFF'})[0])

    def test_nested_test_cell_does_not_override_actual_scan_next_state(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'cells.lib').write_text('''library(x) {
cell(SFF) {
 ff(iq,iqn) { clocked_on : "C"; next_state : "D&!SE | SI&SE"; }
 pin(C) { direction : input; } pin(D) { direction : input; }
 pin(SE) { direction : input; } pin(SI) { direction : input; }
 test_cell() {
  ff(iq,iqn) { clocked_on : "C"; next_state : "D"; }
  pin(SE) { direction : input; signal_type : "test_scan_enable"; }
 } }
}''')
            role = library_roles(source)['SFF']
            self.assertEqual(role['state']['next_state'], 'D&!SE | SI&SE')
            self.assertTrue(role['scan'])

    def test_mapping_cannot_drop_functional_enable_from_used_prescan_cell(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'netlist').mkdir()
            (source / 'lib').mkdir()
            (source / 'lib/cells.lib').write_text('''library(x) {
cell(EFF) { ff(iq,iqn) { clocked_on : "C"; next_state : "D&E | iq&!E"; }
 pin(C) { direction : input; } pin(E) { direction : input; }
 pin(D) { direction : input; } pin(Q) { direction : output; } }
cell(FF) { ff(iq,iqn) { clocked_on : "C"; next_state : "D"; }
 pin(C) { direction : input; } pin(D) { direction : input; } pin(Q) { direction : output; } }
cell(SFF) { ff(iq,iqn) { clocked_on : "C"; next_state : "D&!SE | SI&SE"; }
 pin(C) { direction : input; } pin(D) { direction : input; } pin(Q) { direction : output; }
 pin(SE) { direction : input; } pin(SI) { direction : input; } }
cell(mux2) { pin(A) { direction : input; } pin(B) { direction : input; }
 pin(S) { direction : input; } pin(Y) { direction : output; function : "A&!S | B&S"; } }
cell(mux2_big) { pin(A) { direction : input; } pin(B) { direction : input; }
 pin(S) { direction : input; } pin(Y) { direction : output; function : "A&!S | B&S"; } }
}''')
            original = source / 'netlist/design.v'
            original.write_text('module top(input c,d,e,output q);\nEFF u (.C(c),.D(d),.E(e),.Q(q));\nendmodule')
            log = '[INFO] [CMD-0034] @1: set_scan_cell_mapping EFF SFF\n'
            loss = mapping_input_losses(source, log)
            self.assertEqual(len(loss), 1)
            self.assertEqual(loss[0]['lost_functional_inputs'], ['E'])
            self.assertEqual(loss[0]['example_instance'], 'u')
            context = control_context(source, ['EFF'])
            coverage = context['requested_type_coverage']['EFF']
            self.assertEqual(coverage, {'source_statements': 1, 'fully_shown': 1, 'complete': True})
            self.assertEqual(context['rewrite_cell_candidates'], ['FF', 'mux2'])
            self.assertEqual(context['library_cell_functions']['mux2']['outputs']['Y'], 'A&!S | B&S')
            self.assertEqual(control_context(source)['rewrite_cell_candidates'], [])
            self.assertEqual(mapping_input_losses(source, log.replace('EFF SFF', 'FF SFF')), [])
            candidate = source / 'working.v'
            candidate.write_text('module top(input c,d,e,output q);\nFF u (.C(c),.D(e?d:q),.Q(q));\nendmodule')
            self.assertEqual(mapping_input_losses(source, log, candidate=candidate), [])
            # The source statement exists but a bounded excerpt cannot show
            # its complete span. Coverage must not authorize a guessed edit.
            original.write_text(original.read_text().replace('.C(c),', '.C(c),\n' + '// comment\n' * 50))
            coverage = control_context(source, ['EFF'])['requested_type_coverage']['EFF']
            self.assertEqual(coverage, {'source_statements': 1, 'fully_shown': 0, 'complete': False})

    def test_bounded_context_does_not_depend_on_control_pin_order(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'netlist').mkdir()
            (source / 'lib').mkdir()
            (source / 'lib/cells.lib').write_text('''library(x) {
cell(FF) { ff(qi,qn) { clocked_on : "C"; clear : "!R"; }
 pin(C) { direction : input; } pin(R) { direction : input; }
 pin(D) { direction : input; } pin(Q) { direction : output; } }
}''')
            rows = ['module top(input a, input b, input d);']
            for i in range(60):
                rows.extend([f'assign c{i}a = a;', f'assign c{i}b = c{i}a;',
                             f'assign c{i}c = c{i}b;', f'assign r{i}a = b;',
                             f'assign r{i}b = r{i}a;',
                             f'FF z{i} (.C(c{i}c), .R(r{i}b), .D(d), .Q(q{i}));'])
            rows.append('endmodule')
            (source / 'netlist/design.v').write_text('\n'.join(rows))
            roles = library_roles(source)
            with patch('control_context.library_roles', return_value=roles):
                roles['FF']['controls'] = ['C', 'R']
                first = control_context(source)
                roles['FF']['controls'] = ['R', 'C']
                second = control_context(source)
            self.assertTrue(first['truncated'])
            self.assertEqual(first, second)

    def test_emitted_alias_removal_is_not_functional_corruption(self):
        import json
        sys.path.insert(0, str(ROOT / 'dev'))
        from control_stage import audit
        for name in ('H03_latched_enable', 'H06_clean_alias'):
            source = ROOT / 'benchmarks/control6/cases' / name
            result = audit(ROOT / 'benchmarks/control6/runs/frozen14' / name,
                           json.loads((source / 'oracle.json').read_text()), source / 'input')
            self.assertTrue(result['checks']['netlist_functional_pins_and_scan_enable'])
            self.assertTrue(result['checks']['original_control_cells_and_connections'])
            # Normalizing equivalent wires must NOT forgive the actual wrong
            # partition in the frozen H03 result.
            self.assertEqual(result['passed'], name == 'H06_clean_alias')

    def test_default_partition_is_a_requirement_not_just_a_chain_count(self):
        spec = '全部内部扫描链放在默认分区，恰好四条。'
        requirements = merge_task_constraints(spec, {'constraints': [
            {'quote': spec, 'kind': 'chain_partition', 'target': '', 'value': 'Default_Partition'}], 'uncovered': []})
        reports = {'chains.rpt': 'ChainProperty ScanEnable\nI 1 8 si so move pulse invented_partition'}
        checks, _, _ = _task_checks(ROOT, reports, requirements)
        self.assertFalse(checks['task_chain_partition_scope'])
        reports['chains.rpt'] = reports['chains.rpt'].replace('invented_partition', 'Default_Partition')
        self.assertTrue(_task_checks(ROOT, reports, requirements)[0]['task_chain_partition_scope'])

    def test_neutral_names_hierarchy_latch_and_large_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'netlist').mkdir()
            (source / 'lib').mkdir()
            (source / 'lib/cells.lib').write_text('''library(x) {
cell(FF) { ff(qi,qn) { clocked_on : "P"; clear : "!R"; }
 pin(P) { direction : "input"; } pin(R) { direction : input; }
 pin(D) { direction : input; } pin(Q) { direction : output; } }
cell(LAT) { latch(qi,qn) { enable : "G"; }
 pin(G) { direction : input; } pin(D) { direction : input; } pin(Q) { direction : output; } }
cell(AND) { pin(A) { direction : input; } pin(B) { direction : input; } pin(Y) { direction : output; function : "A&B"; } }
}''')
            (source / 'netlist/merged.v').write_text('''module top(input a, input b, input c, input d, output q);
wire w;
parcel u7 (.i(a), .j(b), .k(c), .o(w));
FF z (.P(w), .R(c), .D(d), .Q(q));
''' + ''.join(f'wire irrelevant_{i};\n' for i in range(2500)) + '''endmodule
module parcel(input i, input j, input k, output o);
wire m;
LAT n2 (.G(i), .D(j), .Q(m));
AND n3 (.A(i), .B(m), .Y(o));
endmodule
''')
            trace = control_context(source)
            text = trace['sources']['netlist/merged.v']
            self.assertIn('LAT n2', text)
            self.assertIn('AND n3', text)
            self.assertIn('parcel u7', text)
            self.assertIn('module parcel', text)
            self.assertEqual(trace['library_cell_functions']['AND']['outputs']['Y'], 'A&B')
            self.assertNotIn('irrelevant_', text)
            self.assertFalse(trace['truncated'])
            samples = {(s['module'], s['net']): (s['example_instance'], s['example_pin'])
                       for s in trace['control_samples']}
            self.assertEqual(samples[('top', 'c')], ('z', 'R'))
            self.assertEqual(samples[('parcel', 'i')], ('n2', 'G'))

    def test_online_member_audit_accepts_existing_segments_and_wrapper(self):
        for relative in ('benchmarks/scan32/runs/baseline_small/C08_segments/final_results',
                         'dev/results/demo_all04_task_1_case3/final_results'):
            run = ROOT / relative
            reports = {p.name: p.read_text(encoding='utf-8') for p in (run / 'reports').glob('*.rpt')}
            checks, details = chain_member_checks(run, reports, list((run / 'deliverables').glob('*.ctl')))
            self.assertTrue(all(checks.values()), (relative, checks, details))

    def test_online_member_audit_rejects_duplicate_and_reordered_ctl(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            ctl = run / 'out.ctl'
            ctl.write_text('ScanChain "1" { ScanLength 2; ScanCells "r0" "r1"; }')
            reports = {'chains.rpt': 'ChainProperty ScanEnable\nI 1 2 si so se clk',
                       'cells.rpt': 'CellNo InstanceName RefName\nI 1 0 r0 clk (+) SFF Q\n 1 r1 clk (+) SFF Q'}
            self.assertTrue(all(chain_member_checks(run, reports, [ctl])[0].values()))
            reports['cells.rpt'] = reports['cells.rpt'].replace('1 r1', '1 r0')
            self.assertFalse(chain_member_checks(run, reports, [ctl])[0]['chain_cell_members_unique_and_numbered'])
            reports['cells.rpt'] = reports['cells.rpt'].replace('1 r0', '1 r1')
            ctl.write_text('ScanChain "1" { ScanLength 2; ScanCells "r1" "r0"; }')
            self.assertFalse(chain_member_checks(run, reports, [ctl])[0]['ctl_chain_members_match_cell_report'])


if __name__ == '__main__':
    unittest.main()
