"""Freeze one new six-fault I2C case before model evaluation.

Requires i2c.ys output. The reference and fault anchors are kept outside input/.
This source is fixture preparation, never imported by the submission.
"""
import json
from pathlib import Path


def main():
    suite = Path(__file__).resolve().parents[3] / 'benchmarks/opensource_20261001'
    source = suite / 'cases/P04_i2c_fifo/input'
    reference = suite / 'oracles/P04_i2c_fifo'
    if source.exists() or reference.exists():
        raise ValueError('Frozen I2C inputs/reference already exist; preserve them')
    design = suite / 'build/i2c01/design.v'
    library = suite / 'cases/P03_serv_clean/input/lib/sky130.lib'
    # Resolve the synthesized design and library before creating frozen outputs.
    design_text = design.read_text(encoding='utf-8')
    library_bytes = library.read_bytes()
    task = '''# I2C 控制器扫描修复

顶层 i2c_master_wbs_16，读取 lib/sky130.lib 与 netlist/design.v，诊断 original.dofile 的实际缺陷并交付修复。
这是带 Wishbone 接口和三个深度为 8 的 FIFO 的 I2C 主控制器；综合后有 462 个功能触发器，全部应参与扫描。
普通 sky130_fd_sc_hd__dfxtp_1 映射为 sky130_fd_sc_hd__sdfxtp_1；带数据使能的 sky130_fd_sc_hd__edfxtp_1 必须映射为 sky130_fd_sc_hd__sedfxtp_1，保持所有原始功能输入和输出的连接。
原始网表不得修改，不能排除数据 FF 或固定普通功能输入。rst 为同步功能复位，无需配置为扫描复位或常量。
唯一时钟为 clk，off_state=0；新增 scan_shift 为高有效数据扫描使能，off_state=0、usage=scan。
所有链处于 Default_Partition；扫描链总数为 7，每条不超过 72 个扫描位。chain_count=7、max_length=72、mix_clocks=false、mix_edges=false、internal_clocks=none、add_lockup=true。
扫描输入采用 i2c_si%d，扫描输出采用 i2c_so%d。不得启用 wrapper，不需要显式 scan segment。DRC 不得有违例，也不允许忽略或降级任何规则。
交付 post_scan.v、post_scan.ctl、post_scan.def；导出 drc.rpt、scan_chain.rpt、scan_cell.rpt、scan_cfg.rpt、scan_signal.rpt、scan_partition.rpt、insertion_info.rpt。所有报告对应最终插链结果，并保存每轮完整日志、修改 diff 和有原始证据的诊断报告。
'''
    correct = '''load_lib [file join $input_dir lib sky130.lib]
load_netlist [file join $input_dir netlist design.v]
present_design i2c_master_wbs_16
set_scan_cell_mapping sky130_fd_sc_hd__dfxtp_1 sky130_fd_sc_hd__sdfxtp_1
set_scan_cell_mapping sky130_fd_sc_hd__edfxtp_1 sky130_fd_sc_hd__sedfxtp_1
set_scan_signal -type clock -port clk -off_state 0
set_scan_signal -type scan_enable -port scan_shift -off_state 0 -usage scan
set_scan_cfg -chain_count 7 -max_length 72 -mix_clocks false -mix_edges false -internal_clocks none -add_lockup true -si_port_format i2c_si%d -so_port_format i2c_so%d
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
    defects = [
        ('enabled_mapping', 'sky130_fd_sc_hd__sedfxtp_1', 'sky130_fd_sc_hd__sdfxtp_1'),
        ('enable_polarity', '-port scan_shift -off_state 0', '-port scan_shift -off_state 1'),
        ('chain_count', '-chain_count 7', '-chain_count 5'),
        ('max_length', '-max_length 72', '-max_length 110'),
        ('mix_edges', '-mix_edges false', '-mix_edges true'),
        ('missing_scandef', 'dump_def -section scan_chain -file "$out_res_dir/post_scan.def"\n', ''),
    ]
    faulty = correct
    for name, before, after in defects:
        if faulty.count(before) != 1:
            raise ValueError(f'Fault anchor changed: {name}')
        faulty = faulty.replace(before, after, 1)
    (source / 'netlist').mkdir(parents=True)
    (source / 'lib').mkdir()
    reference.mkdir(parents=True)
    (source / 'netlist/design.v').write_text(design_text, encoding='utf-8')
    (source / 'lib/sky130.lib').write_bytes(library_bytes)
    (source / 'task_spec.md').write_text(task, encoding='utf-8')
    (source / 'limitations.md').write_text('整个 case 的执行总时间不超过 300 秒。\n', encoding='utf-8')
    header = ('set script_dir /input\nset base_dir /output\n'
              'set out_rpt_dir /output/reports\nset out_res_dir /output/deliverables\n')
    (source / 'original.dofile').write_text(header + faulty.replace('$input_dir', '$script_dir'), encoding='utf-8')
    (reference / 'correct.dofile').write_text(correct, encoding='utf-8')
    (reference / 'faults.json').write_text(json.dumps(defects, indent=2), encoding='utf-8')
    print(json.dumps({'frozen': str(source), 'ff': 462, 'original_defects': len(defects),
                      'scope': 'Fixture only; no model result'}))


if __name__ == '__main__':
    main()
