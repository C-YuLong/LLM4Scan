"""Six new control topologies; reuse the small-suite generator and independent audit.

Only input/ is mounted during model runs. Qualification is explicitly not a
model result. Each batch is immutable; do not edit tasks after model execution.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

import challenge_suite as base

ROOT = base.ROOT
SUITE = ROOT / 'benchmarks/control6'
CASES = {
    'H01_independent_masks': (512, False, 'separate functional masks, monolithic control'),
    'H02_low_bypass': (640, True, 'active-low bypass distinct from FF scan enable'),
    'H03_latched_enable': (768, False, 'low-phase latch holding the functional enable'),
    'H04_hierarchical_mux': (896, True, 'ordinary-named two-level hierarchy and alternate clock mux'),
    'H05_state_pipeline': (1024, True, 'two FFs propagating the test control; initialization required'),
    'H06_clean_alias': (1152, True, 'correct latched control with signal aliases; zero-defect control'),
    'H07_reset_mode': (1280, True, 'high-active reset through inversion and mode logic'),
}


def prepare():
    # Reuse existing netlist, task, reference-script and expected FF generation.
    base.CASES = {name: ('holdout', [('Default_Partition', 'pulse', 'move', n, 4)], 'reset' if name == 'H07_reset_mode' else 'gate', [], repair)
                  for name, (n, repair, _) in CASES.items()}
    base.prepare(SUITE / 'cases', list(CASES))
    for name, (n, repair, description) in CASES.items():
        folder = SUITE / 'cases' / name
        source = folder / 'input'
        oracle = json.loads((folder / 'oracle.json').read_text())
        path = source / 'netlist/design.v'
        design = path.read_text()
        last = design.index('sky130_fd_sc_hd__' + ('dfrtp' if name == 'H07_reset_mode' else 'dfxtp') + '_1 r0 ')
        first = last if name == 'H07_reset_mode' else design.index('wire allowed, gated;')
        extra_ports, extra_modules, setup, controls = '', '', [], ''
        if name == 'H01_independent_masks':
            extra_ports = ', input permit2'
            controls = '''wire a1, a2, middle, gated;
sky130_fd_sc_hd__or2_1 node11 (.A(permit), .B(move), .X(a1));
sky130_fd_sc_hd__or2_1 node12 (.A(permit2), .B(move), .X(a2));
sky130_fd_sc_hd__and2_1 node13 (.A(pulse), .B(a1), .X(middle));
sky130_fd_sc_hd__and2_1 node14 (.A(middle), .B(a2), .X(gated));
'''
            explanation = '两个功能许可输入互不相同；扫描必须同时打开两级门，不得固定功能许可输入来绕过控制逻辑。'
        elif name == 'H02_low_bypass':
            extra_ports = ', input knob'
            controls = '''wire b1, b2, gated;
sky130_fd_sc_hd__inv_1 node21 (.A(knob), .Y(b1));
sky130_fd_sc_hd__or2_1 node22 (.A(permit), .B(b1), .X(b2));
sky130_fd_sc_hd__and2_1 node23 (.A(pulse), .B(b2), .X(gated));
'''
            setup = ['set_scan_signal -type scan_enable -port knob -off_state 1 -usage clock_gating']
            explanation = '`knob` 是独立的门控测试使能（不是常量），必须按实际逻辑选择有效极性；`move` 只控制数据 FF 扫描。'
        elif name == 'H04_hierarchical_mux':
            extra_ports = ', input selector, input alternate'
            controls = 'wire gated;\nparcel node41 (.a(pulse), .b(alternate), .c(selector), .d(move), .e(permit), .o(gated));\n'
            extra_modules = '''module parcel(input a, input b, input c, input d, input e, output o);
piece node42 (.x(a), .y(b), .s(c), .t(d), .u(e), .v(o));
endmodule
module piece(input x, input y, input s, input t, input u, output v);
wire c1, c2;
sky130_fd_sc_hd__mux2_1 node43 (.A0(x), .A1(y), .S(s), .X(c1));
sky130_fd_sc_hd__or2_1 node44 (.A(t), .B(u), .X(c2));
sky130_fd_sc_hd__and2_1 node45 (.A(c1), .B(c2), .X(v));
endmodule
'''
            setup = ['set_scan_signal -type constant -port selector -constant_value 0']
            explanation = '扫描必须经原有选择器使用 pulse；alternate 是功能备选时钟，不能新增扫描域。请从实际层级连接确定 selector 的扫描常量。'
        elif name == 'H05_state_pipeline':
            extra_ports = ', input mode_pin'
            controls = '''wire d1, d2, d3, gated;
sky130_fd_sc_hd__dfxtp_1 node51 (.CLK(pulse), .D(mode_pin), .Q(d1));
sky130_fd_sc_hd__dfxtp_1 node52 (.CLK(pulse), .D(d1), .Q(d2));
sky130_fd_sc_hd__or2_1 node53 (.A(permit), .B(d2), .X(d3));
sky130_fd_sc_hd__and2_1 node54 (.A(pulse), .B(d3), .X(gated));
'''
            setup = ['set_scan_element false {node51 node52}', 'set_scan_drc_cfg -clock_gating_init_cycles 4',
                     'set_scan_signal -type constant -port mode_pin -constant_value 1']
            explanation = 'node51 和 node52 是控制寄存器，必须保留原类型和连接且不参与扫描；它们不计入数据 FF 数量。扫描模式端口 mode_pin 固定为 1。扫描前须使模式值经过这两个寄存器传播，不能排除数据 FF 来规避 DRC。'
        elif name == 'H07_reset_mode':
            design = design.replace('input clear_n', 'input clear_h')
            # Recompute offsets after changing a header identifier.
            first = last = design.index('sky130_fd_sc_hd__dfrtp_1 r0 ')
            extra_ports = ', input mode_pin'
            controls = '''wire f1, clear_n;
sky130_fd_sc_hd__inv_1 node71 (.A(clear_h), .Y(f1));
sky130_fd_sc_hd__or2_1 node72 (.A(f1), .B(mode_pin), .X(clear_n));
'''
            setup = ['set_scan_signal -type constant -port mode_pin -constant_value 0']
            explanation = '复位端口 `clear_h` 为高有效，扫描期间必须释放复位；扫描期间 mode_pin 固定为 0。'
        else:
            controls = '''wire e1, e2, e3, e4, gated;
sky130_fd_sc_hd__inv_1 node61 (.A(pulse), .Y(e1));
sky130_fd_sc_hd__or2_1 node62 (.A(permit), .B(move), .X(e2));
sky130_fd_sc_hd__dlxtp_1 node63 (.GATE(e1), .D(e2), .Q(e3));
sky130_fd_sc_hd__and2_1 node64 (.A(pulse), .B(e3), .X(e4));
assign gated = e4;
'''
            setup = ['set_scan_element false {node63}', 'set_scan_drc_cfg -clock_gating_init_cycles 2']
            explanation = '控制锁存器 node63 必须保留原类型和功能连接，不参与扫描，也不计入数据 FF 数量。扫描应通过其原有使能通路打开门控；不得凭空创建新的内部时钟源。'
            if name == 'H06_clean_alias':
                controls = controls.replace('.D(e2)', '.D(bridge)').replace('wire e1,', 'wire bridge;\nassign bridge = e2;\nwire e1,')
        design = design[:first] + controls + design[last:] + extra_modules
        design = design.replace('output [', extra_ports.lstrip(', ') + ', output [' if extra_ports else 'output [', 1)
        path.write_text(design, encoding='utf-8')
        header = ['# 数据寄存器扫描实施要求', '顶层 sample_top，读取 lib/sky130.lib 与 netlist/design.v。',
                  f'只有 r0 至 r{n-1} 这 {n} 个数据触发器需要插链；映射 dfxtp_1 到 sdfxtp_1（完整单元名前缀 sky130_fd_sc_hd__）。',
                  '功能数据连接不能改变；不得修改输入网表、任意排除数据 FF 或将 DRC 规则降级。DRC 检查无违例。',
                  '默认分区内恰好四条扫描链，每条至多 ' + str(n//4) + ' 个数据单元。',
                  '时钟端口 `pulse` 的 off_state 为 0。所有数据 FF 使用高有效扫描使能 `move`。',
                  '不得跨时钟或混合边沿；内部时钟设置为 none。', explanation]
        original_spec = (source / 'task_spec.md').read_text(encoding='utf-8')
        if name == 'H07_reset_mode':
            header[2] = header[2].replace('dfxtp_1', 'dfrtp_1').replace('sdfxtp_1', 'sdfrtp_1')
        (source / 'task_spec.md').write_text('\n'.join(header) + '\n' + original_spec[original_spec.index('## 输出要求'):], encoding='utf-8')
        (source / 'limitations.md').write_text('整个 case 的执行总时间不超过 480 秒。\n', encoding='utf-8')
        correct = oracle['correct_dofile']
        if name == 'H07_reset_mode':
            correct = correct.replace('reset -port clear_n -off_state 1', 'reset -port clear_h -off_state 0')
        if name == 'H02_low_bypass':
            correct = correct.replace('set_scan_signal -type scan_enable -port move\n', 'set_scan_signal -type scan_enable -port move -off_state 0 -usage scan\n')
        correct = correct.replace('examine_scan_drc', '\n'.join(setup) + '\nexamine_scan_drc', 1)
        original, defects = correct, []
        if name == 'H02_low_bypass':
            bad = setup[0].replace('-off_state 1', '-off_state 0')
            defects = [{'id': 'gate_polarity', 'before': setup[0], 'after': bad, 'anchors': [bad]}]
        elif name == 'H04_hierarchical_mux':
            bad = setup[0].replace('-constant_value 0', '-constant_value 1')
            defects = [{'id': 'mux_selection', 'before': setup[0], 'after': bad, 'anchors': [bad]}]
        elif name == 'H05_state_pipeline':
            bad = setup[1].replace('cycles 4', 'cycles 0')
            defects = [{'id': 'initialization', 'before': setup[1], 'after': bad, 'anchors': [bad]}]
        elif name == 'H07_reset_mode':
            for key, before, after in [('reset_polarity', 'set_scan_signal -type reset -port clear_h -off_state 0', 'set_scan_signal -type reset -port clear_h -off_state 1'),
                                       ('mode', setup[0], setup[0].replace('value 0', 'value 1'))]:
                defects.append({'id': key, 'before': before, 'after': after, 'anchors': [after]})
        for defect in defects:
            original = original.replace(defect['before'], defect['after'], 1)
        if repair:
            (source / 'original.dofile').write_text(original, encoding='utf-8')
        oracle.update(correct_dofile=correct, defects=defects, description=description,
                      control_cells={name: [kind, dict(re.findall(r'\.(\w+)\s*\(\s*([^()]+?)\s*\)', body))]
                                     for kind, name, body in re.findall(r'(sky130_fd_sc_hd__\w+)\s+(node\d+)\s*\((.*?)\);', controls+extra_modules, re.S)})
        (folder / 'oracle.json').write_text(json.dumps(oracle, indent=2), encoding='utf-8')
        # Qualification body has no runner-owned path declarations.
        body = '\n'.join(line for line in correct.splitlines() if not re.match(r'set (script_dir|base_dir|out_rpt_dir|out_res_dir) ', line)).replace('$script_dir', '$input_dir')
        (folder / 'correct.dofile').write_text(body, encoding='utf-8')
    (SUITE / 'README.md').write_text('# Control6 独立控制拓扑验证\n\n不是官方分数；模型只挂载各例 input/，不读取 oracle。\nH05 为额外探索例：参考可行性尚未成立，不计入六例模型成绩；原输入和失败记录保留。\n\n实测成绩、失败分析和复现命令见 [阶段文档](../../dev/控制拓扑与在线验收阶段.md)。\n\n' +
        '\n'.join(f'- {name}：{n} FF，{description}' for name, (n, _, description) in CASES.items()) + '\n', encoding='utf-8')


def audit(output, oracle, source):
    oracle = {**oracle, 'input_aliases': dict(re.findall(r'assign\s+(\S+)\s*=\s*(\S+)\s*;', (source / 'netlist/design.v').read_text()))}
    result = base.audit(output, oracle)
    decision = json.loads((output / 'decision_log.json').read_text(encoding='utf-8'))
    result['checks']['real_model_run'] = not decision.get('fixture_validation', False)
    if decision['final_run']:
        net = (output / 'final_results/deliverables/post_scan.v').read_text()
        instances = {name: (kind, dict(re.findall(r'\.(\w+)\s*\(\s*([^()]+?)\s*\)', body)))
                     for kind, name, body in re.findall(r'(sky130_fd_sc_hd__\w+)\s+(\w+)\s*\((.*?)\);', net, re.S)}
        aliases = dict(re.findall(r'assign\s+(\S+)\s*=\s*(\S+)\s*;', net))
        def resolve(value, links=aliases):
            visited = set()
            while value in links and value not in visited:
                visited.add(value)
                value = links[value]
            return value
        result['checks']['original_control_cells_and_connections'] = all(
            name in instances and instances[name][0] == kind and
            all(resolve(instances[name][1].get(pin)) == resolve(resolve(net, oracle['input_aliases'])) for pin, net in pins.items())
            for name, (kind, pins) in oracle['control_cells'].items())
    result['passed'] = all(result['checks'].values()) and result['diagnosis']['fp_or_duplicate'] == result['diagnosis']['fn'] == 0
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['prepare', 'run', 'qualify', 'score'])
    p.add_argument('--batch', default='experiment01')
    p.add_argument('--cases', nargs='+', choices=list(CASES))
    p.add_argument('--image', help='Frozen replay image; omit to run current local source')
    args = p.parse_args()
    if args.action == 'prepare':
        prepare()
        return
    if Path(args.batch).name != args.batch or args.batch in ('.', '..'):
        p.error('batch must be one directory name')
    batch = SUITE / ('qualification' if args.action == 'qualify' else 'runs') / args.batch
    old = {}
    if args.action == 'score':
        summary = json.loads((batch / 'score.json').read_text())
        old = {r['case']: r for r in summary['records']}
        args.image = summary['image']
    else:
        batch.mkdir(parents=True, exist_ok=False)
    records = []
    for name in args.cases or [n for n in CASES if n != 'H05_state_pipeline']:
        case = SUITE / 'cases' / name
        output = batch / name
        command = [sys.executable, '-X', 'utf8', str(ROOT / 'dev/run_local.py'), '--input-dir', str(case / 'input'), '--output-dir', str(output)]
        if args.image:
            command += ['--image', args.image]
        if args.action == 'qualify':
            command += ['--tool-dofile', str(case / 'correct.dofile')]
        if args.action == 'score':
            row = {k: old[name][k] for k in ('case', 'seconds', 'exit_code')}
        else:
            print(json.dumps({'starting': name, 'mode': args.action}), flush=True)
            started = time.monotonic()
            completed = subprocess.run(command, cwd=ROOT)
            row = {'case': name, 'seconds': round(time.monotonic()-started, 2), 'exit_code': completed.returncode}
        if args.action != 'qualify':
            row.update(audit(output, json.loads((case / 'oracle.json').read_text()), case / 'input'))
        records.append(row)
        (batch / 'score.json').write_text(json.dumps({'image': args.image, 'mode': 'qualify' if args.action == 'qualify' else 'run', 'records': records}, indent=2), encoding='utf-8')
        print(json.dumps(row), flush=True)


if __name__ == '__main__':
    main()
