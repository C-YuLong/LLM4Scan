"""One frozen instance-sensitive case, with an independent Yosys-based audit.

Unlike Scan32's distinct bank types, all four branches instantiate the SAME
two-tile module and all eight tiles instantiate the SAME leaf module. Oracles
are never mounted during model evaluation. This is a synthetic development
case, not industrial IP or an organizer hidden test.
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
SUITE = ROOT / 'benchmarks/reused_hierarchy_20261002'


def prepare():
    source, oracle = SUITE / 'input', SUITE / 'oracle'
    if source.exists() or oracle.exists():
        raise ValueError('Frozen inputs already exist; preserve them')
    library = (ROOT / 'official/extracted/public_cases/task_1/case4/input/lib/sky130.lib').read_bytes()
    net = ['module reused_top(input tick_a, tick_b, clear_n, clear_h, open_h, open_l_n,',
           'input [7:0] permit, input [63:0] data, output [511:0] value);',
           'wire bypass_low, reset_low, reversed;',
           'sky130_fd_sc_hd__inv_1 inv_bypass (.A(open_l_n), .Y(bypass_low));',
           'sky130_fd_sc_hd__inv_1 inv_reset (.A(clear_h), .Y(reset_low));',
           'sky130_fd_sc_hd__inv_1 inv_tick (.A(tick_b), .Y(reversed));']
    for branch in range(4):
        clk = ['tick_a', 'tick_a', 'tick_b', 'reversed'][branch]
        rst = 'clear_n' if branch < 2 else 'reset_low'
        bypass = 'open_h' if branch % 2 == 0 else 'bypass_low'
        net.append(f'pair a{branch} (.c({clk}), .r({rst}), .b({bypass}), '
                   f'.p(permit[{branch*2+1}:{branch*2}]), .d(data), .q(value[{branch*128+127}:{branch*128}]));')
    net += ['endmodule', 'module pair(input c, r, b, input [1:0] p, input [63:0] d, output [127:0] q);',
            'tile left (.c(c), .r(r), .b(b), .p(p[0]), .d(d), .q(q[63:0]));',
            'tile right (.c(c), .r(r), .b(b), .p(p[1]), .d(d), .q(q[127:64]));',
            'endmodule', 'module tile(input c, r, b, p, input [63:0] d, output [63:0] q);',
            'wire low_phase, allow, held, gated;', 'wire [63:0] next_d;',
            'sky130_fd_sc_hd__inv_1 phase (.A(c), .Y(low_phase));',
            'sky130_fd_sc_hd__or2_1 select (.A(p), .B(b), .X(allow));',
            'sky130_fd_sc_hd__dlxtp_1 hold (.GATE(low_phase), .D(allow), .Q(held));',
            'sky130_fd_sc_hd__and2_1 gate (.A(c), .B(held), .X(gated));']
    for bit in range(64):
        net += [f'sky130_fd_sc_hd__xor2_1 mix{bit} (.A(d[{bit}]), .B(q[{(bit+7)%64}]), .X(next_d[{bit}]));',
                f'sky130_fd_sc_hd__dfrtp_1 r{bit} (.CLK(gated), .RESET_B(r), .D(next_d[{bit}]), .Q(q[{bit}]));']
    net += ['endmodule']
    task = '''# 复用层级实例的扫描修复

顶层 reused_top，读取 lib/sky130.lib 与 netlist/design.v。诊断 original.dofile 并完成扫描插入。
四个 a0 至 a3 都是同一 pair 模块的实例，各自包含同一 tile 类型的 left、right；每个 tile 有 64 个数据 FF，总计 512 个数据 FF。
将全部 sky130_fd_sc_hd__dfrtp_1 映射为 sky130_fd_sc_hd__sdfrtp_1；数据功能连接、全部原始组合单元和控制锁存器类型及连接必须保持。
八个 a0/left/hold、a0/right/hold、a1/left/hold、a1/right/hold、a2/left/hold、a2/right/hold、a3/left/hold、a3/right/hold 是时钟使能锁存器，必须排除扫描且不得改接；不能排除任何数据 FF。
tick_a、tick_b 是仅有的源时钟，off_state=0；a3 的实际数据 FF 使用 tick_b 经过反相后的边沿，保留该连接，不新增独立内部时钟。internal_clocks=none，mix_clocks=false，mix_edges=false，add_lockup=true。
clear_n 是低有效异步复位，off_state=1；clear_h 是高有效异步复位，off_state=0，扫描时均释放。
open_h、open_l_n 是原有的独立门控测试使能（usage=clock_gating），不是常量信号。请根据实际层级绑定和逻辑确定它们的有效极性。permit 和 data 为功能输入，不得固定它们。
扫描模式需要通过原有控制锁存器打开门控，不能改功能时钟连接。四个功能区域严格各自形成分区；不同实例共享源时钟不意味着可以合并区域。
| 分区 | 层级范围 | 源时钟 | chain_count | max_length | scan_enable |
|---|---|---|---|---|---|
| zone0 | a0 | tick_a | 2 | 64 | shift_0 |
| zone1 | a1 | tick_a | 2 | 64 | shift_1 |
| zone2 | a2 | tick_b | 2 | 64 | shift_2 |
| zone3 | a3 | tick_b | 2 | 64 | shift_3 |
每个分区仅含对应实例下的 128 个数据 FF，不允许默认分区或其他额外扫描链。新增 shift_0 至 shift_3 都是高有效数据扫描使能，off_state=0、usage=scan。
扫描输入命名 rx_si%d，扫描输出命名 rx_so%d。不能修改原始网表、固定普通功能输入、忽略或降级任何 DRC。DRC 必须为 0。
交付 post_scan.v、post_scan.ctl、post_scan.def；导出 drc.rpt、scan_chain.rpt、scan_cell.rpt、scan_cfg.rpt、scan_signal.rpt、scan_partition.rpt、insertion_info.rpt。
所有输出对应最终插链结果，保存每轮日志和修改记录，诊断需引用真实的原始证据。
'''
    lines = ['load_lib "$input_dir/lib/sky130.lib"', 'load_netlist "$input_dir/netlist/design.v"',
             'present_design reused_top', 'set_scan_cell_mapping sky130_fd_sc_hd__dfrtp_1 sky130_fd_sc_hd__sdfrtp_1',
             'set_scan_signal -type clock -port tick_a -off_state 0',
             'set_scan_signal -type clock -port tick_b -off_state 0 -associated_internal_clocks {inv_tick/Y}',
             'set_scan_signal -type reset -port clear_n -off_state 1', 'set_scan_signal -type reset -port clear_h -off_state 0',
             'set_scan_signal -type scan_enable -port open_h -off_state 0 -usage clock_gating',
             'set_scan_signal -type scan_enable -port open_l_n -off_state 1 -usage clock_gating',
             'set_scan_cfg -mix_clocks false -mix_edges false -internal_clocks none -add_lockup true -si_port_format rx_si%d -so_port_format rx_so%d',
             'set_scan_drc_cfg -clock_gating_init_cycles 2',
             'set_scan_element false {' + ' '.join(f'a{i}/{side}/hold' for i in range(4) for side in ('left', 'right')) + '}']
    for branch in range(4):
        lines += [f'add_scan_partition zone{branch} -include {{a{branch}}}',
                  f'set_current_scan_partition zone{branch}', 'set_scan_cfg -chain_count 2 -max_length 64',
                  f'set_scan_signal -type scan_enable -port shift_{branch} -off_state 0 -usage scan',
                  'set_current_scan_partition Default_Partition']
    lines += ['examine_scan_drc -file "$out_rpt_dir/drc.rpt"', 'examine_scan_chain', 'insert_dft_logic']
    lines += [f'{command} -file "$out_rpt_dir/{report}.rpt"' for command, report in (
        ('rpt_scan_chain', 'scan_chain'), ('rpt_scan_chain_cell', 'scan_cell'), ('rpt_scan_cfg', 'scan_cfg'),
        ('rpt_scan_signal', 'scan_signal'), ('rpt_scan_partition', 'scan_partition'), ('rpt_insertion_info', 'insertion_info'))]
    lines += ['dump_netlist -file "$out_res_dir/post_scan.v"', 'dump_ctl "$out_res_dir/post_scan.ctl"',
              'dump_def -section scan_chain -file "$out_res_dir/post_scan.def"', 'exit']
    correct = '\n'.join(lines) + '\n'
    faults = [
        ('bypass_polarity', '-port open_l_n -off_state 1', '-port open_l_n -off_state 0'),
        ('reset_polarity', '-port clear_h -off_state 0', '-port clear_h -off_state 1'),
        ('instance_membership', 'add_scan_partition zone1 -include {a1}', 'add_scan_partition zone1 -include {a0}'),
        ('enable_binding', '-port shift_2 -off_state 0', '-port shift_1 -off_state 0'),
        ('chain_count', 'set_current_scan_partition zone3\nset_scan_cfg -chain_count 2', 'set_current_scan_partition zone3\nset_scan_cfg -chain_count 1'),
        ('missing_scandef', 'dump_def -section scan_chain -file "$out_res_dir/post_scan.def"\n', ''),
        ('derived_clock_control', ' -associated_internal_clocks {inv_tick/Y}', ''),
    ]
    original = correct
    for name, before, after in faults:
        if original.count(before) != 1:
            raise ValueError('Nonunique fault: ' + name)
        original = original.replace(before, after, 1)
    (source / 'netlist').mkdir(parents=True)
    (source / 'lib').mkdir()
    oracle.mkdir()
    (source / 'lib/sky130.lib').write_bytes(library)
    (source / 'netlist/design.v').write_text('\n'.join(net) + '\n', encoding='utf-8')
    (source / 'task_spec.md').write_text(task, encoding='utf-8')
    (source / 'limitations.md').write_text('整个 case 的执行总时间不超过 300 秒。\n', encoding='utf-8')
    header = 'set script_dir /input\nset base_dir /output\nset out_rpt_dir /output/reports\nset out_res_dir /output/deliverables\n'
    (source / 'original.dofile').write_text(header + original.replace('$input_dir', '$script_dir'), encoding='utf-8')
    (oracle / 'correct.dofile').write_text(correct, encoding='utf-8')
    (oracle / 'faults.json').write_text(json.dumps(faults, indent=2), encoding='utf-8')
    print(json.dumps({'frozen': str(source), 'data_ff': 512, 'excluded_latches': 8, 'faults': len(faults)}))


def audit(source, output):
    result = verify(output)
    checks = result['checks']
    if not checks['accepted_final_run']:
        return result
    final = output / 'final_results'
    work = output / 'independent_hierarchy_audit'
    work.mkdir(exist_ok=False)
    before = read_design(source / 'netlist/design.v', source / 'lib/sky130.lib', work / 'before.json', 'reused_top')
    after = read_design(final / 'deliverables/post_scan.v', source / 'lib/sky130.lib', work / 'after.json', 'reused_top')
    structure, mismatches = functional_pins(before, after)
    checks.update(structure)
    checks['input_netlist_not_rewritten'] = not (final / 'deliverables/pre_scan_final.v').exists()
    expected = {f'a{i}.{side}.r{bit}' for i in range(4) for side in ('left', 'right') for bit in range(64)}
    rows = [line.split() for line in (final / 'reports/scan_chain.rpt').read_text().splitlines() if re.match(r'^I\s+\S+\s+\d+\s', line)]
    checks['partition_counts_clocks_enables_lengths'] = Counter((r[7], r[6], r[5], int(r[2])) for r in rows) == Counter(
        {(f'zone{i}', 'tick_a' if i < 2 else 'tick_b', f'shift_{i}', 64): 2 for i in range(4)})
    by_chain = {r[1]: r[7] for r in rows}
    seen, members, serial, domains, chain_sizes = [], {}, True, True, []
    ctl = (final / 'deliverables/post_scan.ctl').read_text()
    for name, body in re.findall(r'ScanChain\s+"([^"]+)"\s*\{([^}]+)\}', ctl):
        cells = re.findall(r'"([^"]+)"', re.search(r'ScanCells\s+(.*?);', body, re.S)[1])
        size = int(re.search(r'ScanLength\s+(\d+)', body)[1])
        si, so, se = [re.search(key + r'\s+"([^"]+)"', body)[1] for key in ('ScanIn', 'ScanOut', 'ScanEnable')]
        chain_sizes.append(size)
        members[name] = Counter(cells)
        serial &= size == len(cells) == 64 and bool(re.fullmatch(r'rx_si\d+', si) and re.fullmatch(r'rx_so\d+', so))
        previous = after['ports'][si]['bits']
        for cell in cells:
            qualified = cell.replace('/', '.')
            seen.append(qualified)
            branch = re.fullmatch(r'a([0-3])/(?:left|right)/r\d+', cell)
            domains &= bool(branch and by_chain.get(name) == 'zone' + branch[1] and se == 'shift_' + branch[1])
            pins = after['cells'][qualified]['connections']
            serial &= pins['SCD'] == previous and pins['SCE'] == after['ports'][se]['bits']
            previous = pins['Q']
        serial &= after['ports'][so]['bits'] == previous
    checks['exact_data_ff_membership'] = len(seen) == len(set(seen)) == 512 and set(seen) == expected
    checks['instance_specific_partition_and_enable'] = domains
    checks['actual_scan_connections'] = serial
    checks['scandef_exact_membership'] = scandef_members_match((final / 'deliverables/post_scan.def').read_text(), members)
    checks['drc_zero'] = bool(re.search(r'Total violations:\s*0\b', (final / 'reports/drc.rpt').read_text()))
    edge_rows = re.findall(r'\b(a[0-3]/(?:left|right)/r\d+)\s+(tick_[ab])\s+(\([+-]\))',
                           (final / 'reports/scan_cell.rpt').read_text())
    checks['reported_clock_edges_match_original_paths'] = len(edge_rows) == 512 and all(
        edge == ('(-)' if name.startswith('a3/') else '(+)') for name, _, edge in edge_rows)
    checks['eight_equal_chains'] = chain_sizes == [64] * 8
    result.update(passed=all(checks.values()), mismatches=mismatches[:30],
                  scope='Instance-specific structural and serial checks; diagnosis requires separate original-root review, not general LEC.')
    (work / 'acceptance.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'audit'])
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
    else:
        if args.input is None or args.output is None:
            parser.error('audit requires --input and --output')
        result = audit(args.input, args.output)
        print(json.dumps(result))
        raise SystemExit(0 if result.get('passed') else 1)
