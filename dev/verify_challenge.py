"""Development-only oracles for the original dual-clock and wording challenges.

Expected answers never enter /input or the submission image. Report probes are
synthetic, not Scan executions. End-to-end auditing reuses provenance checks.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

from verify_flow import verify

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "submission"))
from acceptance import _task_checks, _task_requirements


def wording_probes():
    # Each pair expresses the same mandatory constraint in different Chinese.
    # The observation violates that constraint in both cases.
    cases = [
        ("chain_count", "构建扫描链，共 3 条链。", "扫描链总数必须等于 3。", "chain_count", 3),
        ("max_length", "最大链长 4。", "每条扫描链至多包含 4 个单元。", "max_length", 4),
        ("drc_zero", "DRC 检查无违例。", "DRC 违例数必须为 0。", "drc_zero", True),
        ("wrapper", "端口 `payload` 必须使用自定义 dedicated wrapper cell。",
         "端口 `payload` 应采用自定义专用 Wrapper 单元。", "wrapper_ports",
         [{"port": "payload", "style": "dedicated", "cell_type": "user_defined"}]),
    ]
    # One length-eight chain and a real violation cannot satisfy these demands.
    reports = {"chain.rpt": "ChainProperty ScanEnable\nI 1 8 si so se clk Default_Partition tool_created",
               "drc.rpt": "Total violations: 1\nThere were 1 DRC rule 'DFTR11' fails"}
    results = []
    for name, canonical, paraphrase, field, expected in cases:
        for variant, text in (("canonical", canonical), ("paraphrase", paraphrase)):
            req = _task_requirements(text)
            checks, unsupported, _ = _task_checks(Path(__file__).parent, reports, req)
            results.append({"case": name, "variant": variant, "spec": text,
                            "constraint_recognized": req[field] == expected,
                            "bad_observation_rejected": any(v is False for v in checks.values()),
                            "unsupported": unsupported})
    return {"scope": "Synthetic acceptance probes only; no model or Scan run", "results": results}


def audit_dual_clock(output, round_id=None):
    result = ({"checks": {"execution_completed": json.loads(
        (output / "runs" / round_id / "execution.json").read_text())["exit_status"] == "completed"}}
        if round_id else verify(output))
    checks = result["checks"]
    if not round_id and not checks["accepted_final_run"]:
        return result
    final = output / "runs" / round_id if round_id else output / "final_results"
    chains = [line.split() for line in (final / "reports/scan_chain.rpt").read_text().splitlines()
              if re.match(r"^I\s+\S+\s+\d+\s", line)]
    expected_groups = Counter({("tick_a", "shift_a", "ingress", 4): 2,
                               ("tick_b", "shift_b", "egress", 4): 1})
    checks["exact_clock_enable_partition_lengths"] = Counter(
        (row[6], row[5], row[7], int(row[2])) for row in chains) == expected_groups
    ctl = (final / "deliverables/post_scan.ctl").read_text()
    blocks = re.findall(r'ScanChain\s+"([^"]+)"\s*\{([^}]+)\}', ctl)
    cells, ctl_rows = [], []
    domain_membership = True
    for name, body in blocks:
        members = re.search(r"ScanCells\s+(.*?);", body, re.S)
        members = re.findall(r'"([^"]+)"', members[1]) if members else []
        length = re.search(r"ScanLength\s+(\d+);", body)
        values = [re.search(key + r'\s+"([^"]+)";', body)
                  for key in ("ScanIn", "ScanOut", "ScanEnable", "ScanMasterClock")]
        if length and all(values) and len(members) == int(length[1]):
            ctl_rows.append([name, length[1]] + [m[1] for m in values])
            prefix = {"tick_a": "a", "tick_b": "b"}.get(values[-1][1])
            domain_membership &= prefix is not None and all(c.startswith(prefix) for c in members)
        else:
            domain_membership = False
        cells.extend(members)
    expected_cells = {f"a{i}" for i in range(8)} | {f"b{i}" for i in range(4)}
    checks["all_twelve_input_cells_once"] = len(cells) == 12 and set(cells) == expected_cells
    checks["ctl_report_agree"] = len(blocks) == len(ctl_rows) == 3 and sorted(ctl_rows) == sorted(row[1:7] for row in chains)
    checks["cells_in_original_clock_domains"] = domain_membership
    checks["drc_zero"] = re.findall(r"Total violations:\s*(\d+)",
                                    (final / "reports/drc.rpt").read_text()) == ["0"]
    result["scope"] = ("Unpublished round; no final-delivery acceptance" if round_id else "Final delivery with provenance") + "; CTL cell identity and report consistency, not equivalence"
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, help="Audit a real dual-clock run; omit for wording probes")
    parser.add_argument("--round", choices=[f"R{i}" for i in range(1, 20)], help="Audit an unpublished round, without claiming delivery passed")
    args = parser.parse_args()
    if args.round and not args.output:
        parser.error("--round requires --output")
    result = audit_dual_clock(args.output, args.round) if args.output else wording_probes()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.output:
        raise SystemExit(0 if all(result["checks"].values()) else 1)
