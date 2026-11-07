"""Freeze and audit a small three-domain missing-control regression.

Concrete target: different omitted reset/preset controls must not collapse into
one diagnosis merely because they share the same Tcl insertion point/DRC code.
The model sees input/ only; the reference remains outside that directory.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify_opensource

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / 'benchmarks/independent_resets_20261002'


def prepare():
    source, oracle = SUITE / 'input', SUITE / 'oracle'
    if source.exists() or oracle.exists():
        raise ValueError('Preserve frozen inputs and reference')
    design = ['module async_top(input clk, ctl_a, ctl_b, ctl_c, input [92:0] data, output [92:0] value);',
              'wire preset_n;', 'sky130_fd_sc_hd__inv_1 polar (.A(ctl_b), .Y(preset_n));']
    for bit in range(93):
        kind, pin, control = (('dfrtp_1', 'RESET_B', 'ctl_a') if bit < 17 else
                              ('dfstp_2', 'SET_B', 'preset_n') if bit < 46 else
                              ('dfrtp_1', 'RESET_B', 'ctl_c'))
        design.append(f'sky130_fd_sc_hd__{kind} r{bit} (.CLK(clk), .D(data[{bit}]), .Q(value[{bit}]), .{pin}({control}));')
    design += ['endmodule']
    task = '''# 三组独立异步控制的扫描实施

顶层 async_top，使用 lib/sky130.lib 与 netlist/design.v。修复 original.dofile 中真实存在的问题并给出可追溯的诊断。
设计有 93 个数据触发器，全部扫描，保留所有功能端口、原有实例及其功能引脚连接。禁止修改 Pre-scan 网表、固定普通功能输入、排除数据 FF 或忽略/降级 DRC。
sky130_fd_sc_hd__dfrtp_1 必须映射为 sky130_fd_sc_hd__sdfrtp_1；sky130_fd_sc_hd__dfstp_2 必须映射为 sky130_fd_sc_hd__sdfstp_2。
clk 是唯一源时钟，off_state=0。扫描期间必须释放所有原有异步控制；根据实际库单元及连接确定 ctl_a、ctl_b、ctl_c 各自的释放值。data 为正常功能输入，不得固定。
新增 scan_shift 为高有效数据扫描使能，off_state=0、usage=scan。仅有 Default_Partition，恰好 3 条扫描链，每条最多 31 位。
chain_count=3、max_length=31、mix_clocks=false、mix_edges=false、internal_clocks=none、add_lockup=true；扫描输入 async_si%d、扫描输出 async_so%d。不使用 Wrapper 或显式 Segment，DRC 必须为零。
交付 post_scan.v、post_scan.ctl、post_scan.def；导出 drc.rpt、scan_chain.rpt、scan_cell.rpt、scan_cfg.rpt、scan_signal.rpt、scan_partition.rpt、insertion_info.rpt。保存真实日志、修改 diff 与最终诊断记录。
'''
    releases = ['set_scan_signal -type reset -port ctl_a -off_state 1',
                'set_scan_signal -type reset -port ctl_b -off_state 0',
                'set_scan_signal -type reset -port ctl_c -off_state 1']
    lines = ['load_lib [file join $input_dir lib sky130.lib]',
             'load_netlist [file join $input_dir netlist design.v]', 'present_design async_top',
             'set_scan_cell_mapping sky130_fd_sc_hd__dfrtp_1 sky130_fd_sc_hd__sdfrtp_1',
             'set_scan_cell_mapping sky130_fd_sc_hd__dfstp_2 sky130_fd_sc_hd__sdfstp_2',
             'set_scan_signal -type clock -port clk -off_state 0', *releases,
             'set_scan_signal -type scan_enable -port scan_shift -off_state 0 -usage scan',
             'set_scan_cfg -chain_count 3 -max_length 31 -mix_clocks false -mix_edges false -internal_clocks none -add_lockup true -si_port_format async_si%d -so_port_format async_so%d',
             'examine_scan_drc -file "$out_rpt_dir/drc.rpt"', 'examine_scan_chain', 'insert_dft_logic']
    lines += [f'{command} -file "$out_rpt_dir/{report}.rpt"' for command, report in (
        ('rpt_scan_chain', 'scan_chain'), ('rpt_scan_chain_cell', 'scan_cell'), ('rpt_scan_cfg', 'scan_cfg'),
        ('rpt_scan_signal', 'scan_signal'), ('rpt_scan_partition', 'scan_partition'), ('rpt_insertion_info', 'insertion_info'))]
    lines += ['dump_netlist -file "$out_res_dir/post_scan.v"', 'dump_ctl "$out_res_dir/post_scan.ctl"',
              'dump_def -section scan_chain -file "$out_res_dir/post_scan.def"', 'exit']
    correct = '\n'.join(lines) + '\n'
    original = '\n'.join(line for line in lines if line not in releases) + '\n'
    (source / 'netlist').mkdir(parents=True)
    (source / 'lib').mkdir()
    oracle.mkdir()
    (source / 'lib/sky130.lib').write_bytes((ROOT / 'official/extracted/public_cases/task_1/case4/input/lib/sky130.lib').read_bytes())
    (source / 'netlist/design.v').write_text('\n'.join(design) + '\n', encoding='utf-8')
    (source / 'task_spec.md').write_text(task, encoding='utf-8')
    (source / 'limitations.md').write_text('整个 case 的执行总时间不超过 240 秒。\n', encoding='utf-8')
    header = 'set script_dir /input\nset base_dir /output\nset out_rpt_dir /output/reports\nset out_res_dir /output/deliverables\n'
    (source / 'original.dofile').write_text(header + original.replace('$input_dir', '$script_dir'), encoding='utf-8')
    (oracle / 'correct.dofile').write_text(correct, encoding='utf-8')
    (oracle / 'faults.json').write_text(json.dumps({'omitted_declarations': releases, 'groups': [17, 29, 47]}, indent=2), encoding='utf-8')
    print(json.dumps({'frozen': str(source), 'ff': 93, 'independent_controls': 3}))


def main():
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
        # Reuse the actual gate-level pin/serial/CTL audit; this synthetic task
        # deliberately uses its established report/scan-enable conventions.
        verify_opensource.CASES['async'] = ('async_top', 93, 3, 31)
        result = verify_opensource.audit(args.input, args.output, 'async')
        print(json.dumps(result))
        raise SystemExit(0 if result.get('passed') else 1)


if __name__ == '__main__':
    main()
