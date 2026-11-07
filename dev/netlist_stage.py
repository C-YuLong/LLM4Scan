"""Isolated Task2 netlist-repair examples; six faults, no oracle mounted."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

import challenge_suite as base

ROOT = base.ROOT
SUITE = ROOT / 'benchmarks/netlist2'
CASES = {'N01_clock_reconvergence': 32, 'N02_reset_reconvergence': 96, 'N03_dual_clock': 128}


def prepare(names):
    base.CASES = {name: ('holdout', [('Default_Partition', 'tick', 'shift', n, 4)], 'reset', [], True)
                  for name, n in CASES.items() if name in names}
    base.prepare(SUITE / 'cases', names)
    for name in names:
        n = CASES[name]
        folder = SUITE / 'cases' / name
        source = folder / 'input'
        path = source / 'netlist/design.v'
        text = path.read_text()
        text = text.replace('input clear_n,', 'input clear_n, input permit,')
        if name.startswith(('N01', 'N03')):
            before = ('sky130_fd_sc_hd__inv_1 g0 (.A(permit), .Y(np));\n'
                      'sky130_fd_sc_hd__and2_1 g1 (.A(tick), .B(permit), .X(a));\n'
                      'sky130_fd_sc_hd__and2_1 g2 (.A(tick), .B(np), .X(b));\n'
                      'sky130_fd_sc_hd__or2_1 g3 (.A(a), .B(b), .X(route));')
            after = 'assign route = tick;'
            if name.startswith('N03'):
                before = before.replace('__and2_1 g1', '__or2_1 g1').replace('__and2_1 g2', '__or2_1 g2').replace('__or2_1 g3', '__and2_1 g3')
            text = text.replace('.CLK(tick)', '.CLK(route)')
        else:
            before = ('sky130_fd_sc_hd__inv_1 g0 (.A(permit), .Y(np));\n'
                      'sky130_fd_sc_hd__or2_1 g1 (.A(clear_n), .B(permit), .X(a));\n'
                      'sky130_fd_sc_hd__or2_1 g2 (.A(clear_n), .B(np), .X(b));\n'
                      'sky130_fd_sc_hd__and2_1 g3 (.A(a), .B(b), .X(route));')
            after = 'assign route = clear_n;'
            text = text.replace('.RESET_B(clear_n)', '.RESET_B(route)')
        index = text.index('sky130_fd_sc_hd__dfrtp_1 r0')
        text = text[:index] + 'wire np, a, b, route;\n' + before + '\n' + text[index:]
        path.write_text(text, encoding='utf-8')
        spec = (source / 'task_spec.md').read_text(encoding='utf-8')
        spec = spec.replace('不得改输入网表、排除寄存器或忽略/降级 DRC 规则。',
            '不得覆盖输入文件、排除寄存器或忽略/降级 DRC 规则。允许在输出目录创建等价修改的 Pre-scan 网表，必须用 Yosys EQY 对原始与最终采用的 Pre-scan 证明功能等价。')
        spec += ('\n所有内部链均在 Default_Partition。`shift` 为高有效扫描使能，off_state=0。\n'
                 'permit 是任意功能输入，不允许将它声明成常量或测试控制；功能等价证明不得固定任何输入。\n'
                 f'配置 chain_count 必须为 4，max_length 必须为 {n//4}，mix_clocks 必须为 false。\n')
        (source / 'task_spec.md').write_text(spec, encoding='utf-8')
        (source / 'limitations.md').write_text('整个 case 的执行总时间不超过 480 秒。\n', encoding='utf-8')
        oracle = json.loads((folder / 'oracle.json').read_text())
        correct = oracle['correct_dofile'].replace('-port shift\n', '-port shift -off_state 0\n')
        substitutions = [
            ('reset', '-port clear_n -off_state 1', '-port clear_n -off_state 0'),
            ('enable', '-port shift -off_state 0', '-port shift -off_state 1'),
            ('chains', '-chain_count 4', '-chain_count 2'),
            ('length', f'-max_length {n//4}', f'-max_length {n//2}'),
            ('mix', '-mix_clocks false', '-mix_clocks true'),
        ]
        original = correct
        for _, good, bad in substitutions:
            original = original.replace(good, bad)
        (source / 'original.dofile').write_text(original, encoding='utf-8')
        body = '\n'.join(line for line in correct.splitlines() if not re.match(r'set (script_dir|base_dir|out_rpt_dir|out_res_dir) ', line)).replace('$script_dir', '$input_dir')
        (folder / 'correct.dofile').write_text(body, encoding='utf-8')
        oracle.update(edits=[{'path': 'netlist/design.v', 'before': before, 'after': after}],
                      expected_defects=['structure']+[x[0] for x in substitutions], correct_dofile=correct)
        (folder / 'oracle.json').write_text(json.dumps(oracle, indent=2), encoding='utf-8')


def main():
    global SUITE
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['prepare', 'run', 'score'])
    p.add_argument('--batch', default='frozen18')
    p.add_argument('--cases', nargs='+', choices=list(CASES))
    p.add_argument('--image', help='Frozen replay image; omit to run current local source')
    p.add_argument('--suite-dir', type=Path, default=SUITE)
    args = p.parse_args()
    SUITE = args.suite_dir.resolve()
    if args.action == 'prepare':
        prepare(args.cases or list(CASES))
        return
    if Path(args.batch).name != args.batch or args.batch in {'.', '..'}:
        p.error('batch must be one directory name')
    for name in args.cases or CASES:
        output = SUITE / 'runs' / args.batch / name
        if args.action == 'score':
            oracle = json.loads((SUITE / 'cases' / name / 'oracle.json').read_text())
            decision = json.loads((output / 'decision_log.json').read_text(encoding='utf-8'))
            if decision['final_run']:
                run = output / 'runs' / decision['final_run']
                modified = run / 'deliverables' / f'pre_scan_{run.name}.v'
                used_pre = modified if modified.exists() else SUITE / 'cases' / name / 'input/netlist/design.v'
                pre = used_pre.read_text()
                oracle['input_aliases'] = dict(re.findall(r'assign\s+(\S+)\s*=\s*(\S+)\s*;', pre))
                for cell, body in re.findall(r'sky130_fd_sc_hd__dfrtp_1\s+(\w+)\s*\((.*?)\);', pre, re.S):
                    if cell in oracle['cells']:
                        oracle['cells'][cell]['reset'] = dict(re.findall(r'\.(\w+)\s*\(\s*([^()]+?)\s*\)', body))['RESET_B']
            result = base.audit(output, oracle)
            checks = result['checks']
            if decision['final_run']:
                run = output / 'runs' / decision['final_run']
                if modified.exists():
                    lec = json.loads((run / 'lec_result.json').read_text())
                    checks['real_eqy_passed'] = lec['passed'] is True and lec['status'] == 'PASS'
                    checks['proved_file_is_scan_input'] = lec['candidate'].split('/')[-1] in (run / 'deliverables' / (run.name+'.dofile')).read_text()
                    checks['netlist_diff_and_lec_reference'] = any(c['type'] == 'netlist' and
                        (output / c['diff_path']).is_file() and (output / c['lec_ref']).is_file() for c in decision['file_changes'])
            checks['real_model_run'] = not decision.get('fixture_validation', False)
            score = {'case': name, 'artifact_passed': all(checks.values()), 'checks': checks,
                'expected_independent_defects': oracle['expected_defects'],
                'reported_issues': len(decision['issue_resolutions']), 'diagnosis_requires_manual_review': True,
                'scope': 'Independent artifacts plus real EQY; issue count alone is not a correctness score.'}
            (output / 'score.json').write_text(json.dumps(score, indent=2), encoding='utf-8')
            print(json.dumps(score))
            continue
        started = time.monotonic()
        command = [sys.executable, '-X', 'utf8', str(ROOT / 'dev/run_local.py'), '--input-dir',
            str(SUITE / 'cases' / name / 'input'), '--output-dir', str(SUITE / 'runs' / args.batch / name)]
        if args.image:
            command += ['--image', args.image]
        completed = subprocess.run(command)
        (output / 'run_metadata.json').write_text(json.dumps({'image': args.image, 'seconds': round(time.monotonic()-started, 2),
            'exit_code': completed.returncode}), encoding='utf-8')


if __name__ == '__main__':
    main()
