"""Generate and independently audit 32 original local Scan challenge circuits.

Generated inputs, private oracles, fixture qualification and model runs are kept
separate. No oracle is mounted for model runs. This is not an official score/LEC.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from challenge_suite import ROOT, LIB
from verify_flow import verify

SUITE = ROOT / "benchmarks/scan32"


def case(name, cells, domains=1, chains=1, **kw):
    return {**dict(name=name, cells=cells, domains=domains, chains=chains,
                reset=0, depth=0, mode=False, falling=False, hierarchy=False,
                same_clock=False, segments=0, wrapper=False, exclude=0,
                existing=False, repair=False, faults=[]), **kw}


# Frozen task definitions: repairs change original.dofile only, never the design.
CASES = [
    case("C01_micro8", 8),
    case("C02_reset_low", 16, chains=2, reset=1, repair=True, faults=["reset"]),
    case("C03_reset_high", 24, chains=3, reset=2),
    case("C04_falling_edge", 32, chains=2, falling=True, repair=True, faults=["count"]),
    case("C05_three_domains", 60, 3, 2),
    case("C06_cascade_gate", 96, 2, 3, depth=3, repair=True, faults=["gate", "count"]),
    case("C07_existing_scan", 128, 2, 4, existing=True),
    case("C08_segments", 256, chains=4, segments=4),
    case("C09_four_domains", 1024, 4, 4, repair=True, faults=["clock", "count"]),
    case("C10_shared_clock_hierarchy", 2048, 4, 4, hierarchy=True, same_clock=True),
    case("C11_reset_gate", 4096, 2, 8, reset=1, depth=4, repair=True, faults=["reset", "gate"]),
    case("C12_mode_mux", 8192, 4, 8, mode=True),
    case("C13_eight_domains", 16384, 8, 8, repair=True, faults=["clock", "count"]),
    case("C14_clean_control", 32768, 4, 16, mode=True, depth=2, repair=True),
    case("C15_native_wrapper", 49152, chains=48, wrapper=True),
    case("C16_segment_banks", 65536, 4, 32, segments=8, repair=True, faults=["count"]),
    case("C17_large131k", 131072, 2, 64),
    case("C18_large196k", 196608, 3, 64, reset=2, repair=True, faults=["reset"]),
    case("C19_large262k", 262144, 4, 64, depth=3),
    case("C20_large393k", 393216, 6, 64, mode=True, repair=True, faults=["mode"]),
    case("C21_large524k", 524288, 8, 64, reset=1, depth=2),
    case("C22_large655k", 655360, 5, 128, hierarchy=True, repair=True, faults=["count"]),
    case("C23_large917k", 917504, 7, 128, mode=True),
    case("C24_million", 1048576, 8, 128, reset=2, repair=True, faults=["reset", "mode"], mode=True),
    case("C25_deep_neutral_control", 768, 3, 4, depth=8, mode=True, reset=2,
         repair=True, faults=["gate", "reset", "mode", "count"]),
    case("C26_hierarchy_exclusions", 12288, 3, 16, hierarchy=True, same_clock=True,
         exclude=16, reset=1, depth=3),
    case("C27_segment_mode", 24576, 3, 16, segments=8, mode=True, reset=0, depth=3),
    case("C28_wrapper_repair", 4096, chains=8, wrapper=True,
         repair=True, faults=["wrapper"]),
    case("C29_clean_deep", 98304, 6, 32, hierarchy=True, depth=6, reset=2,
         mode=True, repair=True),
    case("C30_falling_multi", 196608, 3, 64, falling=True, reset=1, depth=4,
         mode=True, repair=True, faults=["reset", "mode", "count"]),
    case("C31_hard_compound", 786432, 8, 96, hierarchy=True, same_clock=True,
         reset=2, depth=8, mode=True, exclude=32),
    case("C32_extreme1m2", 1200000, 8, 150, reset=1, depth=8, mode=True,
         repair=True, faults=["clock", "reset", "gate", "mode", "count"]),
]


def groups(c):
    count = c["cells"] // c["domains"]
    assert count * c["domains"] == c["cells"]
    return [dict(index=i, count=count, partition=f"bank{i}_partition",
                 clock=f"pace_{0 if c['same_clock'] else i}", se=f"shift_{i}",
                 chains=c["chains"], max_length=math.ceil(count / c["chains"]),
                 prefix=f"unit{i}/" if c["hierarchy"] else "",
                 module=f"bank{i}" if c["hierarchy"] else "challenge_top")
            for i in range(c["domains"])]


def clock_wire(c, i):
    return f"wire_{i}" if c["depth"] or c["mode"] or c["falling"] else f"pace_{0 if c['same_clock'] else i}"


def write_design(source, c):
    gs = groups(c)
    controls = sorted({g["clock"] for g in gs}) + [g["se"] for g in gs]
    if c["reset"]:
        controls += ["clear_ctl"]
    if c["depth"]:
        controls += ["open_ctl", "permit"]
    if c["mode"]:
        controls += ["route_ctl", "mbist_ctl", "alternate_pace"]
    if c["wrapper"]:
        controls += ["wr_tick", "wr_shift_in", "wr_shift_out", "wr_cap_in", "wr_cap_out"]
    ports = ["input " + p for p in controls] + ["input [31:0] din"]
    ports += [f"output [{min(g['count'],32)-1}:0] dout_{g['index']}" for g in gs]
    with (source / "netlist/design.v").open("w", encoding="utf-8", newline="\n") as f:
        f.write("module challenge_top(" + ", ".join(ports) + ");\n")
        if c["reset"] == 2:
            f.write("wire reset_b;\nsky130_fd_sc_hd__inv_1 reset_inverter (.A(clear_ctl), .Y(reset_b));\n")
        for g in gs:
            i = g["index"]
            if c["depth"] or c["mode"] or c["falling"]:
                f.write(f"wire wire_{i};\nroute_box route_{i} (.src({g['clock']}), .dst(wire_{i})")
                if c["depth"]:
                    f.write(", .bypass(open_ctl), .permit(permit)")
                if c["mode"]:
                    f.write(", .select(route_ctl), .mbist(mbist_ctl), .other(alternate_pace)")
                f.write(");\n")
            if c["hierarchy"]:
                f.write(f"bank{i} unit{i} (.clk({clock_wire(c,i)}), .se({g['se']}), .din(din), .dout(dout_{i})")
                if c["reset"]:
                    f.write(f", .rst({'reset_b' if c['reset']==2 else 'clear_ctl'})")
                f.write(");\n")
            else:
                write_bank(f, c, g, False)
        if c["exclude"]:
            f.write(f"wire [{c['exclude']-1}:0] keep_q;\n")
            for i in range(c["exclude"]):
                f.write(f"sky130_fd_sc_hd__dfxtp_1 keep_{i} (.CLK(pace_0), .D(din[{i%32}]), .Q(keep_q[{i}]));\n")
        f.write("endmodule\n")
        if c["hierarchy"]:
            for g in gs:
                i = g["index"]
                f.write(f"module bank{i}(input clk, input se, input [31:0] din, output [31:0] dout" + (", input rst" if c["reset"] else "") + ");\n")
                write_bank(f, c, g, True)
                f.write("endmodule\n")
    if c["depth"] or c["mode"] or c["falling"]:
        ports = ["input src", "output dst"]
        if c["depth"]:
            ports += ["input bypass", "input permit"]
        if c["mode"]:
            ports += ["input select", "input mbist", "input other"]
        lines = ["module route_box(" + ", ".join(ports) + ");"]
        prior = "src"
        if c["mode"]:
            lines += ["wire chosen, mbist_n, selected;",
                      "sky130_fd_sc_hd__mux2_1 choose (.A0(other), .A1(src), .S(select), .X(chosen));",
                      "sky130_fd_sc_hd__inv_1 invert_mode (.A(mbist), .Y(mbist_n));",
                      "sky130_fd_sc_hd__and2_1 mask (.A(chosen), .B(mbist_n), .X(selected));"]
            prior = "selected"
        for d in range(c["depth"]):
            lines += [f"wire allow_{d}, pulse_{d};",
                      f"sky130_fd_sc_hd__or2_1 bypass_{d} (.A(permit), .B(bypass), .X(allow_{d}));",
                      f"sky130_fd_sc_hd__and2_1 stage_{d} (.A({prior}), .B(allow_{d}), .X(pulse_{d}));"]
            prior = f"pulse_{d}"
        if c["falling"]:
            lines.append(f"sky130_fd_sc_hd__inv_1 invert_edge (.A({prior}), .Y(dst));")
        else:
            lines.append(f"assign dst = {prior};")
        lines.append("endmodule")
        (source / "netlist/control.v").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_bank(f, c, g, hierarchical):
    i, count = g["index"], g["count"]
    q, n = f"q{i}", f"n{i}"
    clk = "clk" if hierarchical else clock_wire(c, i)
    rst = "rst" if hierarchical else "reset_b" if c["reset"] == 2 else "clear_ctl"
    se = "se" if hierarchical else g["se"]
    out = "dout" if hierarchical else f"dout_{i}"
    f.write(f"wire [{count-1}:0] {q}, {n};\nassign {out} = {q}[{min(count,32)-1}:0];\n")
    seglen = 16
    for bit in range(count):
        segment = bit < c["segments"] * seglen
        first = segment and bit % seglen == 0
        ff = "dfrtp" if c["reset"] else "dfxtp"
        if c["existing"] or first:
            ff = "s" + ff
        data = f"{q}[{bit-1}]" if segment and not first else f"{n}[{bit}]"
        if not segment or first:
            f.write(f"sky130_fd_sc_hd__xor2_1 mix{i}_{bit} (.A(din[{(bit*7+i)%32}]), .B({q}[{(bit+13)%count}]), .X({n}[{bit}]));\n")
        f.write(f"sky130_fd_sc_hd__{ff}_1 r{i}_{bit} (.CLK({clk}), .D({data}), .Q({q}[{bit}])")
        if c["reset"]:
            f.write(f", .RESET_B({rst})")
        if c["existing"] or first:
            f.write(f", .SCE({se}), .SCD(din[{bit%32}])")
        f.write(");\n")


def script(c):
    gs = groups(c)
    files = '"$input_dir/netlist/design.v"'
    if c["depth"] or c["mode"] or c["falling"]:
        files += ' "$input_dir/netlist/control.v"'
    ff = "dfrtp" if c["reset"] else "dfxtp"
    lines = ['load_lib "$input_dir/lib/sky130.lib"', f"load_netlist [list {files}]", "present_design challenge_top",
             f"set_scan_cell_mapping sky130_fd_sc_hd__{ff}_1 sky130_fd_sc_hd__s{ff}_1"]
    lines += [f"set_scan_signal -type clock -port {clk} -off_state 0" for clk in sorted({g["clock"] for g in gs})]
    if c["reset"]:
        lines += [f"set_scan_signal -type reset -port clear_ctl -off_state {1 if c['reset']==1 else 0}"]
    if c["mode"]:
        lines += ["set_scan_signal -type constant -port route_ctl -constant_value 1",
                  "set_scan_signal -type constant -port mbist_ctl -constant_value 0"]
    lines += ["set_scan_cfg -mix_clocks false -mix_edges false -internal_clocks none"]
    if c["depth"]:
        lines += ["set_scan_signal -type scan_enable -port open_ctl -off_state 0 -usage clock_gating"]
    if c["existing"] or c["segments"]:
        lines += ["set_scan_cfg -respect_sff_se_connection true"]
    if c["exclude"]:
        lines += ["set_scan_element false {" + " ".join(f"keep_{i}" for i in range(c["exclude"])) + "}"]
    if c["wrapper"]:
        lines += ["set_scan_signal -type wrapper_clock -port wr_tick -off_state 0",
                  "set_wrapper_cfg enable -style dedicated -chain_count 4",
                  "set_wrapper_cfg -style none -port {din}"]
        for kind, port in (("wrp_in_shift_en", "wr_shift_in"), ("wrp_out_shift_en", "wr_shift_out"),
                           ("wrp_in_capture_en", "wr_cap_in"), ("wrp_out_capture_en", "wr_cap_out")):
            lines += [f"set_scan_signal -type {kind} -port {port} -off_state 0"]
    for g in gs:
        if not c["wrapper"]:
            selector = f"-include {{unit{g['index']}}}" if c["hierarchy"] else f"-clocks {{{g['clock']}}}"
            lines += [f"add_scan_partition {g['partition']} {selector}", f"set_current_scan_partition {g['partition']}"]
        lines += [f"set_scan_cfg -chain_count {g['chains']+(4 if c['wrapper'] else 0)} -max_length {g['max_length']}",
                  f"set_scan_signal -type scan_enable -port {g['se']} -off_state 0 -usage scan",
                  "set_current_scan_partition Default_Partition"]
    lines += ['examine_scan_drc -file "$out_rpt_dir/drc.rpt"']
    if c["segments"]:
        lines += ['rpt_shift_register -file "$out_rpt_dir/shift_register.rpt"']
        for g in gs:
            for s in range(c["segments"]):
                a, b = f"r{g['index']}_{s*16}", f"r{g['index']}_{s*16+15}"
                lines += [f"set_scan_segment segment_{g['index']}_{s} -access {{scan_enable {a}/SCE scan_data_in {a}/SCD scan_data_out {b}/Q}} -lockup_exists false"]
    lines += ["examine_scan_chain", "insert_dft_logic"]
    reports = [("rpt_scan_chain", "scan_chain"), ("rpt_scan_chain_cell", "scan_cell"),
               ("rpt_scan_cfg", "scan_cfg"), ("rpt_scan_signal", "scan_signal"),
               ("rpt_scan_partition", "scan_partition"), ("rpt_insertion_info", "insertion_info")]
    if c["segments"]:
        reports += [("rpt_scan_segment", "scan_segment")]
    if c["wrapper"]:
        reports += [("rpt_wrapper_cfg", "wrapper_cfg"), ("rpt_wrapper_implementation", "wrapper_implementation")]
    lines += [f'{cmd} -file "$out_rpt_dir/{name}.rpt"' for cmd, name in reports]
    lines += ['dump_netlist -file "$out_res_dir/post_scan.v"', 'dump_ctl "$out_res_dir/post_scan.ctl"',
              'dump_def -section scan_chain -file "$out_res_dir/post_scan.def"', "exit"]
    return "\n".join(lines) + "\n"


def specification(c):
    gs = groups(c)
    ff = "dfrtp" if c["reset"] else "dfxtp"
    text = ["# 原创扫描插入挑战", "顶层 challenge_top；使用 lib/sky130.lib，加载 netlist/ 下所有 Verilog 文件。",
            f"设计包含 {c['cells']} 个待扫描寄存器。完成全流程插链并保持功能数据路径不变。",
            f"普通寄存器 sky130_fd_sc_hd__{ff}_1 映射为 sky130_fd_sc_hd__s{ff}_1；已存在 SFF 的类型保持不变。",
            "不得修改输入网表，不得忽略或降级 DRC 规则，不得擅自排除寄存器。DRC 检查无违例。",
            "不得跨时钟域，不允许不同时钟边沿混合。internal_clocks 设置为 none。",
            "顶层时钟端口 " + "、".join(f"`{x}`" for x in sorted({g['clock'] for g in gs})) + " 均配置为时钟信号，off_state 为 0。",
            "扫描使能均为高有效（off_state 0）。"]
    if c["reset"]:
        text += ["`clear_ctl` 配置为复位信号，" + ("低有效" if c["reset"] == 1 else "高有效，经反相器到各 FF 的 RESET_B") + "异步复位；扫描期间必须释放复位。"]
    if c["depth"]:
        text += [f"时钟控制 route_box 包含 {c['depth']} 级门控，`open_ctl` 为高有效门控测试旁路（clock_gating usage），所有级扫描时必须打开；它与各分区扫描使能是不同端口。"]
    if c["mode"]:
        text += ["扫描测试时，`route_ctl` 锁定为 1；`mbist_ctl` 锁定为 0。alternate_pace 是功能备选源，不创建它的扫描域。"]
    if c["falling"]:
        text += ["时钟路径末级有反相器，寄存器相对于顶层源使用下降沿；不得改变反相器或改造功能时钟。"]
    if c["existing"] or c["segments"]:
        text += ["保持既有扫描触发器的扫描使能连接不变。"]
    if c["segments"]:
        text += ["所有长移位寄存器均配置为 scan segment。长移位寄存器（长度 ≥ 16）的首单元已经是 SFF。",
                 "识别连续 Q-to-D 的移位段，显式注册后再构建链；后续普通 DFF 保留功能移位连接。链长度按位而不是段对象数计算。"]
    if c["exclude"]:
        text += [f"只有顶层 keep_0 到 keep_{c['exclude']-1} 这 {c['exclude']} 个寄存器不参与扫描，必须保留其原有普通 DFF 类型和功能连接；它们不计入待扫描寄存器数。"]
    if c["hierarchy"]:
        text += ["按层级划分分区：" + "；".join(f"{g['partition']} 仅含 unit{g['index']} 内全部寄存器" for g in gs) + "。同一源时钟不能作为这些层级分区的额外并集选择条件。"]
    if c["wrapper"]:
        text += ["全局启用 dedicated wrapper；wrapper chain 数目要求为 4；" + f"unwrapper chain 数目要求为 {c['chains']}。",
                 "wrapper 时钟为 wr_tick，off_state 0；输入/输出 shift enable 分别为 wr_shift_in / wr_shift_out，capture enable 分别为 wr_cap_in / wr_cap_out，均高有效。",
                 "端口 `din` 不使用 wrapper；端口 `dout_0` 必须使用 dedicated wrapper。所有内部链使用默认分区，扫描使能 shift_0，" + f"内部 scan chain 最大长度不超过 {gs[0]['max_length']}。"]
    else:
        text += ["分区必须严格满足下表，不得生成额外链；长度是实际链上位数的上限。",
                 "| partition | clock | chain_count | max_length | scan_enable |", "|---|---|---|---|---|"]
        text += [f"| {g['partition']} | {g['clock']} | {g['chains']} | {g['max_length']} | {g['se']} |" for g in reversed(gs)]
    text += ["## 输出要求", "插链后网表 `post_scan.v`、CTL 文件 `post_scan.ctl`、SCANDEF 文件 `post_scan.def`。",
             "DRC 报告 `drc.rpt`、扫描链报告 `scan_chain.rpt`、链单元明细 `scan_cell.rpt`、",
             "插链配置报告 `scan_cfg.rpt`、插链信号报告 `scan_signal.rpt`、插链分区报告 `scan_partition.rpt`、统计报告 `insertion_info.rpt`。"]
    if c["segments"]:
        text += ["另导出 `shift_register.rpt`、`scan_segment.rpt`。"]
    if c["wrapper"]:
        text += ["另导出 `wrapper_cfg.rpt`、`wrapper_implementation.rpt`。"]
    return "\n".join(text) + "\n"


def prepare(folder):
    folder.mkdir(parents=True, exist_ok=False)
    inventory = []
    for c in CASES:
        source, oracle = folder / "inputs" / c["name"], folder / "oracles" / c["name"]
        (source / "netlist").mkdir(parents=True)
        (source / "lib").mkdir()
        oracle.mkdir(parents=True)
        shutil.copyfile(LIB, source / "lib/sky130.lib")
        write_design(source, c)
        (source / "task_spec.md").write_text(specification(c), encoding="utf-8")
        budget = 1800 if c["cells"] >= 500000 else 900 if c["cells"] >= 100000 else 480
        (source / "limitations.md").write_text(f"整个 case 的执行总时间不超过 {budget} 秒。\n", encoding="utf-8")
        correct = script(c)
        (oracle / "correct.dofile").write_text(correct, encoding="utf-8")
        original, defects = correct, []
        g = groups(c)[0]
        for fault in c["faults"]:
            if fault == "reset":
                before = f"set_scan_signal -type reset -port clear_ctl -off_state {1 if c['reset']==1 else 0}"
                after = before[:-1] + str(0 if c["reset"] == 1 else 1)
            elif fault == "clock":
                before = f"set_scan_signal -type clock -port {groups(c)[-1]['clock']} -off_state 0"
                after = before.replace(groups(c)[-1]["clock"], "absent_clock")
            elif fault == "gate":
                before = "set_scan_signal -type scan_enable -port open_ctl -off_state 0 -usage clock_gating"
                after = before.replace("off_state 0", "off_state 1")
            elif fault == "mode":
                before = "set_scan_signal -type constant -port route_ctl -constant_value 1"
                after = before[:-1] + "0"
            elif fault == "wrapper":
                before = "set_wrapper_cfg -style none -port {din}"
                after = "set_wrapper_cfg -style dedicated -port {din}"
            elif fault == "scan_usage":
                before = f"set_scan_signal -type scan_enable -port {g['se']} -off_state 0 -usage scan"
                after = before.replace("-usage scan", "-usage all")
            elif fault == "ctl":
                before = 'dump_ctl "$out_res_dir/post_scan.ctl"'
                after = 'dump_ctl -file "$out_res_dir/post_scan.ctl"'
            elif fault == "count":
                before = f"set_scan_cfg -chain_count {g['chains']} -max_length {g['max_length']}"
                after = before.replace(f"-chain_count {g['chains']}", "-chain_count 1")
            else:
                raise ValueError(f"Unknown fixture fault: {fault}")
            assert before in original, (c["name"], fault)
            original = original.replace(before, after, 1)
            defects.append(dict(id=fault, before=before, after=after))
        if c["repair"]:
            # Original task-2 scripts have runner-rewritable legacy path variables.
            body = original.replace("$input_dir", "$script_dir")
            body = "set script_dir /input\nset base_dir /output\nset out_rpt_dir /output/reports\nset out_res_dir /output/deliverables\n" + body
            (source / "original.dofile").write_text(body, encoding="utf-8")
        record = {**c, "groups": groups(c), "defects": defects, "wall_seconds": budget,
                  "netlist_bytes": sum(p.stat().st_size for p in (source / "netlist").glob("*.v"))}
        (oracle / "oracle.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        inventory.append(record)
        print(json.dumps({"prepared": c["name"], "cells": c["cells"], "netlist_bytes": record["netlist_bytes"]}), flush=True)
    (folder / "manifest.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    lines = [f"# {len(inventory)} 例本地挑战集", "", "原创合成门级设计；不是独立工业 IP，也不是官方隐藏分数。",
             "输入 inputs/；隔离的参考与判分信息 oracles/；参考可行性 qualification/；真实 Luna 运行 runs/。",
             "模型只挂载一个 inputs 子目录和其输出目录。参考脚本执行永远不计作模型通过。", "",
             "| 案例 | 待扫描 FF | 域/分区 | 门控深度 | 难点 |", "|---|---:|---:|---:|---|"]
    for c in inventory:
        features = [key for key in ("reset", "mode", "falling", "hierarchy", "same_clock", "segments", "wrapper", "exclude", "existing") if c[key]]
        lines += [f"| {c['name']} | {c['cells']:,} | {c['domains']} | {c['depth']} | {', '.join(features) or '基础/容量'} |"]
    lines += ["", "公开大例对照：openC906 615,026、VeeR 799,031 个扫描单元。这里只比较 FF 数，不将其等同总门数或工业复杂度。",
              "规模与时限独立记录；480/900/1800 秒为本地压力测试预算，不冒充官方时限。"]
    (folder / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def audit(output, c, *, fixture=False):
    decision_path = output / "decision_log.json"
    if not decision_path.exists():
        return {"passed": False, "checks": {"decision_present": False}}
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    result = verify(output)
    checks = result["checks"]
    checks["execution_mode_matches"] = bool(decision.get("fixture_validation", False)) == fixture
    if not checks["accepted_final_run"]:
        return {**result, "passed": False, "failure": decision.get("failure_type", decision.get("summary"))}
    final = output / "final_results"
    report = (final / "reports/scan_chain.rpt").read_text(encoding="utf-8")
    rows = [line.split() for line in report.splitlines() if re.match(r"^[IW]\s+\S+\s+\d+\s", line)]
    internal = [r for r in rows if r[0] == "I"]
    checks["chain_class_counts"] = Counter(r[0] for r in rows) == {"I": c["chains"] * c["domains"], **({"W": 4} if c["wrapper"] else {})}
    expected_groups = {(g["partition"] if not c["wrapper"] else "Default_Partition"): g for g in c["groups"]}
    checks["partition_counts_clocks_enables_lengths"] = all(
        len([r for r in internal if r[7] == name]) == g["chains"] and
        sum(int(r[2]) for r in internal if r[7] == name) == g["count"] and
        all(r[5:7] == [g["se"], g["clock"]] and 0 < int(r[2]) <= g["max_length"] for r in internal if r[7] == name)
        for name, g in expected_groups.items()) and all(r[7] in expected_groups for r in internal)
    ctl = (final / "deliverables/post_scan.ctl").read_text(encoding="utf-8")
    ctl_rows, seen, serial, end_links, physical = [], set(), {}, [], set()
    domains, duplicates = True, False
    row_by_id = {r[1]: r for r in rows}
    for name, body in re.findall(r'ScanChain\s+"([^"]+)"\s*\{([^}]+)\}', ctl):
        length = re.search(r"ScanLength\s+(\d+);", body)
        vals = [re.search(key + r'\s+"([^"]+)";', body) for key in ("ScanIn", "ScanOut", "ScanEnable", "ScanMasterClock")]
        block = re.search(r"ScanCells\s+(.*?);", body, re.S)
        members = re.findall(r'"([^"]+)"', block[1]) if block else []
        if not c["segments"] or any(int(cell.rsplit("_", 1)[-1]) >= c["segments"]*16 for cell in members):
            physical.add(name)
        if not length or not all(vals):
            domains = False
            continue
        ctl_rows.append([name, length[1]] + [m[1] for m in vals])
        domains &= len(members) == int(length[1])
        prior = vals[0][1]
        for cell in members:
            duplicates |= cell in seen
            seen.add(cell)
            if row_by_id.get(name, [""])[0] != "I":
                continue
            match = re.fullmatch(r"(?:unit(\d+)/)?r(\d+)_(\d+)", cell)
            if not match:
                domains = False
                continue
            group, bit = int(match[2]), int(match[3])
            if group >= len(c["groups"]):
                domains = False
                continue
            g = c["groups"][group]
            domains &= cell == f"{g['prefix']}r{group}_{bit}" and bit < g["count"] and vals[-2][1] == g["se"] and vals[-1][1] == g["clock"]
            serial[cell] = prior
            prior = f"{g['prefix']}q{group}[{bit}]"
        if row_by_id.get(name, [""])[0] == "I":
            end_links.append((vals[1][1], prior))
    checks["ctl_report_agreement"] = sorted(ctl_rows) == sorted(r[1:7] for r in rows)
    checks["exact_internal_members_once_and_domains"] = domains and not duplicates and len(serial) == c["cells"]
    cell_counts, cell_rows_ok, current_chain = Counter(), True, None
    with (final / "reports/scan_cell.rpt").open(encoding="utf-8") as f:
        for line in f:
            match = re.match(r"^\s*(?:[IW]\s+(\S+)\s+)?(\d+)\s+(\S+)\s+(\S+)\s+\(([^)]+)\)\s+(\S+)", line)
            if not match:
                continue
            chain, position, cell, clk, edge, kind = match.groups()
            current_chain = chain or current_chain
            cell_rows_ok &= cell in seen and int(position) == cell_counts[current_chain]
            seen.discard(cell)
            cell_counts[current_chain] += 1
            if cell in serial:
                group, bit = map(int, cell.rsplit("/",1)[-1][1:].split("_"))
                g = c["groups"][group]
                middle = bit < c["segments"]*16 and bit%16 != 0
                expected = ("" if middle else "s") + ("dfrtp" if c["reset"] else "dfxtp")
                cell_rows_ok &= clk == g["clock"] and edge == ("-" if c["falling"] else "+") and kind == f"sky130_fd_sc_hd__{expected}_1"
    checks["cell_report_members_edges_types_lengths"] = cell_rows_ok and not seen and dict(cell_counts) == {r[1]: int(r[2]) for r in rows}
    checks["drc_zero"] = re.findall(r"Total violations:\s*(\d+)", (final / "reports/drc.rpt").read_text()) == ["0"]
    signal = (final / "reports/scan_signal.rpt").read_text()
    signal_rows = [line.split() for line in signal.splitlines()]
    expected_signals = [(clk, "clock", "0") for clk in {g["clock"] for g in c["groups"]}]
    expected_signals += [(g["se"], "scan_enable(spec)", "0") for g in c["groups"]]
    if c["reset"]:
        expected_signals += [("clear_ctl", "reset", "1" if c["reset"] == 1 else "0")]
    if c["depth"]:
        expected_signals += [("open_ctl", "scan_enable(spec)", "0")]
    if c["wrapper"]:
        expected_signals += [("wr_tick", "wrapper_clock", "0"), ("wr_shift_in", "wrp_in_shift_en", "0"),
                             ("wr_shift_out", "wrp_out_shift_en", "0"), ("wr_cap_in", "wrp_in_capture_en", "0"),
                             ("wr_cap_out", "wrp_out_capture_en", "0")]
    checks["signal_roles_and_inactive_polarities"] = all(any(len(r)>3 and (r[0],r[2],r[3]) == entry for r in signal_rows) for entry in expected_signals)
    if c["depth"]:
        checks["separate_clock_gating_usage"] = any(len(r)>4 and r[0] == "open_ctl" and "clock_gating" in r[4:] for r in signal_rows)
    if c["mode"]:
        header = next(line for line in signal.splitlines() if "ConstantValue" in line)
        constants = {line.split()[0]: line[header.index("ConstantValue"):header.index("OwnerPartition")].strip()
                     for line in signal.splitlines() if len(line.split())>2 and line.split()[2] == "constant"}
        checks["mode_constants"] = constants.get("route_ctl") == "1" and constants.get("mbist_ctl") == "0"
    scandef = (final / "deliverables/post_scan.def").read_text()
    declared = re.search(r"SCANCHAINS\s+(\d+)\s*;", scandef)
    checks["scandef_chain_agreement"] = bool(declared) and int(declared[1]) == len(physical) and set(re.findall(r"(?m)^\s*-\s+SubScanChain_(\S+)", scandef)) == physical and "END SCANCHAINS" in scandef
    wrapper_paths = {}
    if c["segments"]:
        segment = (final / "reports/scan_segment.rpt").read_text()
        entries = re.findall(r"(?m)^\s*(\S+)\s+user_defined\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)", segment)
        expected = {(f"r{g['index']}_{s*16}/SCD", f"r{g['index']}_{s*16+15}/Q", "16") for g in c["groups"] for s in range(c["segments"])}
        checks["explicit_segments_complete"] = {(si, so, n) for _, n, si, so, _ in entries} == expected and all(chain in row_by_id for *_, chain in entries)
    if c["wrapper"]:
        wr = (final / "reports/wrapper_implementation.rpt").read_text()
        din = [line for line in wr.splitlines() if re.match(r"\s*\d+\s+din\[", line)]
        dout = [line for line in wr.splitlines() if re.match(r"\s*\d+\s+dout_0\[", line)]
        checks["wrapper_port_semantics"] = len(din) == 32 and len(dout) == 32 and all(re.search(r"\bnone\b", x) for x in din) and all("dedicated" in x for x in dout)
        wrapper_paths = {int(re.search(r"dout_0\[(\d+)\]", line)[1]): line.split()[9] for line in dout}
    checks.update(audit_netlist(final / "deliverables/post_scan.v", c, serial, end_links, wrapper_paths))
    issues = decision.get("issue_resolutions", []) if c["repair"] and not fixture else []
    matched, extra = set(), []
    for issue in issues:
        quote = issue["found"]["excerpt"].strip()
        ids = [d["id"] for d in c["defects"] if d["after"] in quote or quote in d["after"]]
        available = [key for key in ids if key not in matched]
        if len(available) == 1:
            matched.add(available[0])
        else:
            extra.append(issue["issue_id"])
    diagnosis = {"tp": len(matched), "fp_or_duplicate": len(extra), "fn": len(c["defects"])-len(matched), "unmatched": extra}
    if c["repair"] and not fixture:
        checks["diagnosis_all_roots_no_false_positives"] = diagnosis["fp_or_duplicate"] == diagnosis["fn"] == 0
    return {**result, "passed": all(checks.values()), "diagnosis": diagnosis,
            "rounds": len(list((output / "runs").glob("R*/execution.json"))),
            "scope": "Exact cell/domain/CTL/DEF, functional FF pins and serial connectivity; wrapper insertion interface checks, not full combinational LEC."}


def instances(path):
    module, body, cell_type, name = None, [], None, None
    with path.open(encoding="utf-8") as f:
        for line in f:
            m = re.match(r"module\s+(\S+)\s*\(", line)
            if m:
                module = m[1]
            m = re.match(r"\s*(\w+)\s+(\S+)\s*\(", line)
            if m and m[1] == "module":
                m = None
            if m:
                cell_type, name, body = m[1], m[2], []
            if cell_type:
                body.append(line)
                if ");" in line:
                    yield module, cell_type, name, dict(re.findall(r"\.(\w+)\s*\(\s*([^()]+?)\s*\)", "".join(body)))
                    cell_type = None


def audit_netlist(path, c, serial, end_links, wrapper_paths):
    aliases, scopes = {}, {"challenge_top": ""}
    def resolve(net):
        trail = []
        while net in aliases and aliases[net] != net:
            trail.append(net)
            net = aliases[net]
        for item in trail:
            aliases[item] = net
        return net
    def join(left, right):
        a, b = resolve(left), resolve(right)
        if a != b:
            aliases[max(a, b)] = min(a, b)
    # Only hierarchy connections and assignment aliases are retained, not millions
    # of pin dictionaries. Generated banks have one instance per module.
    children = {}
    for module, kind, name, pins in instances(path):
        if not kind.startswith("sky130_"):
            children.setdefault(module, []).append((kind, name, pins))
    pending = [("challenge_top", "")]
    while pending:
        parent, prefix = pending.pop()
        for kind, name, pins in children.get(parent, []):
            child = prefix + name + "/"
            scopes[kind] = child
            for pin, net in pins.items():
                join(child + pin, prefix + net)
                if pin in ("din", "dout"):
                    for bit in range(32):
                        join(f"{child}{pin}[{bit}]", f"{prefix}{net}[{bit}]")
            pending.append((kind, child))
    for g in c["groups"]:
        for bit in range(min(g["count"], 32)):
            endpoint = wrapper_paths[bit] + "/cfi" if c["wrapper"] else f"dout_{g['index']}[{bit}]"
            join(f"{g['prefix']}q{g['index']}[{bit}]", endpoint)
    module = None
    with path.open(encoding="utf-8") as f:
        for line in f:
            m = re.match(r"module\s+(\S+)\s*\(", line)
            if m:
                module = m[1]
            m = re.match(r"\s*assign\s+(\S+)\s*=\s*(\S+)\s*;", line)
            if m and module in scopes:
                prefix = scopes[module]
                left, right = m[1], m[2]
                # Tool emits bit assignments for vectors, but handle slices too.
                a, b = re.fullmatch(r"(\w+)\[(\d+):(\d+)\]", left), re.fullmatch(r"(\w+)\[(\d+):(\d+)\]", right)
                if a and b and int(a[2])-int(a[3]) == int(b[2])-int(b[3]):
                    for offset in range(int(a[2])-int(a[3])+1):
                        join(f"{prefix}{a[1]}[{int(a[3])+offset}]", f"{prefix}{b[1]}[{int(b[3])+offset}]")
                else:
                    join(prefix + left, prefix + right)
    intact, linked, logic, excluded, count, gates = True, True, True, 0, 0, 0
    for module, kind, name, pins in instances(path):
        if module not in scopes:
            continue
        prefix = scopes[module]
        m = re.fullmatch(r"r(\d+)_(\d+)", name)
        if m:
            group, bit = int(m[1]), int(m[2])
            if group >= len(c["groups"]):
                intact = False
                continue
            g = c["groups"][group]
            cell = prefix + name
            count += 1
            seg_middle = bit < c["segments"]*16 and bit % 16 != 0
            expected_type = ("" if seg_middle else "s") + ("dfrtp" if c["reset"] else "dfxtp")
            expected_d = f"{prefix}q{group}[{bit-1}]" if seg_middle else f"{prefix}n{group}[{bit}]"
            actual = {pin: resolve(prefix + net) for pin, net in pins.items()}
            intact &= cell in serial and kind == f"sky130_fd_sc_hd__{expected_type}_1"
            intact &= actual.get("D") == resolve(expected_d) and actual.get("Q") == resolve(f"{prefix}q{group}[{bit}]")
            intact &= actual.get("CLK") == resolve(clock_wire(c, group))
            if c["reset"]:
                intact &= actual.get("RESET_B") == resolve("reset_b" if c["reset"] == 2 else "clear_ctl")
            if not seg_middle:
                intact &= actual.get("SCE") == resolve(g["se"])
                linked &= actual.get("SCD") == resolve(serial.get(cell, "<absent>"))
        elif re.fullmatch(r"keep_\d+", name):
            excluded += 1
            bit = int(name.split("_")[1])
            intact &= kind == "sky130_fd_sc_hd__dfxtp_1" and pins.get("CLK") == "pace_0" and pins.get("D") == f"din[{bit%32}]" and pins.get("Q") == f"keep_q[{bit}]"
        elif re.fullmatch(r"mix\d+_\d+", name):
            group, bit = map(int, name[3:].split("_"))
            g = c["groups"][group]
            gates += 1
            logic &= kind == "sky130_fd_sc_hd__xor2_1" and prefix == g["prefix"]
            expected = {"A": f"din[{(bit*7+group)%32}]", "B": f"{prefix}q{group}[{(bit+13)%g['count']}]", "X": f"{prefix}n{group}[{bit}]"}
            logic &= all(resolve(prefix + pins.get(pin, "<absent>")) == resolve(net) for pin, net in expected.items())
    return {"netlist_original_ff_count_and_exclusions": count == c["cells"] and excluded == c["exclude"],
            "netlist_functional_ff_pins_types_enables": intact,
            "netlist_functional_xor_network": logic and gates == c["cells"]-c["domains"]*c["segments"]*15,
            "netlist_wrapper_functional_endpoints": all(resolve(f"dout_0[{bit}]") == resolve(inst + "/cfo") for bit, inst in wrapper_paths.items()),
            "netlist_serial_connections": linked and all(resolve(a) == resolve(b) for a, b in end_links)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["prepare", "qualify", "run", "score", "report"])
    p.add_argument("--folder", type=Path, default=SUITE)
    p.add_argument("--batch", default="baseline")
    p.add_argument("--cases", nargs="+")
    p.add_argument("--image", help="Frozen image; omitted means current workspace source")
    p.add_argument("--batches", nargs="+", help="Report batches in explicit priority order; later records supersede earlier ones, even on failure")
    args = p.parse_args()
    if args.action == "prepare":
        prepare(args.folder)
        return
    if args.action == "report":
        if not args.batches:
            p.error("report requires --batches in explicit priority order")
        latest, attempts = {}, []
        for batch_name in args.batches:
            if Path(batch_name).name != batch_name or batch_name in (".", ".."):
                p.error("invalid batch directory")
            summary = json.loads((args.folder / "runs" / batch_name / "score.json").read_text(encoding="utf-8"))
            if summary["fixture_only"]:
                p.error("reference qualification is not a model score")
            for row in summary["records"]:
                record = {**row, "batch": batch_name, "image": summary["image"]}
                latest[row["case"]] = record
                attempts.append(record)
        count = sum(r["passed"] for r in latest.values())
        lines = ["# 本地挑战集实测成绩", "", f"指定批次优先级选定的结果通过：{count}/{len(CASES)}；已有结果：{len(latest)}/{len(CASES)}。",
                 "后列批次覆盖前列同名案例（失败也覆盖），不是自动挑最好分数。参考脚本执行不参与统计。",
                 "这是分批、跨版本的开发与修复记录，不是同一冻结版本首次全量通过。", "",
                 "| 案例 | FF | 结果 | 秒 | 轮次 | TP/FP/FN | 批次 / 镜像 |", "|---|---:|---|---:|---:|---|---|"]
        for c in CASES:
            r = latest.get(c["name"], {})
            d = r.get("diagnosis", {})
            diagnosis = "/".join(str(d.get(k,"—")) for k in ("tp", "fp_or_duplicate", "fn"))
            label = "通过" if r.get("passed") else "未通过" if r else "未运行"
            link = f"[结果](runs/{r['batch']}/{c['name']}/decision_log.json)" if r else ""
            lines += [f"| {c['name']} | {c['cells']:,} | {label} {link} | {r.get('seconds','—')} | {r.get('rounds','—')} | {diagnosis} | {r.get('batch','—')} / {r.get('image','—')} |"]
        lines += ["", "这是本地原创合成测试成绩，不是官方评分、独立工业 IP 测试或完整形式等价证明。",
                  "输入和标准答案隔离；结果同时核查实际网表、CTL、报告与诊断。案例详情见 README.md。"]
        (args.folder / "scoreboard.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (args.folder / "results.json").write_text(json.dumps({"passed": count, "total": len(CASES), "batches": args.batches,
            "latest": latest, "attempts": attempts}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": count, "scored": len(latest), "total": len(CASES)}))
        return
    if Path(args.batch).name != args.batch or args.batch in (".", ".."):
        p.error("batch must be a directory name")
    names = args.cases or [c["name"] for c in CASES]
    if not set(names) <= {c["name"] for c in CASES}:
        p.error("unknown case")
    fixture = args.action == "qualify"
    batch = args.folder / ("qualification" if fixture else "runs") / args.batch
    old_records = {}
    if args.action == "score" and (batch / "score.json").exists():
        old = json.loads((batch / "score.json").read_text(encoding="utf-8"))
        args.image = old["image"]
        old_records = {row["case"]: row for row in old["records"]}
    if args.action != "score":
        batch.mkdir(parents=True, exist_ok=True)
        if any((batch / n).exists() for n in names):
            p.error("case output already exists; select a new batch")
    records = []
    for name in names:
        oracle = args.folder / "oracles" / name
        c = json.loads((oracle / "oracle.json").read_text(encoding="utf-8"))
        output = batch / name
        elapsed = old_records.get(name, {}).get("seconds")
        if args.action != "score":
            command = [sys.executable, "-X", "utf8", str(ROOT / "dev/run_local.py"), "--input-dir", str(args.folder / "inputs" / name),
                       "--output-dir", str(output)]
            if args.image:
                command += ["--image", args.image]
            if fixture:
                command += ["--tool-dofile", str(oracle / "correct.dofile")]
            started = time.monotonic()
            print(json.dumps({"starting": name, "cells": c["cells"], "fixture_only": fixture}), flush=True)
            completed = subprocess.run(command, cwd=ROOT)
            elapsed = round(time.monotonic() - started, 2)
            print(json.dumps({"finished": name, "exit": completed.returncode, "seconds": elapsed}), flush=True)
        result = audit(output, c, fixture=fixture)
        records.append({"case": name, "cells": c["cells"], "seconds": elapsed, **result})
        (batch / "score.json").write_text(json.dumps({"image": args.image, "fixture_only": fixture, "requested": names,
            "complete": len(records) == len(names), "passed": sum(x["passed"] for x in records), "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"case": name, "passed": result["passed"], "failed": [k for k,v in result["checks"].items() if not v]}), flush=True)


if __name__ == "__main__":
    main()
