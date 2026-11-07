"""Focused public Wrapper/CTL/Segment acceptance; not a functional proof."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re

from verify_flow import verify


def scandef_members_match(scandef, expected):
    """Compare physical scan bits across legitimate lockup-split DEF records."""
    actual, seen = {}, set()
    for name, body in re.findall(r"(?ms)^\s*-\s+SubScanChain_(\S+)\s+(.*?);", scandef):
        chain = name if name in expected else re.sub(r"_SUB\d+$", "", name)
        if chain not in expected or name in seen:
            return False
        seen.add(name)
        members = actual.setdefault(chain, Counter())
        for cell, bits in re.findall(r"(?m)^\s+(\S+)\s+\(\s*IN\s+[^\n]*?\(\s*BITS\s+(\d+)\s*\)", body):
            # DEF escapes bus brackets; zero-bit muxes are physical routing
            # objects, not the FFs listed as ScanCells in CTL.
            if int(bits):
                members[re.sub(r"\\(.)", r"\1", cell)] += int(bits)
    return actual == expected


def audit(root, case):
    result = verify(root)
    checks = result["checks"]
    if not checks["accepted_final_run"]:
        return result
    final = root / "final_results"
    reports = [p.read_text(encoding="utf-8") for p in (final / "reports").glob("*.rpt")]
    chain = next((t for t in reports if "ChainProperty" in t), "")
    rows = re.findall(r"(?m)^\s*([IW])\s+(\S+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)", chain)
    counts = Counter(c for c, *_ in rows)
    segment = next((t for t in reports if "SegmentProperty" in t), "")
    segments = re.findall(r"(?m)^\s*(\S+)\s+(inferred_from_ctl|user_defined)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)", segment)
    wrapper = next((t for t in reports if "WrapperConfigurationParameter" in t), "")
    cfg = next((t for t in reports if "ScanConfigurationParameter" in t), "")
    ctl_paths = list((final / "deliverables").glob("*.ctl"))
    ctl = ctl_paths[0].read_text(encoding="utf-8") if len(ctl_paths) == 1 else ""
    ctl_blocks = re.findall(r'ScanChain\s+"([^"]+)"\s*\{([^}]+)\}', ctl)
    ctl_lengths = [(name, int(m[1])) for name, body in ctl_blocks for m in [re.search(r"ScanLength\s+(\d+)", body)] if m]
    checks["ctl_chain_names_lengths_match_report"] = bool(rows) and sorted(ctl_lengths) == sorted((name, int(n)) for _, name, n, *_ in rows)
    ctl_cells = [cell for block in re.findall(r"ScanCells\s+(.*?);", ctl, re.S) for cell in re.findall(r'"([^"]+)"', block)]
    checks["ctl_cell_names_unique"] = bool(ctl_cells) and len(ctl_cells) == len(set(ctl_cells))
    defs = list((final / "deliverables").glob("*.def"))
    scandef = defs[0].read_text(encoding="utf-8") if len(defs) == 1 else ""
    declared = re.search(r"SCANCHAINS\s+(\d+)\s*;", scandef)
    def_names = re.findall(r"(?m)^\s*-\s+SubScanChain_(\S+)", scandef)
    checks["scandef_declared_entries"] = bool(declared) and int(declared[1]) == len(def_names) > 0 and "END SCANCHAINS" in scandef
    drc = next((t for t in reports if "Total violations:" in t), "")
    checks["drc_zero"] = bool(re.search(r"Total violations:\s*0\b", drc))
    if case == "ctl":
        checks["requested_chain_counts"] = counts == {"I": 55, "W": 15}
        checks["dedicated_wrapper_configuration"] = bool(re.search(r"(?m)^style\s+dedicated\s*$", wrapper)) and bool(re.search(r"(?m)^max_length\s+100\s*$", wrapper))
        input_ctl = Path(__file__).resolve().parents[1] / "official/extracted/public_cases/task_1/case3/input/ctl/aes_cipher_top.ctl"
        source = input_ctl.read_text(encoding="utf-8")
        original_cells = [cell for block in re.findall(r"ScanCells\s+(.*?);", source, re.S) for cell in re.findall(r'"([^"]+)"', block)]
        original_chains = re.findall(r'ScanChain\s+"([^"]+)"', source)
        expected_segments = {f"U_AES_CIPHER_TOP_{i}/Internal_scan/{name}" for i in range(3) for name in original_chains}
        checks["all_blackbox_segments_registered"] = {name for name, kind, *_ in segments if kind == "inferred_from_ctl"} == expected_segments
        expected_cells = {f"U_AES_CIPHER_TOP_{i}/{name}" for i in range(3) for name in original_cells}
        checks["flat_ctl_includes_all_blackbox_cells"] = bool(expected_cells) and expected_cells <= set(ctl_cells) and len(re.findall(r"\bScanStructures\b", ctl)) == 1
        # CTL-only chains have no top-level physical cells to export to DEF.
        # Compare with chains containing real top-level cells, not total chains.
        physical_chains = {}
        for name, body in ctl_blocks:
            match = re.search(r"ScanCells\s+(.*?);", body, re.S)
            cells = re.findall(r'"([^"]+)"', match[1]) if match else []
            physical = Counter(cell for cell in cells if cell not in expected_cells)
            if physical:
                physical_chains[name] = physical
        checks["scandef_physical_chains"] = scandef_members_match(scandef, physical_chains)
        prefix = "x_cr_tcipif_top/x_cr_clic_top/x_cr_clic_ctrl/"
        scoped = [cell for cell in ctl_cells if cell.startswith(prefix)]
        checks["hierarchy_exclusion_with_mintthresh_exception"] = bool(scoped) and all(cell.startswith(prefix + "mintthresh") for cell in scoped)
        checks["mixed_clocks_enabled"] = bool(re.search(r"(?m)^mix_clocks\s+True\s*$", cfg))
        # The task expressly permits overlength indivisible CTL wrapper segments.
        checks["internal_chain_lengths_at_most_100"] = all(int(n) <= 100 for c, _, n, *_ in rows if c == "I")
    else:
        checks["all_chain_lengths_at_most_100"] = bool(rows) and all(int(n) <= 100 for _, _, n, *_ in rows)
        checks["scan_ports_and_enable"] = bool(counts["I"]) and all(se == "decrypt" and re.fullmatch(r"scan_data_in_\d+", si) and re.fullmatch(r"scan_data_out_\d+", so) for c, _, _, si, so, se in rows if c == "I")
        checks["preserve_existing_enable_configuration"] = bool(re.search(r"(?m)^respect_sff_se_connection\s+True\s*$", cfg))
        checks["wrapper_thresholds"] = all(re.search(r"(?m)^" + key + r"\s+" + str(value) + r"\s*$", wrapper) for key, value in (("reuse_threshold", 64), ("depth_threshold", 16)))
        implementation = next((t for t in reports if "PortName" in t and "WrapperStyle" in t), "")
        key2 = [line for line in implementation.splitlines() if re.search(r"\bkey2(?:\[|\s|\()", line)]
        key1 = [line for line in implementation.splitlines() if re.search(r"\bkey1(?:\[|\s|\()", line)]
        checks["key2_uses_custom_dedicated_cells"] = bool(key2) and all("dedicated" in line and "user_defined" in line for line in key2)
        checks["key1_not_wrapped"] = bool(key1) and all(re.search(r"\bnone\b", line) for line in key1)
        shift = next((t for t in reports if "ShiftRegID/CellNo" in t and "ChainName" not in t), "")
        groups = {}
        for name, group, index in re.findall(r"(?m)^\s*(\S+)\s+(\d+)/(\d+)\s*$", shift):
            groups.setdefault(group, {})[int(index)] = name
        long_groups = [cells for cells in groups.values() if len(cells) >= 10]
        actual = {(si.rsplit('/', 1)[0], so.rsplit('/', 1)[0], int(n)) for _, kind, n, si, so, _ in segments if kind == "user_defined"}
        expected = {(cells[min(cells)], cells[max(cells)], len(cells)) for cells in long_groups}
        checks["long_shift_registers_registered_as_segments"] = bool(expected) and expected <= actual
        checks["segments_assigned_to_chains"] = bool(segments) and all(chain_name in {row[1] for row in rows} for *_, chain_name in segments)
        result["long_shift_registers"] = len(long_groups)
    result.update(chain_counts=dict(counts), max_chain_length=max((int(r[2]) for r in rows), default=0),
                  segment_count=len(segments), ctl_cells=len(ctl_cells),
                  scope="Public wrapper/segment report and CTL consistency, not netlist connectivity or equivalence")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", choices=("ctl", "des"), required=True)
    args = parser.parse_args()
    result = audit(args.output, args.case)
    (args.output / "wrapper_acceptance.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    raise SystemExit(0 if all(result["checks"].values()) else 1)
