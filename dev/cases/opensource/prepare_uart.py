"""Freeze six UART faults and an isolated, no-model EQY qualification rewrite.

Requires the pinned uart.ys synthesis and the initial uart_direct.dofile probe.
Reference edits stay outside input/. Never import them into the submission.
"""
import json
from pathlib import Path
import re


def main():
    suite = Path(__file__).resolve().parents[3] / 'benchmarks/opensource_20261001'
    source = suite / 'cases/P01_uart_enabled_v2/input'
    reference = suite / 'oracles/P01_uart_enabled_v2'
    original = source / 'original.dofile'
    edits_path = reference / 'edits.json'
    body_path = reference / 'correct.dofile'
    if any(p.exists() for p in (original, edits_path, body_path)):
        raise ValueError('Frozen UART files already exist; preserve them')
    text = (source / 'netlist/design.v').read_text(encoding='utf-8')
    edits = []
    for match in re.finditer(r'sky130_fd_sc_hd__edfxtp_1\s+(\S+)\s*\((.*?)\);', text, re.S):
        name, body = match.groups()
        pins = dict(re.findall(r'\.(\w+)\s*\((.*?)\)', body))
        wire, mux = 'hold_d' + name, 'hold_mux' + name
        if re.search(r'\b(?:' + wire + '|' + mux + r')\b', text):
            raise ValueError('Qualification name collision')
        replacement = (f'wire {wire};\n'
            f'sky130_fd_sc_hd__mux2_1 {mux} (.A0({pins["Q"]}), .A1({pins["D"]}), '
            f'.S({pins["DE"]}), .X({wire}));\n'
            f'sky130_fd_sc_hd__dfxtp_1 {name} (.CLK({pins["CLK"]}), .D({wire}), .Q({pins["Q"]}));')
        edits.append({'path': 'netlist/design.v', 'before': match[0], 'after': replacement})
    if len(edits) != 17:
        raise ValueError('Pinned fixture no longer has the expected 17 enabled FFs')
    direct = (suite / 'oracles/uart_direct.dofile').read_text(encoding='utf-8')
    mapping = 'set_scan_cell_mapping sky130_fd_sc_hd__edfxtp_1 sky130_fd_sc_hd__sdfxtp_1\n'
    correct = direct.replace(mapping, '')
    faulty = direct
    for good, bad in (
        ('-port scan_shift -off_state 0', '-port scan_shift -off_state 1'),
        ('-chain_count 4', '-chain_count 2'),
        ('-max_length 20', '-max_length 40'),
        ('-mix_clocks false', '-mix_clocks true'),
        ('dump_ctl "$out_res_dir/post_scan.ctl"\n', ''),
    ):
        if faulty.count(good) != 1:
            raise ValueError('Fault anchor changed')
        faulty = faulty.replace(good, bad)
    reference.mkdir(parents=True, exist_ok=False)
    # original.dofile must be executable on its own, unlike a runner body.
    header = ('set script_dir /input\nset base_dir /output\n'
              'set out_rpt_dir /output/reports\nset out_res_dir /output/deliverables\n')
    original.write_text(header + faulty.replace('$input_dir', '$script_dir'), encoding='utf-8')
    body_path.write_text(correct, encoding='utf-8')
    edits_path.write_text(json.dumps({'edits': edits}, indent=2), encoding='utf-8')
    print(json.dumps({'enabled_ff_rewrites': len(edits), 'original_defects': 6,
                      'scope': 'Fixture preparation only; no model success claimed'}))


if __name__ == '__main__':
    main()
