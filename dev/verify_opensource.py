"""Independent post-run audit of the pinned AES/UART/SERV gate-level tasks.

Read with Yosys rather than the submission's source-excerpt parser. Check all
functional pins, scan connectivity and exact membership. Task2 pre-scan edits
also require the existing real EQY proof. This is not RTL synthesis equivalence.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re

from verify_flow import verify
from verify_preprocessing import read_design
from verify_wrapper import scandef_members_match

MAPPINGS = {
    'sky130_fd_sc_hd__dfxtp_1': 'sky130_fd_sc_hd__sdfxtp_1',
    'sky130_fd_sc_hd__edfxtp_1': 'sky130_fd_sc_hd__sedfxtp_1',
    'sky130_fd_sc_hd__dfrtp_1': 'sky130_fd_sc_hd__sdfrtp_1',
    'sky130_fd_sc_hd__dfstp_2': 'sky130_fd_sc_hd__sdfstp_2',
}
CASES = {'uart': ('uart', 79, 4, 20), 'aes': ('aes_core', 2476, 16, 155),
         'serv': ('serv_rf_top', 1336, 8, 167), 'i2c': ('i2c_master_wbs_16', 462, 7, 72),
         'sha256': ('sha256_core', 1034, 11, 94), 'uart_plain': ('uart', 79, 4, 20)}


def functional_pins(before, after, retained_shift_cells=()):
    # Compare endpoint connectivity, not a shared wire-name alias. A wrongly
    # rewired output can keep its old name while losing its original driver.
    old = {n: c for n, c in before['cells'].items() if c['type'] != '$scopeinfo'}
    new = {n: c for n, c in after['cells'].items() if c['type'] != '$scopeinfo'}
    def endpoints(design):
        result = {}
        for name, cell in old.items():
            final_cell = design['cells'].get(name, {})
            for pin in cell['connections']:
                for index, bit in enumerate(final_cell.get('connections', {}).get(pin, [])):
                    result.setdefault(bit, set()).add(('cell', name, pin, index))
        for name in before['ports']:
            for index, bit in enumerate(design['ports'].get(name, {}).get('bits', [])):
                result.setdefault(bit, set()).add(('port', name, index))
        return result
    old_links, new_links = endpoints(before), endpoints(after)
    def same(left, right):
        return len(left) == len(right) and all(
            a == b if isinstance(a, str) or isinstance(b, str)
            else old_links[a] == new_links[b]
            for a, b in zip(left, right))
    mismatches = []
    for name, cell in old.items():
        replacement = new.get(name)
        expected_type = cell['type'] if name in retained_shift_cells else MAPPINGS.get(cell['type'], cell['type'])
        if replacement is None or replacement['type'] != expected_type:
            mismatches.append({'instance': name, 'reason': 'missing cell or incompatible replacement'})
            continue
        for pin, bits in cell['connections'].items():
            if not same(bits, replacement['connections'].get(pin, [])):
                mismatches.append({'instance': name, 'pin': pin, 'reason': 'functional connection changed'})
    for name, port in before['ports'].items():
        final = after['ports'].get(name)
        if final is None or final['direction'] != port['direction'] or not same(port['bits'], final['bits']):
            mismatches.append({'port': name, 'reason': 'original functional port changed'})
    return {'same_instance_set': old.keys() == new.keys(),
            'all_functional_pins_preserved': not mismatches}, mismatches


def audit(source, output, case):
    top, count, chain_count, max_length = CASES[case]
    result = verify(output)
    checks = result['checks']
    if not checks['accepted_final_run']:
        return result
    decision = json.loads((output / 'decision_log.json').read_text(encoding='utf-8'))
    run = output / 'runs' / decision['final_run']
    final = output / 'final_results'
    work = output / 'independent_netlist_audit'
    work.mkdir(exist_ok=False)
    library = source / 'lib/sky130.lib'
    original = read_design(source / 'netlist/design.v', library, work / 'original.json', top)
    pre_path = final / 'deliverables/pre_scan_final.v'
    used = read_design(pre_path, library, work / 'used.json', top) if pre_path.exists() else original
    post = read_design(final / 'deliverables/post_scan.v', library, work / 'post.json', top)
    if case in {'aes', 'sha256'}:
        checks['task_prohibits_prescan_edit'] = not pre_path.exists()
    elif pre_path.exists():
        lec = json.loads((run / 'lec_result.json').read_text())
        checks['original_to_used_eqy_pass'] = (lec['passed'] is True and lec['status'] == 'PASS'
            and lec['candidate_unchanged_after_scan'] is True
            and (run / 'equivalence/result/PASS').exists())
        checks['proved_prescan_is_scan_input'] = Path(lec['candidate']).name in (run / 'deliverables' / (run.name + '.dofile')).read_text()
    cells = post['cells']
    scanned = {name for name, c in cells.items() if c['type'] in MAPPINGS.values()}
    if case == 'uart_plain':
        checks['only_requested_scan_type'] = {cells[name]['type'] for name in scanned} == {'sky130_fd_sc_hd__sdfxtp_1'}
    # The tool can retain non-head plain DFFs of a shift register. Accept them
    # only when the actual D/Q chain below proves the serial link from a scan
    # head, and functional_pins proves that the original D connection survived.
    retained = {name for name, c in cells.items()
                if c['type'] == 'sky130_fd_sc_hd__dfxtp_1'
                and name in used['cells'] and used['cells'][name]['type'] == c['type']}
    structural, mismatches = functional_pins(used, post, retained)
    checks.update(structural)
    expected = {name for name, c in original['cells'].items()
                if c['type'] in MAPPINGS or c['type'] == 'sky130_fd_sc_hd__edfxtp_1'}
    checks['all_original_ff_once'] = len(expected) == len(scanned | retained) == count and scanned | retained == expected
    text = (final / 'deliverables/post_scan.ctl').read_text()
    blocks = re.findall(r'ScanChain\s+"([^"]+)"\s*\{([^}]+)\}', text)
    member_counts, serial_ok, seen, ports = {}, True, [], {'scan_shift'}
    chain_sizes = []
    for name, body in blocks:
        members = re.findall(r'"([^"]+)"', re.search(r'ScanCells\s+(.*?);', body, re.S)[1])
        size = int(re.search(r'ScanLength\s+(\d+)', body)[1])
        chain_sizes.append(size)
        seen += members
        member_counts[name] = Counter(members)
        si, so = [re.search(key + r'\s+"([^"]+)"', body)[1] for key in ('ScanIn', 'ScanOut')]
        se = re.search(r'ScanEnable\s+"([^"]+)"', body)[1]
        ports.update((si, so))
        serial_ok &= bool(members) and size == len(members) and se == 'scan_shift'
        serial_ok &= bool(re.fullmatch(case + r'_si\d+', si) and re.fullmatch(case + r'_so\d+', so))
        prior = post['ports'][si]['bits']
        for position, member in enumerate(members):
            if member not in scanned | retained:
                serial_ok = False
                continue
            pins = cells[member]['connections']
            if member in retained:
                serial_ok &= position > 0 and pins['D'] == prior
            else:
                serial_ok &= pins['SCD'] == prior and pins['SCE'] == post['ports']['scan_shift']['bits']
            prior = pins['Q']
        serial_ok &= post['ports'][so]['bits'] == prior
    checks['ctl_exact_membership'] = len(seen) == len(set(seen)) == count and set(seen) == expected
    checks['requested_chain_count_lengths'] = len(blocks) == chain_count and all(0 < n <= max_length for n in chain_sizes)
    checks['actual_serial_connectivity_and_enable'] = bool(serial_ok)
    checks['no_unrequested_ports'] = set(post['ports']) == set(original['ports']) | ports
    checks['scandef_exact_membership'] = scandef_members_match((final / 'deliverables/post_scan.def').read_text(), member_counts)
    cfg = (final / 'reports/scan_cfg.rpt').read_text()
    checks['configuration_values'] = all(re.search(r'(?m)^' + key + r'\s+' + value + r'\s*$', cfg)
        for key, value in [('chain_count', str(chain_count)), ('max_length', str(max_length)),
                           ('mix_clocks', 'False'), ('mix_edges', 'False'), ('internal_clocks', 'none')])
    checks['drc_zero'] = bool(re.search(r'Total violations:\s*0\b', (final / 'reports/drc.rpt').read_text()))
    if case == 'serv':
        # The raw SERV reference was originally labelled clean, but actual R1
        # export corruption disproved that label. Establish the no-change
        # control from the ORIGINAL emitted circuit, not from its case name.
        first_run = output / 'runs/R1'
        first_post = read_design(first_run / 'deliverables/post_scan.v', library, work / 'first_post.json', top)
        first_retained = {name for name, cell in first_post['cells'].items()
                          if cell['type'] == 'sky130_fd_sc_hd__dfxtp_1'
                          and name in original['cells'] and original['cells'][name]['type'] == cell['type']}
        first_checks, first_mismatches = functional_pins(original, first_post, first_retained)
        first_accepted = json.loads((first_run / 'acceptance.json').read_text())['accepted']
        result['original_functional_integrity'] = {'checks': first_checks, 'mismatches': first_mismatches[:30]}
        if first_accepted and all(first_checks.values()):
            checks['clean_original_no_invented_findings'] = not decision['issue_resolutions']
            checks['clean_original_no_unnecessary_changes'] = not decision['file_changes'] and decision['final_run'] == 'R1'
    result.update(passed=all(checks.values()), mismatches=mismatches[:30],
        chain_lengths=chain_sizes, scope='Actual gate-level functional pins and serial connectivity; EQY for pre-scan edits, not RTL-to-gates proof.')
    (work / 'acceptance.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', choices=list(CASES), required=True)
    args = parser.parse_args()
    result = audit(args.input, args.output, args.case)
    print(json.dumps(result))
    raise SystemExit(0 if result.get('passed') else 1)
