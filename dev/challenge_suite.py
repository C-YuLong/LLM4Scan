"""Original bounded Scan benchmark. Oracles stay outside the mounted input.

Generate deterministic small circuits, then use the existing local launcher.
Scores are development measurements, not the organizer's scoring formula.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from verify_flow import verify

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "official/extracted/public_cases/task_1/case4/input/lib/sky130.lib"
# Each tuple: partition, clock, scan enable, FF count, chain count.
CASES = {
    "d_clock_count": ("dev", [("ingress", "tick_a", "shift_a", 8, 2), ("egress", "tick_b", "shift_b", 4, 1)], "plain", ["clock", "count"], True),
    "d_reset": ("dev", [("storage", "pulse", "shift", 10, 2)], "reset", ["reset"], True),
    "d_clean": ("dev", [("left", "beat_l", "test_l", 6, 2), ("right", "beat_r", "test_r", 6, 2)], "plain", [], True),
    "d_generate": ("dev", [("Default_Partition", "pulse", "scan_go", 9, 3)], "gate", [], False),
    "h_three": ("holdout", [("blue", "pace_x", "move_x", 6, 2), ("green", "pace_y", "move_y", 8, 2), ("red", "pace_z", "move_z", 10, 2)], "plain", ["clock", "count"], True),
    "h_reset": ("holdout", [("vault", "sample", "shift_mode", 12, 3)], "reset", ["reset", "count"], True),
    "h_clean": ("holdout", [("Default_Partition", "beat", "test_move", 8, 2)], "gate", [], True),
    "h_generate": ("holdout", [("west", "step_w", "load_w", 15, 3), ("east", "step_e", "load_e", 6, 2)], "plain", [], False),
    "h_prose": ("holdout", [("Default_Partition", "flow_clk", "shift_mode", 12, 3)], "plain", [], False),
}


def prepare(folder, names):
    folder.mkdir(parents=True, exist_ok=False)
    for name in names:
        split, groups, kind, faults, repair = CASES[name]
        source = folder / name / "input"
        (source / "netlist").mkdir(parents=True)
        (source / "lib").mkdir()
        shutil.copyfile(LIB, source / "lib/sky130.lib")
        total = sum(g[3] for g in groups)
        ff = "dfrtp" if kind == "reset" else "dfxtp"
        sff = "sdfrtp" if kind == "reset" else "sdfxtp"
        ports = ["input " + g[1] for g in groups]
        if kind == "reset":
            ports.append("input clear_n")
        if kind == "gate":
            ports.extend(["input permit", "input " + groups[0][2]])
        ports += [f"input [{total-1}:0] data", f"output [{total-1}:0] value"]
        netlist = ["module sample_top(" + ", ".join(ports) + ");"]
        if kind == "gate":
            netlist += ["wire allowed, gated;",
                        f"sky130_fd_sc_hd__or2_1 u_permit (.A(permit), .B({groups[0][2]}), .X(allowed));",
                        f"sky130_fd_sc_hd__and2_1 u_gate (.A({groups[0][1]}), .B(allowed), .X(gated));"]
        cells, index = {}, 0
        for group, clk, se, count, chains in groups:
            for i in range(count):
                cell = f"r{index}"
                netlist.append(f"sky130_fd_sc_hd__{ff}_1 {cell} (.CLK({'gated' if kind == 'gate' else clk}), .D(data[{index}]), .Q(value[{index}])" + (", .RESET_B(clear_n)" if kind == "reset" else "") + ");")
                cells[cell] = {"clock": clk, "enable": se, "partition": group, "bit": index}
                index += 1
        netlist.append("endmodule")
        (source / "netlist/design.v").write_text("\n".join(netlist) + "\n", encoding="utf-8")
        spec = ["# 插链任务", "顶层 sample_top，读取 lib/sky130.lib 和 netlist/design.v。",
                f"将全部 {total} 个 sky130_fd_sc_hd__{ff}_1 替换成 sky130_fd_sc_hd__{sff}_1，完成扫描链插入。",
                "不得改输入网表、排除寄存器或忽略/降级 DRC 规则。不得跨时钟域，不允许不同时钟边沿混合。",
                "DRC 检查无违例。" if split == "dev" else "完成后 DRC 违例数必须为 0。",
                "顶层时钟端口 " + "、".join(f"`{g[1]}`" for g in groups) + " 均配置为时钟信号，off_state 为 0。",
                "扫描使能均为高有效，低电平不使能。"]
        if kind == "reset":
            spec.append("`clear_n` 配置为复位信号，低有效异步复位；扫描期间必须释放复位。")
        if kind == "gate":
            spec.append("扫描使能 " + f"`{groups[0][2]}` 与 permit 经过 OR 后控制时钟 AND 门；扫描时必须打开门控。" +
                        "不增加独立的内部门控时钟域；使用 internal_clocks none。")
        if name == "h_prose":
            spec += ["扫描链总数必须等于 3。", "每条扫描链至多包含 4 个单元。",
                     "所有寄存器使用默认分区，统一由扫描使能端口 `shift_mode` 控制；该端口为高有效。"]
        else:
            spec += ["各分区只包含对应时钟域全部寄存器，严格满足下表，不允许额外扫描链。",
                     "| 分区 | 时钟 | chain_count | max_length | scan_enable |", "|---|---|---|---|---|"]
            spec += [f"| {g} | {c} | {n} | {count//n} | {s} |" for g, c, s, count, n in reversed(groups)]
        spec += ["## 输出要求", "插链后网表 `post_scan.v`、CTL 文件 `post_scan.ctl`。",
                 "DRC 报告 `drc.rpt`；扫描链报告 `scan_chain.rpt`；链上单元明细 `scan_cell.rpt`。",
                 "插链配置报告 `scan_cfg.rpt`、插链信号报告 `scan_signal.rpt`、插链分区报告 `scan_partition.rpt`、统计报告 `insertion_info.rpt`。"]
        (source / "task_spec.md").write_text("\n".join(spec) + "\n", encoding="utf-8")
        (source / "limitations.md").write_text("整个 case 的执行总时间不超过 240 秒。\n", encoding="utf-8")
        script = ["set script_dir /input", "set base_dir /output", "set out_rpt_dir /output/reports", "set out_res_dir /output/deliverables",
                  'load_lib "$script_dir/lib/sky130.lib"', 'load_netlist "$script_dir/netlist/design.v"', "present_design sample_top",
                  f"set_scan_cell_mapping sky130_fd_sc_hd__{ff}_1 sky130_fd_sc_hd__{sff}_1"]
        script += [f"set_scan_signal -type clock -port {g[1]} -off_state 0" for g in groups]
        if kind == "reset":
            script.append("set_scan_signal -type reset -port clear_n -off_state 1")
        script.append("set_scan_cfg -mix_clocks false -mix_edges false -internal_clocks none")
        for group, clk, se, count, chains in groups:
            if group != "Default_Partition":
                script += [f"add_scan_partition {group} -clocks {{{clk}}}", f"set_current_scan_partition {group}"]
            script += [f"set_scan_cfg -chain_count {chains} -max_length {count//chains}",
                       f"set_scan_signal -type scan_enable -port {se}" + (" -off_state 0 -usage scan" if group != "Default_Partition" else ""),
                       "set_current_scan_partition Default_Partition"]
        script += ['examine_scan_drc -file "$out_rpt_dir/drc.rpt"', "examine_scan_chain", "insert_dft_logic"]
        script += [f'{cmd} -file "$out_rpt_dir/{report}.rpt"' for cmd, report in (
            ("rpt_scan_chain", "scan_chain"), ("rpt_scan_chain_cell", "scan_cell"), ("rpt_scan_cfg", "scan_cfg"),
            ("rpt_scan_signal", "scan_signal"), ("rpt_scan_partition", "scan_partition"), ("rpt_insertion_info", "insertion_info"))]
        script += ['dump_netlist -file "$out_res_dir/post_scan.v"', 'dump_ctl "$out_res_dir/post_scan.ctl"', "exit"]
        correct = "\n".join(script) + "\n"
        original, defects = correct, []
        for fault in faults:
            if fault == "clock":
                clk = groups[-1][1]
                before = f"set_scan_signal -type clock -port {clk} -off_state 0"
                after = "set_scan_signal -type clock -port nonexistent_pulse -off_state 0"
                anchors = [after, f"add_scan_partition {groups[-1][0]} -clocks {{{clk}}}"]
            elif fault == "reset":
                before = "set_scan_signal -type reset -port clear_n -off_state 1"
                after = before[:-1] + "0"
                anchors = [after]
            else:
                before = f"set_scan_cfg -chain_count {groups[0][4]} -max_length {groups[0][3]//groups[0][4]}"
                after = before.replace(f"-chain_count {groups[0][4]}", "-chain_count 1")
                anchors = [after]
            original = original.replace(before, after, 1)
            defects.append({"id": fault, "before": before, "after": after, "anchors": anchors})
        if repair:
            (source / "original.dofile").write_text(original, encoding="utf-8")
        oracle = {"split": split, "kind": kind, "groups": groups, "cells": cells, "sff": sff, "repair": repair, "defects": defects,
                  "correct_dofile": correct}
        (folder / name / "oracle.json").write_text(json.dumps(oracle, indent=2), encoding="utf-8")


def audit(output, oracle):
    result = verify(output)
    checks = result["checks"]
    decision = json.loads((output / "decision_log.json").read_text(encoding="utf-8"))
    if checks["accepted_final_run"]:
        final = output / "final_results"
        rows = [line.split() for line in (final / "reports/scan_chain.rpt").read_text().splitlines() if re.match(r"^I\s+\S+\s+\d+\s", line)]
        expected = Counter({(g, c, s, count//n): n for g, c, s, count, n in oracle["groups"]})
        checks["partition_clock_enable_lengths"] = Counter((r[7], r[6], r[5], int(r[2])) for r in rows) == expected
        ctl = (final / "deliverables/post_scan.ctl").read_text()
        members, ctl_rows, domains = [], [], True
        for name, body in re.findall(r'ScanChain\s+"([^"]+)"\s*\{([^}]+)\}', ctl):
            cell_block = re.search(r"ScanCells\s+(.*?);", body, re.S)
            cells = re.findall(r'"([^"]+)"', cell_block[1]) if cell_block else []
            length = re.search(r"ScanLength\s+(\d+);", body)
            values = [re.search(key + r'\s+"([^"]+)";', body) for key in ("ScanIn", "ScanOut", "ScanEnable", "ScanMasterClock")]
            if length and all(values) and len(cells) == int(length[1]):
                ctl_rows.append([name, length[1]] + [m[1] for m in values])
                domains &= all(c in oracle["cells"] and oracle["cells"][c]["clock"] == values[-1][1] and oracle["cells"][c]["enable"] == values[-2][1] for c in cells)
            else:
                domains = False
            members += cells
        checks["exact_cell_set_once"] = len(members) == len(set(members)) == len(oracle["cells"]) and set(members) == set(oracle["cells"])
        checks["ctl_report_agree"] = sorted(ctl_rows) == sorted(r[1:7] for r in rows)
        checks["cell_domains"] = domains
        checks["drc_zero"] = re.findall(r"Total violations:\s*(\d+)", (final / "reports/drc.rpt").read_text()) == ["0"]
        # Check actual functional pins and scan enable in the emitted gate netlist.
        net = (final / "deliverables/post_scan.v").read_text()
        instances = {name: (cell, dict(re.findall(r"\.(\w+)\s*\(\s*([^()]+?)\s*\)", body)))
                     for cell, name, body in re.findall(r"(sky130_fd_sc_hd__\w+)\s+(\w+)\s*\((.*?)\);", net, re.S)}
        aliases = dict(re.findall(r"assign\s+(\S+)\s*=\s*(\S+)\s*;", net))
        def resolve(net_name, links=aliases):
            visited = set()
            while net_name in links and net_name not in visited:
                visited.add(net_name)
                net_name = links[net_name]
            return net_name
        def expected_net(net_name):
            return resolve(resolve(net_name, oracle.get('input_aliases', {})))
        intact = True
        for name, expected_cell in oracle["cells"].items():
            cell, pins = instances.get(name, ("", {}))
            bit = expected_cell["bit"]
            intact &= cell == f"sky130_fd_sc_hd__{oracle['sff']}_1" and resolve(pins.get("D")) == expected_net(f"data[{bit}]") and resolve(pins.get("Q")) == expected_net(f"value[{bit}]") and resolve(pins.get("SCE")) == expected_net(expected_cell["enable"])
            intact &= resolve(pins.get("CLK")) == expected_net("gated" if oracle["kind"] == "gate" else expected_cell["clock"])
            if oracle["kind"] == "reset":
                intact &= resolve(pins.get("RESET_B")) == expected_net(expected_cell.get('reset', "clear_n"))
        checks["netlist_functional_pins_and_scan_enable"] = intact
        # Follow simple assignment aliases to check every serial link against CTL.
        connectivity = True
        for _, body in re.findall(r'ScanChain\s+"([^"]+)"\s*\{([^}]+)\}', ctl):
            block = re.search(r"ScanCells\s+(.*?);", body, re.S)
            seq = re.findall(r'"([^"]+)"', block[1]) if block else []
            si, so = re.search(r'ScanIn\s+"([^"]+)";', body), re.search(r'ScanOut\s+"([^"]+)";', body)
            if not seq or not si or not so:
                connectivity = False
                continue
            previous = si[1]
            for cell_name in seq:
                pins = instances.get(cell_name, ("", {}))[1]
                connectivity &= resolve(pins.get("SCD")) == resolve(previous)
                previous = pins.get("Q")
            connectivity &= resolve(so[1]) == resolve(previous)
        checks["netlist_serial_connectivity"] = connectivity
    findings = decision.get("issue_resolutions", []) if oracle["repair"] else []
    matched, unmatched = set(), []
    for issue in findings:
        quote = issue["found"]["excerpt"].strip()
        candidates = [d["id"] for d in oracle["defects"] if any(a in quote or quote in a for a in d["anchors"])]
        remaining = [key for key in candidates if key not in matched]
        if len(remaining) == 1:
            matched.add(remaining[0])
        else:
            unmatched.append(issue["issue_id"])
    tp, fp, fn = len(matched), len(unmatched), len(oracle["defects"]) - len(matched)
    result.update({"artifact_passed": all(checks.values()), "diagnosis": {"tp": tp, "fp_or_duplicate": fp, "fn": fn,
                   "unmatched_issue_ids": unmatched, "f1": 2*tp/(2*tp+fp+fn) if tp+fp+fn else 1.0},
                   "rounds": len(list((output / "runs").glob("R*/execution.json"))),
                   "scope": "Small generated gate netlists only; exact cell/pin/serial-link checks, not general LEC. Diagnosis anchors require manual review."})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "run", "score"])
    parser.add_argument("--suite", required=True, help="New folder name under dev/results")
    parser.add_argument("--split", choices=["dev", "holdout", "all"], default="dev")
    parser.add_argument("--cases", nargs="+", choices=list(CASES))
    parser.add_argument("--prefix", help="New result prefix for a run/score")
    parser.add_argument("--image")
    args = parser.parse_args()
    for label in (args.suite, args.prefix):
        if label is not None and (Path(label).name != label or label in {".", ".."}):
            parser.error("suite and prefix must be directory names")
    folder = ROOT / "dev/results" / args.suite
    if args.action == "prepare":
        names = args.cases or list(CASES)
        prepare(folder, names)
        print(json.dumps({"cases": len(names), "suite": str(folder)}))
        return
    if not args.prefix:
        parser.error("run/score requires --prefix")
    selected = args.cases or [name for name, case in CASES.items() if args.split == "all" or case[0] == args.split]
    summary_path = ROOT / "dev/results" / f"{args.prefix}_score.json"
    if args.action == "run" and (summary_path.exists() or any((ROOT / "dev/results" / f"{args.prefix}_{name}").exists() for name in selected)):
        raise ValueError("Result prefix already exists")
    records = []
    for name in selected:
        output = ROOT / "dev/results" / f"{args.prefix}_{name}"
        if args.action == "run":
            command = [sys.executable, "-X", "utf8", str(ROOT / "dev/run_local.py"), "--input-dir", str(folder / name / "input"), "--output", output.name]
            if args.image:
                command += ["--image", args.image]
            started = time.monotonic()
            completed = subprocess.run(command, cwd=ROOT)
            print(json.dumps({"case": name, "exit_code": completed.returncode, "elapsed": round(time.monotonic()-started, 2)}), flush=True)
        oracle = json.loads((folder / name / "oracle.json").read_text())
        result = audit(output, oracle)
        records.append({"case": name, **result})
        summary = {"suite": args.suite, "image": args.image, "requested_cases": selected, "finished": len(records) == len(selected), "records": records,
                   "artifact_passed": sum(r["artifact_passed"] for r in records), "cases_scored": len(records)}
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"case": name, "artifacts": result["artifact_passed"], "diagnosis": result["diagnosis"]}), flush=True)


if __name__ == "__main__":
    main()
