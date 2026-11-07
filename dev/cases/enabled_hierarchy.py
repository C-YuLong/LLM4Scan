"""Freeze/audit hierarchical transfer of function-preserving enabled-FF repair.

Concrete target: 12 edited source declarations elaborate to 144 state elements
with different enable bindings. This is a synthetic holdout, not a new IP.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_flow import verify
from verify_preprocessing import read_design
from verify_opensource import functional_pins
from verify_wrapper import scandef_members_match

ROOT = Path(__file__).resolve().parents[2]
# The first frozen qualification used the reserved instance identifier
# "release", rejected by Scan. Preserve that failed input; v2 fixes its name
# before any model evaluation, without changing the task or reference behavior.
SUITE = ROOT / 'benchmarks/enabled_hierarchy_v2_20261002'


def prepare():
    source, oracle = SUITE / 'input', SUITE / 'oracle'
    if source.exists() or oracle.exists():
        raise ValueError('Preserve frozen inputs and isolated reference')
    net = ['module enabled_top(input tick, input [11:0] enable_n, data, output [143:0] value);']
    for region in range(4):
        net.append(f'triad g{region} (.c(tick), .en(enable_n[{3*region+2}:{3*region}]), '
                   f'.d(data), .q(value[{36*region+35}:{36*region}]));')
    net += ['endmodule',
            'module triad(input c, input [2:0] en, input [11:0] d, output [35:0] q);']
    for leaf in range(3):
        net.append(f'leaf t{leaf} (.c(c), .en_n(en[{leaf}]), .d(d), .q(q[{12*leaf+11}:{12*leaf}]));')
    net += ['endmodule', 'module leaf(input c, en_n, input [11:0] d, output [11:0] q);',
            'wire allow;', 'wire [11:0] next_d;',
            'sky130_fd_sc_hd__inv_1 enable_inv (.A(en_n), .Y(allow));']
    edits = []
    for bit in range(12):
        net.append(f'sky130_fd_sc_hd__xor2_1 mix{bit} (.A(d[{bit}]), .B(q[{(bit+5)%12}]), .X(next_d[{bit}]));')
        original = f'sky130_fd_sc_hd__edfxtp_1 r{bit} (.CLK(c), .DE(allow), .D(next_d[{bit}]), .Q(q[{bit}]));'
        replacement = (f'wire held_data{bit};\n'
            f'sky130_fd_sc_hd__mux2_1 hold{bit} (.A0(q[{bit}]), .A1(next_d[{bit}]), .S(allow), .X(held_data{bit}));\n'
            f'sky130_fd_sc_hd__dfxtp_1 r{bit} (.CLK(c), .D(held_data{bit}), .Q(q[{bit}]));')
        net.append(original)
        edits.append({'path': 'netlist/design.v', 'before': original, 'after': replacement})
    net += ['endmodule']
    task = '''# 多实例数据使能的功能保持扫描修复

顶层 enabled_top，读取 lib/sky130.lib 和 netlist/design.v，诊断 original.dofile 并完成扫描。
四个 g0 至 g3 复用同一 triad，每个含三个同类型 leaf；每个 leaf 有 12 个寄存器。共 144 个原始 FF 必须且只能各入链一次，保留原始实例名、功能端口和全部状态行为。
tick 是唯一源时钟，off_state=0；enable_n 与 data 都是任意功能输入，不能固定、当成扫描控制或排除其寄存器。各 leaf 有自己独立的 enable_n 绑定，控制 DE 数据保持/更新，不是门控时钟。
最终指定扫描单元只有 sky130_fd_sc_hd__sdfxtp_1；不得使用 sedfxtp_1 或丢弃原 edfxtp_1 的 DE 行为。允许必要的等价 Pre-scan 工作副本改写、增加组合逻辑，但不能覆盖原始输入。必须对原始网表和最终实际使用的 Pre-scan 运行真实 Yosys EQY，无输入假设；证明失败不得发布成功。
所有 FF 属于 Default_Partition。新增 scan_shift 为高有效扫描使能，off_state=0、usage=scan。
chain_count=6，max_length=24，mix_clocks=false，mix_edges=false，internal_clocks=none，add_lockup=true。扫描输入 enable_si%d，扫描输出 enable_so%d。不使用 wrapper 或显式 segment，不能降级/忽略 DRC，最终 DRC 为零。
导出 post_scan.v、post_scan.ctl、post_scan.def；导出 drc.rpt、scan_chain.rpt、scan_cell.rpt、scan_cfg.rpt、scan_signal.rpt、scan_partition.rpt、insertion_info.rpt。报告对应最终插链结果，并保存完整日志、修改 diff 和诊断证据。
'''
    lines = ['load_lib "$input_dir/lib/sky130.lib"', 'load_netlist "$input_dir/netlist/design.v"',
             'present_design enabled_top',
             'set_scan_cell_mapping sky130_fd_sc_hd__edfxtp_1 sky130_fd_sc_hd__sdfxtp_1',
             'set_scan_cell_mapping sky130_fd_sc_hd__dfxtp_1 sky130_fd_sc_hd__sdfxtp_1',
             'set_scan_signal -type clock -port tick -off_state 0',
             'set_scan_signal -type scan_enable -port scan_shift -off_state 0 -usage scan',
             'set_scan_cfg -chain_count 6 -max_length 24 -mix_clocks false -mix_edges false -internal_clocks none -add_lockup true -si_port_format enable_si%d -so_port_format enable_so%d',
             'examine_scan_drc -file "$out_rpt_dir/drc.rpt"', 'examine_scan_chain', 'insert_dft_logic']
    lines += [f'{command} -file "$out_rpt_dir/{report}.rpt"' for command, report in (
        ('rpt_scan_chain', 'scan_chain'), ('rpt_scan_chain_cell', 'scan_cell'), ('rpt_scan_cfg', 'scan_cfg'),
        ('rpt_scan_signal', 'scan_signal'), ('rpt_scan_partition', 'scan_partition'), ('rpt_insertion_info', 'insertion_info'))]
    lines += ['dump_netlist -file "$out_res_dir/post_scan.v"', 'dump_ctl "$out_res_dir/post_scan.ctl"',
              'dump_def -section scan_chain -file "$out_res_dir/post_scan.def"', 'exit']
    correct = '\n'.join(lines) + '\n'
    substitutions = [
        ('scan_enable_polarity', '-port scan_shift -off_state 0', '-port scan_shift -off_state 1'),
        ('chain_count', '-chain_count 6', '-chain_count 3'),
        ('max_length', '-max_length 24', '-max_length 48'),
        ('mix_edges', '-mix_edges false', '-mix_edges true'),
        ('missing_scandef', 'dump_def -section scan_chain -file "$out_res_dir/post_scan.def"\n', '')]
    original = correct
    for name, before, after in substitutions:
        if original.count(before) != 1:
            raise ValueError('Nonunique fault: ' + name)
        original = original.replace(before, after, 1)
    (source / 'lib').mkdir(parents=True)
    (source / 'netlist').mkdir()
    oracle.mkdir()
    (source / 'lib/sky130.lib').write_bytes((ROOT / 'official/extracted/public_cases/task_1/case4/input/lib/sky130.lib').read_bytes())
    (source / 'netlist/design.v').write_text('\n'.join(net) + '\n', encoding='utf-8')
    (source / 'task_spec.md').write_text(task, encoding='utf-8')
    (source / 'limitations.md').write_text('整个 case 的执行总时间不超过 480 秒。\n', encoding='utf-8')
    header = 'set script_dir /input\nset base_dir /output\nset out_rpt_dir /output/reports\nset out_res_dir /output/deliverables\n'
    (source / 'original.dofile').write_text(header + original.replace('$input_dir', '$script_dir'), encoding='utf-8')
    (oracle / 'correct.dofile').write_text(correct, encoding='utf-8')
    (oracle / 'edits.json').write_text(json.dumps({'edits': edits}, indent=2), encoding='utf-8')
    (oracle / 'faults.json').write_text(json.dumps(['data_enable_loss', *[row[0] for row in substitutions]], indent=2), encoding='utf-8')
    print(json.dumps({'frozen': str(source), 'ff': 144, 'source_enabled_ff': 12, 'faults': 6}))


def audit(source, output):
    result = verify(output)
    checks = result['checks']
    if not checks['accepted_final_run']:
        return result
    decision = json.loads((output / 'decision_log.json').read_text(encoding='utf-8'))
    run, final = output / 'runs' / decision['final_run'], output / 'final_results'
    work = output / 'independent_enabled_audit'
    work.mkdir(exist_ok=False)
    library = source / 'lib/sky130.lib'
    original = read_design(source / 'netlist/design.v', library, work / 'original.json', 'enabled_top')
    used = read_design(final / 'deliverables/pre_scan_final.v', library, work / 'used.json', 'enabled_top')
    post = read_design(final / 'deliverables/post_scan.v', library, work / 'post.json', 'enabled_top')
    structure, mismatches = functional_pins(used, post)
    checks.update(structure)
    lec = json.loads((run / 'lec_result.json').read_text())
    checks['original_to_used_eqy_pass'] = (lec['passed'] is True and lec['status'] == 'PASS'
        and lec['candidate_unchanged_after_scan'] is True and (run / 'equivalence/result/PASS').is_file())
    checks['proved_prescan_is_scan_input'] = Path(lec['candidate']).name in (run / 'deliverables' / (run.name + '.dofile')).read_text()
    expected = {name for name, cell in original['cells'].items() if cell['type'] == 'sky130_fd_sc_hd__edfxtp_1'}
    scanned = {name for name, cell in post['cells'].items() if cell['type'] == 'sky130_fd_sc_hd__sdfxtp_1'}
    checks['all_original_ff_once'] = len(expected) == 144 and expected == scanned
    seen, members, ports, serial, sizes = [], {}, {'scan_shift'}, True, []
    for name, body in re.findall(r'ScanChain\s+"([^"]+)"\s*\{([^}]+)\}', (final / 'deliverables/post_scan.ctl').read_text()):
        names = re.findall(r'"([^"]+)"', re.search(r'ScanCells\s+(.*?);', body, re.S)[1])
        si, so, se = [re.search(key + r'\s+"([^"]+)"', body)[1] for key in ('ScanIn', 'ScanOut', 'ScanEnable')]
        size = int(re.search(r'ScanLength\s+(\d+)', body)[1])
        members[name], prior = Counter(names), post['ports'][si]['bits']
        sizes.append(size)
        ports.update((si, so))
        serial &= len(names) == size and se == 'scan_shift' and bool(re.fullmatch(r'enable_si\d+', si) and re.fullmatch(r'enable_so\d+', so))
        for member in names:
            qualified = member.replace('/', '.')
            seen.append(qualified)
            pins = post['cells'][qualified]['connections']
            serial &= pins['SCD'] == prior and pins['SCE'] == post['ports']['scan_shift']['bits']
            prior = pins['Q']
        serial &= prior == post['ports'][so]['bits']
    checks['ctl_exact_membership'] = len(seen) == len(set(seen)) == 144 and set(seen) == expected
    checks['six_chains_24_bits'] = sizes == [24] * 6
    checks['actual_serial_connectivity_and_enable'] = bool(serial)
    checks['no_unrequested_ports'] = set(post['ports']) == set(original['ports']) | ports
    checks['scandef_exact_membership'] = scandef_members_match((final / 'deliverables/post_scan.def').read_text(), members)
    cfg = (final / 'reports/scan_cfg.rpt').read_text()
    checks['configuration_values'] = all(re.search(r'(?m)^' + key + r'\s+' + value + r'\s*$', cfg)
        for key, value in [('chain_count', '6'), ('max_length', '24'), ('mix_clocks', 'False'),
                           ('mix_edges', 'False'), ('internal_clocks', 'none'), ('add_lockup', 'True')])
    checks['drc_zero'] = bool(re.search(r'Total violations:\s*0\b', (final / 'reports/drc.rpt').read_text()))
    result.update(passed=all(checks.values()), mismatches=mismatches[:30],
                  scope='Hierarchical original-to-used EQY plus actual post-scan functional/serial connectivity; not a hidden-score estimate.')
    (work / 'acceptance.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'audit'])
    parser.add_argument('--input', type=Path, default=SUITE / 'input')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
    else:
        if args.output is None:
            parser.error('audit requires --output')
        result = audit(args.input, args.output)
        print(json.dumps(result))
        raise SystemExit(0 if result.get('passed') else 1)
