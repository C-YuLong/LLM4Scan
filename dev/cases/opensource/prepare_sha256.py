"""Freeze a six-fault SHA-256 task from pinned RTL synthesis, before model use."""
import json
from pathlib import Path


def main():
    suite = Path(__file__).resolve().parents[3] / 'benchmarks/opensource_20261001'
    source = suite / 'cases/P05_sha256/input'
    reference = suite / 'oracles/P05_sha256'
    if source.exists() or reference.exists():
        raise ValueError('Preserve frozen SHA-256 input and reference')
    build = suite / 'build/sha256_01'
    design = (build / 'design.v').read_text(encoding='utf-8')
    library = (suite / 'cases/P03_serv_clean/input/lib/sky130.lib').read_bytes()
    cells = json.loads((build / 'design.json').read_text())['modules']['sha256_core']['cells']
    expected = {'sky130_fd_sc_hd__dfrtp_1': 1033, 'sky130_fd_sc_hd__dfstp_2': 1}
    if {kind: sum(c['type'] == kind for c in cells.values()) for kind in expected} != expected:
        raise ValueError('Synthesis inventory differs from the frozen task')
    task = '''# SHA-256 核扫描修复

顶层 sha256_core，读取 lib/sky130.lib 与 netlist/design.v，检查 original.dofile 并修复真实存在的问题。
设计有 1034 个功能触发器，全部参与扫描；保持原始功能端口、单元功能引脚连接与实例集合。
1033 个 sky130_fd_sc_hd__dfrtp_1 必须映射为 sky130_fd_sc_hd__sdfrtp_1；一个 sky130_fd_sc_hd__dfstp_2 必须映射为 sky130_fd_sc_hd__sdfstp_2，不得丢失异步清零或置位功能。
禁止修改输入/Pre-scan 网表、排除功能 FF、固定普通功能输入或忽略/降级 DRC。DRC 违例必须为零。
clk 是唯一源时钟，off_state=0。reset_n 是低有效异步复位，扫描期间必须释放，off_state=1；该端口同时连接部分单元的异步置位引脚。
新增高有效 scan_shift 控制数据扫描，off_state=0，usage=scan。所有链属于 Default_Partition，不启用 Wrapper 或显式 Segment。
扫描链恰好 11 条，每条最多 94 位。chain_count=11、max_length=94、mix_clocks=false、mix_edges=false、internal_clocks=none、add_lockup=true。
扫描输入使用 sha256_si%d，扫描输出使用 sha256_so%d。
交付 post_scan.v、post_scan.ctl、post_scan.def；导出 drc.rpt、scan_chain.rpt、scan_cell.rpt、scan_cfg.rpt、scan_signal.rpt、scan_partition.rpt、insertion_info.rpt，并保存每轮完整日志、修改 diff、可追溯的诊断记录。报告必须来自最终实际交付轮次。
'''
    correct = '''load_lib [file join $input_dir lib sky130.lib]
load_netlist [file join $input_dir netlist design.v]
present_design sha256_core
set_scan_cell_mapping sky130_fd_sc_hd__dfrtp_1 sky130_fd_sc_hd__sdfrtp_1
set_scan_cell_mapping sky130_fd_sc_hd__dfstp_2 sky130_fd_sc_hd__sdfstp_2
set_scan_signal -type clock -port clk -off_state 0
set_scan_signal -type reset -port reset_n -off_state 1
set_scan_signal -type scan_enable -port scan_shift -off_state 0 -usage scan
set_scan_cfg -chain_count 11 -max_length 94 -mix_clocks false -mix_edges false -internal_clocks none -add_lockup true -si_port_format sha256_si%d -so_port_format sha256_so%d
examine_scan_drc -file "$out_rpt_dir/drc.rpt"
examine_scan_chain
insert_dft_logic
rpt_scan_chain -file "$out_rpt_dir/scan_chain.rpt"
rpt_scan_chain_cell -file "$out_rpt_dir/scan_cell.rpt"
rpt_scan_cfg -file "$out_rpt_dir/scan_cfg.rpt"
rpt_scan_signal -file "$out_rpt_dir/scan_signal.rpt"
rpt_scan_partition -file "$out_rpt_dir/scan_partition.rpt"
rpt_insertion_info -file "$out_rpt_dir/insertion_info.rpt"
dump_netlist -file "$out_res_dir/post_scan.v"
dump_ctl "$out_res_dir/post_scan.ctl"
dump_def -section scan_chain -file "$out_res_dir/post_scan.def"
exit
'''
    faults = [
        ('lost_reset_mapping', 'sky130_fd_sc_hd__sdfrtp_1', 'sky130_fd_sc_hd__sdfxtp_1'),
        ('reset_release', '-port reset_n -off_state 1', '-port reset_n -off_state 0'),
        ('chain_count', '-chain_count 11', '-chain_count 8'),
        ('max_length', '-max_length 94', '-max_length 150'),
        ('output_port_format', '-so_port_format sha256_so%d', '-so_port_format scan_out%d'),
        ('missing_ctl', 'dump_ctl "$out_res_dir/post_scan.ctl"\n', ''),
    ]
    faulty = correct
    for name, before, after in faults:
        if faulty.count(before) != 1:
            raise ValueError('Nonunique fault anchor: ' + name)
        faulty = faulty.replace(before, after, 1)
    (source / 'netlist').mkdir(parents=True)
    (source / 'lib').mkdir()
    reference.mkdir(parents=True)
    (source / 'netlist/design.v').write_text(design, encoding='utf-8')
    (source / 'lib/sky130.lib').write_bytes(library)
    (source / 'task_spec.md').write_text(task, encoding='utf-8')
    (source / 'limitations.md').write_text('整个 case 的执行总时间不超过 300 秒。\n', encoding='utf-8')
    header = 'set script_dir /input\nset base_dir /output\nset out_rpt_dir /output/reports\nset out_res_dir /output/deliverables\n'
    (source / 'original.dofile').write_text(header + faulty.replace('$input_dir', '$script_dir'), encoding='utf-8')
    (reference / 'correct.dofile').write_text(correct, encoding='utf-8')
    (reference / 'faults.json').write_text(json.dumps(faults, indent=2), encoding='utf-8')
    print(json.dumps({'frozen': str(source), 'ff': 1034, 'faults': len(faults)}))


if __name__ == '__main__':
    main()
