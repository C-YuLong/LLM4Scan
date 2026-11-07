"""Audit real output provenance; optional public T2/C1 acceptance from its task spec."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re


def verify(root, picorv32=False, cv32e40p=False, ethernet=False, tv80=False, ac97=False, openc906=False, veer=False):
    decision = json.loads((root / "decision_log.json").read_text(encoding="utf-8"))
    checks = {}
    final_id = decision["final_run"]
    checks["accepted_final_run"] = bool(final_id)
    if not final_id:
        return {"checks": checks, "scope": "No accepted final run"}
    run = root / "runs" / final_id
    checks["execution_completed"] = json.loads((run / "execution.json").read_text())["exit_status"] == "completed"
    expected_files = {}
    for group in ("deliverables", "reports"):
        for path in (run / group).rglob("*"):
            if path.is_file():
                name = path.relative_to(run).as_posix()
                if name == f"deliverables/{final_id}.dofile":
                    name = "deliverables/final.dofile"
                elif name == f'deliverables/pre_scan_{final_id}.v':
                    name = 'deliverables/pre_scan_final.v'
                expected_files[name] = path
    expected_files["final.log"] = run / f"{final_id}.log"
    final = root / "final_results"
    checks["final_file_set"] = set(expected_files) == {p.relative_to(final).as_posix() for p in final.rglob("*") if p.is_file()}
    checks["final_bytes_match_run"] = all((final / name).is_file() and path.read_bytes() == (final / name).read_bytes() for name, path in expected_files.items())

    def reference(ref):
        path = (root / ref["source"]).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            return False
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        match = re.fullmatch(r"L(\d+)(?:-L(\d+))?", ref["locator"])
        if not match:
            return False
        start, end = int(match[1]), int(match[2] or match[1])
        excerpt = ref.get("excerpt", "")
        if not excerpt or not 1 <= start <= end <= len(lines):
            return False
        # Static commands may be substrings of a line; multiline excerpts must
        # start at the reported source line and retain all intervening text.
        return excerpt in "\n".join(lines[start - 1:max(end, start + len(excerpt.splitlines()) - 1)])

    changes = {item["change_id"]: item for item in decision["file_changes"]}
    checks["diff_files_exist"] = all((root / c["path"]).is_file() and (root / c["diff_path"]).is_file() for c in changes.values())
    refs = []
    for issue in decision["issue_resolutions"]:
        refs.append(reference(issue["found"]))
        for attempt in issue["attempts"]:
            refs.append(all(key in changes for key in attempt["fix"]["artifact_ref"]))
            if attempt["verify"]["resolved"]:
                refs.append(reference(attempt["verify"]))
    for item in decision.get("requirement_mapping", []):
        refs.append(reference(dict(item["config_ref"], excerpt=item["dft_config"])))
    checks["evidence_references_valid"] = all(refs)
    result = {"checks": checks, "scope": "Artifact provenance and evidence references; not general semantic acceptance"}
    if picorv32 or cv32e40p:
        reports = final / "reports"
        chains = [line.split() for line in (reports / "scan_chain.rpt").read_text().splitlines() if re.match(r"^I\s+\S+\s+\d+\s", line)]
    if picorv32:
        cells = []
        per_chain = Counter()
        chain = None
        report_texts = [p.read_text(encoding="utf-8", errors="replace") for p in reports.glob("*.rpt")]
        cell_report = next((t for t in report_texts if "InstanceName" in t and "CellNo" in t and "RefName" in t), "")
        for line in cell_report.splitlines():
            match = re.match(r"^\s*(?:I\s+(\S+)\s+)?(\d+)\s+(\S+)\s+(\S+)\s+\(([^)]+)\)\s+(\S+)", line)
            if match:
                chain = match[1] or chain
                cells.append((chain, int(match[2]), match[3], match[4], match[5], match[6]))
                per_chain[chain] += 1
        checks["four_chains_clk_test_se"] = len(chains) == 4 and all(c[5:7] == ["test_se", "clk"] for c in chains)
        checks["1610_unique_scan_cells"] = len(cells) == len({c[2] for c in cells}) == 1610
        checks["cell_clock_edge_mapping"] = bool(cells) and all(c[3:] == ("clk", "+", "sky130_fd_sc_hd__sdfxtp_1") for c in cells)
        checks["chain_lengths_match_cells"] = bool(chains) and {c[1]: int(c[2]) for c in chains} == dict(per_chain)
        checks["cell_indices_contiguous"] = all([c[1] for c in cells if c[0] == name] == list(range(count)) for name, count in per_chain.items())
        drc_counts = [int(n) for text in report_texts for n in re.findall(r'Total violations:\s+(\d+)\b', text)]
        checks["drc_zero"] = bool(drc_counts) and all(n == 0 for n in drc_counts)
        result.update({"chain_lengths": [int(c[2]) for c in chains], "scan_cells": len(cells), "scope": "Public T2/C1 report acceptance plus provenance; not functional equivalence"})
    if cv32e40p:
        required = ["deliverables/post_scan.v", "deliverables/post_scan.ctl", "deliverables/post_scan.def"]
        required += [f"reports/{name}.rpt" for name in ("drc", "scan_chain", "scan_cfg", "scan_signal", "insertion_info")]
        checks["required_outputs_present"] = all((final / name).is_file() and (final / name).stat().st_size > 0 for name in required)
        checks["chain_length_at_most_100"] = bool(chains) and all(0 < int(c[2]) <= 100 for c in chains)
        checks["chain_clock_enable_ports"] = bool(chains) and all(
            c[5:7] == ["scan_cg_en_i", "clk_i"] and re.fullmatch(r"scan_data_in_\d+", c[3]) and re.fullmatch(r"scan_data_out_\d+", c[4]) for c in chains)
        cfg = dict(re.findall(r"(?m)^\s*(\w+)\s+(\S+)\s*$", (reports / "scan_cfg.rpt").read_text()))
        expected = {"max_length": "100", "mix_clocks": "False", "mix_edges": "True", "add_lockup": "True", "si_port_format": "scan_data_in_%d", "so_port_format": "scan_data_out_%d"}
        checks["scan_configuration"] = all(cfg.get(k) == v for k, v in expected.items())
        signals = [line.split() for line in (reports / "scan_signal.rpt").read_text().splitlines()]
        checks["clock_reset_enable_configuration"] = all(any(row[:len(prefix)] == prefix and ("all" in row if prefix[0] == "scan_cg_en_i" else True) for row in signals) for prefix in (
            ["clk_i", "pre_existing", "clock", "0"], ["rst_ni", "pre_existing", "reset", "1"], ["scan_cg_en_i", "pre_existing", "scan_enable(spec)", "0"]))
        checks["drc_zero"] = bool(re.search(r"Total violations:\s+0\b", (reports / "drc.rpt").read_text()))
        log = (final / "final.log").read_text(encoding="utf-8", errors="replace")
        overrides = re.findall(r"CMD-0034\]\s+@\d+:\s*set_scan_drc_rule_handling\s+\{([^}]+)\}\s+(\w+)", log)
        checks["only_permitted_drc_ignores"] = bool(overrides) and all(set(ids.split()) <= {"DFTR-TIE0", "DFTR-TIE1"} and level == "Ignore" for ids, level in overrides) and {rule for ids, _ in overrides for rule in ids.split()} == {"DFTR-TIE0", "DFTR-TIE1"}
        ctl = (final / "deliverables/post_scan.ctl").read_text()
        blocks = re.findall(r'ScanChain\s+"([^"]+)"\s*\{([^}]+)\}', ctl)
        ctl_rows, all_cells = [], []
        for name, body in blocks:
            length = re.search(r"ScanLength\s+(\d+);", body)
            cells_match = re.search(r"ScanCells\s+(.*?);", body, re.S)
            cells = re.findall(r'"([^"]+)"', cells_match[1]) if cells_match else []
            values = [re.search(key + r'\s+"([^"]+)";', body) for key in ("ScanIn", "ScanOut", "ScanEnable", "ScanMasterClock")]
            if length and all(values) and len(cells) == int(length[1]):
                ctl_rows.append([name, length[1]] + [m[1] for m in values])
            all_cells.extend(cells)
        checks["ctl_matches_chain_report"] = bool(blocks) and len(blocks) == len(ctl_rows) == len(chains) and sorted(ctl_rows) == sorted(c[1:7] for c in chains)
        info = (reports / "insertion_info.rpt").read_text()
        scannable = re.search(r"(?m)^\s*\d+\s+Scannable FF Count\s+(\d+)", info)
        chain_count = re.search(r"(?m)^\s*\d+\s+Scan Chain Cell Count\s+(\d+)", info)
        checks["reported_scannable_coverage_and_ctl_uniqueness"] = bool(scannable and chain_count) and int(scannable[1]) == int(chain_count[1]) == sum(int(c[2]) for c in chains) == len(all_cells) == len(set(all_cells))
        scandef = (final / "deliverables/post_scan.def").read_text()
        count = re.search(r"SCANCHAINS\s+(\d+)\s*;", scandef)
        checks["scandef_chain_count"] = bool(count) and int(count[1]) == len(chains) and "END SCANCHAINS" in scandef
        result.update({"chains": len(chains), "chain_lengths": [int(c[2]) for c in chains], "ctl_unique_cells": len(set(all_cells)), "scope": "Public T2/C4 reports and CTL consistency; not netlist connectivity or functional equivalence proof"})
    if ethernet or tv80:
        # Public development oracles, not runtime case branching. File names are
        # intentionally free: identify actual report types from their headers.
        texts = [p.read_text(encoding="utf-8", errors="replace") for p in (final / "reports").glob("*.rpt")]
        chain_text = next((t for t in texts if "ChainProperty" in t and "ScanEnable" in t), "")
        cell_text = next((t for t in texts if "InstanceName" in t and "CellNo" in t and "RefName" in t), "")
        chains = [line.split() for line in chain_text.splitlines() if re.match(r"^I\s+\S+\s+\d+\s", line)]
        cells, per_chain, current = [], Counter(), None
        indices = {}
        for line in cell_text.splitlines():
            match = re.match(r"^\s*(?:I\s+(\S+)\s+)?(\d+)\s+(\S+)\s+(\S+)\s+\([^)]+\)\s+(\S+)", line)
            if match:
                current = match[1] or current
                cells.append(match[3])
                indices.setdefault(current, []).append(int(match[2]))
                per_chain[current] += 1
        expected_cells = 10543 if ethernet else 359
        checks["expected_unique_chain_cells"] = len(cells) == len(set(cells)) == expected_cells
        checks["chain_rows_match_cells"] = bool(chains) and {c[1]: int(c[2]) for c in chains} == dict(per_chain)
        checks["chain_indices_contiguous"] = bool(indices) and all(values == list(range(len(values))) for values in indices.values())
        totals = [int(n) for t in texts for n in re.findall(r"Total violations:\s*(\d+)", t)]
        if ethernet:
            expected = {"wb_partition": (34, "wb_clk_i", "se_wb"), "tx_partition": (1, "mtx_clk_pad_i", "se_tx"), "rx_partition": (1, "mrx_clk_pad_i", "se_rx")}
            checks["three_partition_counts_clocks_enables"] = len(chains) == 36 and all(
                len([c for c in chains if c[7] == name]) == count and
                all(c[5:7] == [enable, clk] for c in chains if c[7] == name)
                for name, (count, clk, enable) in expected.items())
            checks["maximum_chain_length_300"] = bool(chains) and all(0 < int(c[2]) <= 300 for c in chains)
            # Development oracle expressly permits the 21 inherent DFTR10 warnings.
            failures = [rule for t in texts for rule in re.findall(r"DRC rule '(DFTR[^']+)' fails", t)]
            checks["only_known_residual_drc"] = bool(totals) and all(n in (0, 21) for n in totals) and set(failures) <= {"DFTR10"}
        else:
            checks["at_most_five_chains"] = 0 < len(chains) <= 5
            checks["chain_enable_and_port_format"] = bool(chains) and all(c[5] == "scan_cg_en" and re.fullmatch(r"scan_si_\d+", c[3]) and re.fullmatch(r"scan_so_\d+", c[4]) for c in chains)
            checks["drc_zero"] = bool(totals) and all(n == 0 for n in totals)
            checks["clock_latch_not_scanned"] = "u_clk_latch" not in cells
        result.update({"chains": len(chains), "chain_lengths": [int(c[2]) for c in chains], "unique_cells": len(set(cells)), "drc_totals": totals,
                       "scope": "Public Task2 report/unique-cell acceptance and provenance; not netlist connectivity or equivalence proof"})
    if ac97:
        texts = [p.read_text(encoding="utf-8", errors="replace") for p in (final / "reports").glob("*.rpt")]
        chain_text = next((t for t in texts if "ChainProperty" in t and "ScanEnable" in t), "")
        cell_text = next((t for t in texts if "InstanceName" in t and "CellNo" in t and "RefName" in t), "")
        chains = [line.split() for line in chain_text.splitlines() if re.match(r"^I\s+\S+\s+\d+\s", line)]
        by_id = {row[1]: row for row in chains}
        cells, current = [], None
        for line in cell_text.splitlines():
            match = re.match(r"^\s*(?:I\s+(\S+)\s+)?(\d+)\s+(\S+)\s+(\S+)\s+\(([^)]+)\)", line)
            if match:
                current = match[1] or current
                cells.append((current, int(match[2]), match[3], match[4], match[5]))
        # Public task-book oracle: hierarchy membership is separate from merely
        # getting the right number of chains in each named partition.
        groups = {
            "rx_fifo_partition": (2, "se_rx_fifo", {"u9", "u10", "u11"}),
            "tx_fifo_partition": (4, "se_tx_fifo", {"u3", "u4", "u5", "u6", "u7", "u8"}),
            "rx_serdes_partition": (2, "se_serdes", {"u1"}),
            "tx_serdes_partition": (1, "se_serdes", {"u0"}),
            "reg_partition": (1, "se_ctrl", {"u13"}),
            "wb_partition": (1, "se_ctrl", {"u12"}),
            "ctrl_partition": (2, "se_ctrl", {"u2", *[f"u{i}" for i in range(14, 27)]}),
        }
        checks["ac97_partition_counts_enables_lengths"] = bool(chains) and all(
            len([c for c in chains if c[7] == name]) == count and
            all(c[5] == se and 0 < int(c[2]) <= 300 for c in chains if c[7] == name)
            for name, (count, se, _) in groups.items())
        checks["ac97_only_expected_partitions"] = bool(chains) and {c[7] for c in chains} <= {*groups, "Default_Partition"}
        membership = {instance: name for name, (_, _, instances) in groups.items() for instance in instances}
        checks["ac97_cell_hierarchy_partition"] = bool(cells) and all(
            cell[0] in by_id and by_id[cell[0]][7] == (membership.get(cell[2].split('/')[0]) if '/' in cell[2] else "Default_Partition")
            for cell in cells)
        checks["ac97_unique_cells_and_chain_lengths"] = bool(cells) and len(cells) == len({c[2] for c in cells}) and all(
            len([c for c in cells if c[0] == row[1]]) == int(row[2]) for row in chains)
        checks["ac97_cell_indices_contiguous"] = bool(chains) and all(
            [c[1] for c in cells if c[0] == row[1]] == list(range(int(row[2]))) for row in chains)
        checks["ac97_no_clock_or_edge_mixing"] = bool(chains) and all(
            len({(c[3], c[4]) for c in cells if c[0] == row[1]}) == 1 for row in chains)
        checks["ac97_source_clocks_match_cells"] = bool(chains) and all(
            row[6] in {"clk_i", "bit_clk_pad_i"} and all(c[3] == row[6] for c in cells if c[0] == row[1]) for row in chains)
        signals = [line.split() for text in texts if "SignalType" in text for line in text.splitlines()]
        checks["ac97_clock_reset_signals"] = all(any(row[:len(prefix)] == prefix for row in signals) for prefix in (
            ["clk_i", "pre_existing", "clock", "0"], ["bit_clk_pad_i", "pre_existing", "clock", "0"],
            ["rst_i", "pre_existing", "reset", "1"]))
        checks["ac97_required_report_types"] = all(any(re.search(header, text, re.I) for text in texts) for header in (
            r"Obj_?State", r"ByClock", r"ScanConfigurationParameter", r"SignalType", r"ChainProperty"))
        scannable = [int(n) for text in texts for n in re.findall(r"(?m)^\s*\d+\s+Scannable FF Count\s+(\d+)", text)]
        checks["ac97_reported_scannable_coverage"] = len(scannable) == 1 and scannable[0] == len(cells) > 0
        checks["ac97_scan_port_formats"] = bool(chains) and all(
            re.fullmatch(r"scan_data_in_\d+", c[3]) and re.fullmatch(r"scan_data_out_\d+", c[4]) for c in chains)
        totals = [int(n) for t in texts for n in re.findall(r"Total violations:\s*(\d+)", t)]
        failures = [(int(n), rule) for t in texts for n, rule in re.findall(r"There were (\d+) DRC rule '(DFTR[^']+)' fails", t)]
        checks["ac97_only_permitted_drc_residual"] = len(totals) == 1 and totals[0] == sum(n for n, _ in failures) and all(rule == "DFTR10" for _, rule in failures)
        ctl_files = list((final / "deliverables").glob("*.ctl"))
        def_files = list((final / "deliverables").glob("*.def"))
        checks["ac97_ctl_scandef_present"] = len(ctl_files) == len(def_files) == 1 and all(p.stat().st_size for p in ctl_files + def_files)
        if len(ctl_files) == len(def_files) == 1:
            ctl = ctl_files[0].read_text()
            ctl_cells = [cell for block in re.findall(r"ScanCells\s+(.*?);", ctl, re.S) for cell in re.findall(r'"([^"]+)"', block)]
            checks["ac97_ctl_cells_match_report"] = Counter(ctl_cells) == Counter(c[2] for c in cells) and bool(cells)
            count = re.search(r"SCANCHAINS\s+(\d+)\s*;", def_files[0].read_text())
            checks["ac97_scandef_chain_count"] = bool(count) and int(count[1]) == len(chains)
        result.update({"chains": len(chains), "chain_lengths": [int(c[2]) for c in chains],
                       "unique_cells": len({c[2] for c in cells}), "drc_totals": totals,
                       "scope": "Public T1/C2 hierarchy/partition, cell and CTL report consistency; not netlist connectivity or functional equivalence"})
    if openc906 or veer:
        texts = [p.read_text(encoding="utf-8") for p in (final / "reports").glob("*.rpt")]
        chain_text = next((t for t in texts if "ChainProperty" in t and "ScanEnable" in t), "")
        chains = [line.split() for line in chain_text.splitlines() if re.match(r"^I\s+\S+\s+\d+\s", line)]
        cell_text = next((t for t in texts if "InstanceName" in t and "CellNo" in t and "RefName" in t), "")
        by_chain, cells, cell_clocks = Counter(), set(), {}
        chain_id, duplicates = None, False
        for line in cell_text.splitlines():
            match = re.match(r"\s*(?:I\s+(\S+)\s+)?(\d+)\s+(\S+)\s+(\S+)\s+\(([^)]+)\)", line)
            if match:
                chain_id = match[1] or chain_id
                duplicates |= match[3] in cells
                cells.add(match[3])
                by_chain[chain_id] += 1
                cell_clocks.setdefault(chain_id, set()).add(match[4])
        checks["large_unique_cells_and_lengths"] = bool(cells) and not duplicates and dict(by_chain) == {c[1]: int(c[2]) for c in chains}
        checks["large_max_chain_length"] = bool(chains) and all(0 < int(c[2]) <= (500 if openc906 else 1000) for c in chains)
        ctl_files = list((final / "deliverables").glob("*.ctl"))
        ctl = ctl_files[0].read_text(encoding="utf-8") if len(ctl_files) == 1 else ""
        blocks = re.findall(r'ScanChain\s+"([^"]+)"\s*\{([^}]+)\}', ctl)
        ctl_lengths = {name: int(m[1]) for name, body in blocks for m in [re.search(r"ScanLength\s+(\d+)", body)] if m}
        ctl_cells = [cell for body in re.findall(r"ScanCells\s+(.*?);", ctl, re.S) for cell in re.findall(r'"([^"]+)"', body)]
        checks["large_ctl_matches_chain_cells"] = bool(cells) and set(ctl_cells) == cells and len(ctl_cells) == len(cells) and ctl_lengths == dict(by_chain)
        defs = list((final / "deliverables").glob("*.def"))
        scandef = defs[0].read_text(encoding="utf-8") if len(defs) == 1 else ""
        declared = re.search(r"SCANCHAINS\s+(\d+)\s*;", scandef)
        checks["large_scandef_chain_names"] = bool(declared) and int(declared[1]) == len(chains) and set(re.findall(r"(?m)^\s*-\s+SubScanChain_(\S+)", scandef)) == set(by_chain) and "END SCANCHAINS" in scandef
        signal = next((t for t in texts if "SignalType" in t and "ConstantValue" in t), "")
        header = next((line for line in signal.splitlines() if "ConstantValue" in line and "OwnerPartition" in line), "")
        expected_constants = {"pad_yy_scan_mode": "1", "pad_yy_mbist_mode": "0"} if openc906 else {"scan_mode": "1", "mbist_mode": "0"}
        constants = {line.split()[0]: line[header.index("ConstantValue"):header.index("OwnerPartition")].strip()
                     for line in signal.splitlines() if header and len(line.split()) > 2 and line.split()[2] == "constant"}
        checks["large_scan_and_mbist_modes"] = all(constants.get(port) == value for port, value in expected_constants.items())
        if openc906:
            checks["large_no_cross_clock_chains"] = bool(cell_clocks) and all(len(clocks) == 1 for clocks in cell_clocks.values())
            pseudo = next((t for t in texts if "PseudoPIName" in t), "")
            pseudo_outputs = set(re.findall(r"(?m)^\s*(\S+)\(O\)\s*$", pseudo))
            checks["openc906_nine_pseudo_clock_domains"] = len(pseudo_outputs) == 9 and all(p.endswith('/clk_out') for p in pseudo_outputs)
            checks["openc906_chain_source_clocks"] = bool(chains) and all(c[6] in {"pll_core_cpuclk", "sys_apb_clk"} | pseudo_outputs for c in chains)
        else:
            # Frozen public input: 799125 FFs, of which the task-excluded TAP
            # contains 94. Report-to-report consistency alone once accepted a
            # candidate that silently removed 783928 additional RAM FFs.
            checks['veer_expected_ff_coverage'] = len(cells) == 799125 - 94
            checks["veer_jtag_tap_excluded"] = bool(cells) and not any('/i_jtag_tap/' in '/' + cell for cell in cells) and all('jtag_tck' not in c[6] for c in chains)
            checks["veer_scan_port_formats"] = bool(chains) and all(re.fullmatch(r"scan_data_in_\d+", c[3]) and re.fullmatch(r"scan_data_out_\d+", c[4]) for c in chains)
        result.update(chains=len(chains), unique_cells=len(cells), max_chain_length=max((int(c[2]) for c in chains), default=0), scope="Large public-case cell/CTL/SCANDEF and task-mode consistency; not functional equivalence")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--picorv32", action="store_true", help="Check public T2/C1 task requirements")
    modes.add_argument("--cv32e40p", action="store_true", help="Check public T2/C4 reports and CTL")
    modes.add_argument("--ethernet", action="store_true", help="Check public T2/C2 partitions and cell coverage")
    modes.add_argument("--tv80", action="store_true", help="Check public T2/C3 channels and cell coverage")
    modes.add_argument("--ac97", action="store_true", help="Check public T1/C2 hierarchy partitions, chain cells and CTL")
    modes.add_argument("--openc906", action="store_true", help="Check public T1/C5 large-design output consistency")
    modes.add_argument("--veer", action="store_true", help="Check public T2/C5 large-design output consistency")
    args = parser.parse_args()
    result = verify(args.output, args.picorv32, args.cv32e40p, args.ethernet, args.tv80, args.ac97, args.openc906, args.veer)
    (args.output / "flow_acceptance.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0 if all(result["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
