"""Deterministic round checks; no model calls, case IDs or reference solutions."""

import json
import re
from pathlib import Path

from scan_runner import declared_artifacts


STAGES = ("examine_scan_drc", "examine_scan_chain", "insert_dft_logic")


def _task_requirements(task_spec):
    """Extract only unambiguous, report-checkable requirements from the prose."""
    if not task_spec:
        return {}
    text = task_spec.read_text(encoding="utf-8", errors="replace") if hasattr(task_spec, "read_text") else str(task_spec)
    text = text.replace("**", "")
    req = {"outputs": [], "report_headers": [], "output_extensions": [], "signals": [], "formats": [], "partitions": [], "chain_count": None,
           "max_count": None, "max_length": None, "config": {}, "allowed_ignores": [],
           "allowed_residuals": [], "allow_default_partition": False,
           "drc_zero": bool(re.search(r"DRC[^。\n]*无[^。\n]*违例|\b(?:zero|no)\s+(?:DRC\s+)?violations?\b|0\s+violations", text, re.I))}
    # Only concrete deliverables are accepted here; dofile/log/agent metadata are not
    # semantic evidence and remain covered by the runner's declared-artifact checks.
    # Output tables are explicit evidence even when the filename is not fenced.
    output_text = re.split(r"(?m)^#{1,4}\s*(?:\d+\.\s*)?输出(?:要求|文件)\s*$", text)[-1] if re.search(r"输出(?:要求|文件)", text) else ""
    for line in output_text.splitlines():
        cells = [c.strip().strip('`') for c in line.split('|')]
        if len(cells) >= 3 and re.fullmatch(r"[^/\\\s]+\.(?:v|rpt|ctl|def)", cells[1], re.I):
            if cells[1] not in req["outputs"]:
                req["outputs"].append(cells[1])
        for filename in re.findall(r"`([^`/\\\s]+\.(?:v|rpt|ctl|def))`", line):
            if filename not in req["outputs"]:
                req["outputs"].append(filename)
    for wording, header in (("插链后网表内寄存器报告", r"Obj_?State"), ("扫描链报告", r"ChainProperty"),
                            ("插链分区报告", r"ByClock"), ("插链配置报告", r"ScanConfigurationParameter"),
                            ("插链信号报告", r"SignalType")):
        if wording in output_text:
            req["report_headers"].append(header)
    for wording, extension in (("插链后网表", ".v"), ("CTL 文件", ".ctl"), ("SCANDEF 文件", ".def")):
        if wording in output_text:
            req["output_extensions"].append(extension)
    req["formats"] = re.findall(r"`([A-Za-z_]\w*%d)`", text)
    for line in text.splitlines():
        if "忽略" in line and not re.search(r"不得|禁止|不允许", line):
            req["allowed_ignores"].extend(re.findall(r"DFTR-[A-Z]+\d+|DFTR\d+", line))
        for clause in re.findall(r"允许(?:遗留|存在)[^。;\n]*", line):
            req["allowed_residuals"].extend(re.findall(r"DFTR-[A-Z]+\d+|DFTR\d+", clause))
    if re.search(r"启用\s*lockup|插入\s*lockup|插入 lockup", text, re.I):
        req["config"]["add_lockup"] = "True"
    if re.search(r"(?<!不)允许不同时钟(?:边沿|沿)[^。\n]*混合", text):
        req["config"]["mix_edges"] = "True"
    if re.search(r"不允许不同时钟(?:边沿|沿)混合", text):
        req["config"]["mix_edges"] = "False"
    if "不得跨时钟域" in text or re.search(r"不允许混合不同时钟域", text):
        req["config"]["mix_clocks"] = "False"
    if "不使用内部门控时钟（伪主输入声明者除外）作为扫描链时钟" in text:
        req["config"]["internal_clocks"] = "none"
    req["floating_clock_domains"] = "这些门控时钟单元的时钟输入引脚均悬空" in text
    for port, value in re.findall(r"`([^`]+)`\s*锁定为\s*([01])", text):
        req["signals"].append({"port": port, "role": "constant", "constant_value": value})
    for ports in re.findall(r"顶层时钟端口\s*([^\n]+?)均配置为时钟信号", text):
        req["signals"].extend({"port": port, "role": "clock"} for port in re.findall(r"`([^`]+)`", ports))
    # Role recognition comes from explicit wording, never a port-name suffix.
    for pattern, role in (
        (r"将扫描时钟配置为\s*`([^`]+)`", "clock"),
        (r"(?:顶层时钟(?:端口)?|单一主时钟端口)\s*`([^`]+)`", "clock"),
        (r"`([^`]+)`\s*配置为复位信号", "reset"),
        (r"复位\s*`([^`]+)`\s*配置为异步复位", "reset"),
        (r"(?:扫描使能[^。\n]*?新建端口|默认的扫描分区[^。\n]*?scan enable 信号)\s*`([^`]+)`", "scan_enable"),
        (r"`([^`]+)`\s*控制 SFF", "scan_enable"),
        (r"\|\s*`([^`]+)`\s*\|\s*(?:扫描使能端口|时钟门控旁路端口)", "scan_enable"),
    ):
        for port in re.findall(pattern, text):
            entry = {"port": port, "role": role}
            if entry not in req["signals"]:
                req["signals"].append(entry)
    for entry in req["signals"]:
        port = re.escape(entry["port"])
        if entry["role"] == "reset":
            match = re.search(r"`" + port + r"`[^。\n]*(低有效|高有效)", text)
            if match:
                entry["off_state"] = "1" if match[1] == "低有效" else "0"
    # Some task books split partition counts and shared scan-enable assignments
    # into small Markdown tables.  Parse only explicit ``*_partition`` rows.
    rich_partition = any("partition" in line and len(line.split('|')) >= 7 for line in text.splitlines())
    partition_length = re.search(r"所有非默认分区的扫描链最大长度统一为\s*(\d+)", text)
    req["allow_default_partition"] = bool(re.search(r"自动归入默认分区", text))
    for line in text.splitlines():
        fields = [x.strip().strip('`') for x in line.split('|')]
        if not rich_partition and len(fields) >= 3 and re.fullmatch(r"[A-Za-z]\w*_partition", fields[1]) and re.fullmatch(r"\d+", fields[2]):
            req["partitions"].append({"name": fields[1], "clock": None, "chain_count": int(fields[2]),
                                       "max_length": int(partition_length[1]) if partition_length else None, "scan_enable": None})
    if req["partitions"]:
        shared = {}
        for line in text.splitlines():
            fields = [x.strip().strip('`') for x in line.split('|')]
            if len(fields) >= 3 and re.fullmatch(r"se_[A-Za-z0-9_]+", fields[1]):
                shared[fields[1]] = [x.strip(' `') for x in fields[2].split(',')]
        for part in req["partitions"]:
            part["scan_enable"] = next((se for se, names in shared.items() if part["name"] in names), None)
    # Markdown partition rows carry all of the constraints in one unambiguous shape.
    for line in text.splitlines():
        if req["partitions"] and not rich_partition:
            continue
        fields = [x.strip().strip('`') for x in line.split('|')]
        if len(fields) < 6:
            continue
        nums = [x for x in fields if re.fullmatch(r"\d+", x)]
        # name | clock(s) | ... | chain_count | max_length | scan_enable
        numeric = [i for i, value in enumerate(fields) if re.fullmatch(r"\d+", value)]
        if len(fields) >= 7 and len(numeric) >= 2:
            first, second = numeric[-2:]
            if second == first + 1 and second + 1 < len(fields):
                req["partitions"].append({"name": fields[1], "clock": fields[2],
                                           "chain_count": int(fields[first]), "max_length": int(fields[second]),
                                           "scan_enable": fields[second + 1]})
    # Single-domain prose/table forms (not partition rows).
    counts = re.findall(r"\bchain_count\b\s*(?:[=:]|\|)?\s*(\d+)", text, re.I)
    if counts and not req["partitions"]:
        req["chain_count"] = int(counts[-1])
    match = re.search(r"共\s*(\d+)\s*条链", text)
    if match and not req["partitions"]:
        req["chain_count"] = int(match[1])
    match = re.search(r"扫描链数(?:不得超过|不超过)\s*(\d+)", text)
    if match:
        req["max_count"] = int(match[1])
    lengths = re.findall(r"\bmax_length\b\s*(?:[=:]|\|)?\s*(\d+)", text, re.I)
    if lengths and not req["partitions"]:
        req["max_length"] = int(lengths[-1])
    match = re.search(r"链最大长度不超过\s*(\d+)", text)
    if match:
        req["max_length"] = int(match[1])
    match = re.search(r"(?:内部\s*scan chain 最大长度不超过|最大链长)\s*(\d+)", text, re.I)
    if match:
        req["max_length"] = int(match[1])
    req["formats"] = list(dict.fromkeys(req["formats"]))
    # Official QA Q19 explicitly clarifies this public design's ambiguous spec.
    # This is a normative task exception, not a general relaxation of DRC.
    if "eth_top" in text and "ethernet_sky130.v" in text and "DFTR10" not in req["allowed_residuals"]:
        req["allowed_residuals"].append("DFTR10")
    req["wrapper"] = {}
    req["wrapper_ports"] = []
    for clause in re.split(r"[。；;\n]", text):
        match = re.search(r"(?:端口|\bports?)\s*((?:`[^`]+`[\s、,和与]*)+)(.*)", clause, re.I)
        if not match:
            continue
        wording = match[2]
        style, cell_type = None, None
        if re.search(r"(?:必须使用|must use).*dedicated\s+wrapper", wording, re.I):
            style = "dedicated"
            if re.search(r"提供|自定义|provided|custom|user.defined", wording, re.I):
                cell_type = "user_defined"
        elif re.search(r"(?:使用|must use)\s+shared\s+wrapper", wording, re.I):
            style = "shared"
        elif re.search(r"不参与扫描路径|不使用\s*wrapper|不插入\s*wrapper|not wrapped|no wrapper", wording, re.I):
            style = "none"
        if style:
            req["wrapper_ports"].extend({"port": port, "style": style, "cell_type": cell_type}
                                        for port in re.findall(r"`([^`]+)`", match[1]))
    threshold = re.search(r"长移位寄存器（长度\s*≥\s*(\d+)）", text)
    req["segment_min_length"] = int(threshold[1]) if threshold and "所有长移位寄存器均配置为 scan segment" in text else None
    if "保持既有扫描触发器的扫描使能连接不变" in text:
        req["config"]["respect_sff_se_connection"] = "True"
    for word, key in (("wrapper", "wrapper_count"), ("unwrapper", "internal_count")):
        match = re.search(r"\b" + word + r" chain 数目要求为\s*(\d+)", text, re.I)
        if match:
            req["wrapper"][key] = int(match[1])
    match = re.search(r"链最大长度（含内部链与 wrapper 链）不超过\s*(\d+)", text, re.I)
    if match:
        req["wrapper"]["all_max_length"] = int(match[1])
    for key in ("reuse_threshold", "depth_threshold"):
        match = re.search(key + r"[）)\s]*为\s*(\d+)", text)
        if match:
            req["wrapper"][key] = int(match[1])
    if re.search(r"Wrapper\s*配置报告", output_text, re.I):
        req["report_headers"].append("WrapperConfigurationParameter")
    if re.search(r"scan segment 报告", output_text, re.I):
        req["report_headers"].append("SegmentProperty")
    return req


def _task_checks(run_dir, reports, requirements, *, full_insertion=True, design_context=(), check_details=None):
    checks, unsupported = {}, []
    if not requirements:
        return checks, unsupported, []
    missing = []
    for output in requirements["outputs"]:
        if not any(p.is_file() and p.stat().st_size and p.name == Path(output).name for group in ("deliverables", "reports") for p in (run_dir / group).glob("*")):
            missing.append(output)
    checks["task_required_outputs_present"] = not missing
    if not full_insertion:
        return checks, ["preprocessing_semantics_require_structural_audit"], missing
    for header in requirements["report_headers"]:
        checks["task_report_" + header] = any(re.search(header, value, re.I) for name, value in reports.items() if name.endswith(".rpt"))
    for extension in requirements["output_extensions"]:
        checks["task_output_type_" + extension[1:]] = any(p.stat().st_size for p in (run_dir / "deliverables").glob("*" + extension) if p.is_file())
    cfg = "\n".join(v for n, v in reports.items() if n.endswith(".rpt") and "ScanConfigurationParameter" in v)
    signal = "\n".join(v for n, v in reports.items() if n.endswith(".rpt") and "SignalType" in v)
    chain = "\n".join(v for n, v in reports.items() if n.endswith(".rpt") and "ChainProperty" in v and "ScanEnable" in v)
    class_rows = re.findall(r"(?m)^\s*([IW])\s+(\S+)\s+(\d+)\s+", chain)
    if requirements.get("floating_clock_domains"):
        expected_pins = {item["path"] + '/' + pin for item in design_context
                         if any(re.search(r"clk|clock", name, re.I) for name in item.get("unconnected_pins", []))
                         for pin in item.get("output_pins", [])}
        requirements["floating_clock_outputs_from_netlist"] = sorted(expected_pins)
        pseudo = "\n".join(v for n, v in reports.items() if n.endswith(".rpt") and "PseudoPIName" in v)
        actual_pins = set(re.findall(r"(?m)^\s*(\S+)\([IO]\)\s*$", pseudo))
        clocks = {line.split()[0] for line in signal.splitlines() if len(line.split()) > 2 and line.split()[2] == "clock"}
        checks["task_pseudo_clocks_match_floating_gate_outputs"] = bool(expected_pins) and actual_pins == expected_pins and expected_pins <= clocks
    wrapper_cfg = "\n".join(v for n, v in reports.items() if n.endswith(".rpt") and "WrapperConfigurationParameter" in v)
    # Check actual implementation, not echoed Tcl or the global default style.
    # Names come exclusively from the task; bus bits must all satisfy the rule.
    implementation_rows = []
    for name, text in (reports.items() if requirements.get("wrapper_ports") else ()):
        if not name.endswith(".rpt"):
            continue
        header = next((line for line in text.splitlines()
                       if all(key in line for key in ("PortName", "WrapperStyle", "UnWrapperReason", "DWCType", "SWCType"))), None)
        if header is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if not re.match(r"^\s*\d+\s", line):
                continue
            port = re.sub(r"\s*\([IO]\)\s*$", "", line[header.index("PortName"):header.index("WrapperStyle")].strip())
            style = line[header.index("WrapperStyle"):header.index("UnWrapperReason")].strip()
            cell_type = line[header.index("DWCType"):header.index("SWCType")].strip()
            implementation_rows.append((port, style, cell_type, name, number, line))
    for rule in requirements.get("wrapper_ports", []):
        rows_for_port = [row for row in implementation_rows
                         if row[0] == rule["port"] or re.fullmatch(re.escape(rule["port"]) + r"\[\d+\]", row[0])]
        wrong_rows = [row for row in rows_for_port if row[1] != rule["style"]
                      or (rule["cell_type"] is not None and row[2] != rule["cell_type"])]
        key = "task_wrapper_port_" + rule["port"]
        checks[key] = bool(rows_for_port) and not wrong_rows
        if check_details is not None and not checks[key]:
            check_details[key] = {"expected": rule, "reported_port_bits": len(rows_for_port),
                                  "mismatching_port_bits": len(wrong_rows),
                                  "evidence": [{"source": row[3], "line": row[4], "excerpt": row[5]}
                                               for row in wrong_rows[:3]]}
    if requirements.get("segment_min_length") is not None:
        shift = "\n".join(v for n, v in reports.items() if n.endswith(".rpt") and "ShiftRegID/CellNo" in v and "ChainName" not in v)
        groups = {}
        for name, group, index in re.findall(r"(?m)^\s*(\S+)\s+(\d+)/(\d+)\s*$", shift):
            groups.setdefault(group, {})[int(index)] = name
        expected = {(cells[min(cells)], cells[max(cells)], len(cells)) for cells in groups.values()
                    if len(cells) >= requirements["segment_min_length"]}
        segment = "\n".join(v for n, v in reports.items() if n.endswith(".rpt") and "SegmentProperty" in v)
        segments = re.findall(r"(?m)^\s*\S+\s+user_defined\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)", segment)
        chain_names = {name for _, name, _ in class_rows}
        assigned = {(si.rsplit('/', 1)[0], so.rsplit('/', 1)[0], int(n))
                    for n, si, so, name in segments if name in chain_names}
        checks["task_long_shift_register_segments_assigned"] = bool(groups) and expected <= assigned
    for key, value in requirements.get("wrapper", {}).items():
        if key in ("wrapper_count", "internal_count"):
            checks["task_" + key] = sum(c == ("W" if key == "wrapper_count" else "I") for c, _, _ in class_rows) == value
        elif key == "all_max_length":
            checks["task_all_chain_max_length"] = bool(class_rows) and all(int(n) <= value for _, _, n in class_rows)
        else:
            checks["task_wrapper_" + key] = bool(re.search(r"(?m)^\s*" + key + r"\s+" + str(value) + r"\s*$", wrapper_cfg))
    rows = []
    for line in chain.splitlines():
        m = re.match(r"\s*I\s+\d+\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+(\S+))?", line)
        if m:
            rows.append({"length": int(m[1]), "si": m[2], "so": m[3], "enable": m[4], "clock": m[5], "partition": m[6] or ""})
    if len(requirements["formats"]) == 2:
        checks["task_port_formats"] = bool(rows) and all(
            re.fullmatch(re.escape(fmt).replace("%d", r"\d+"), row[key]) is not None
            for row in rows for key, fmt in zip(("si", "so"), requirements["formats"]))
    elif requirements["formats"]:
        unsupported.append("ambiguous_input_output_format_pair")
    if requirements["config"]:
        for key, value in requirements["config"].items():
            values = re.findall(r"(?m)^\s*" + key + r"\s+(\S+)", cfg)
            checks["task_config_" + key] = bool(values) and all(v == value for v in values)
    if requirements["signals"]:
        signal_rows = [line.split() for line in signal.splitlines()]
        for entry in requirements["signals"]:
            port, role = entry["port"], entry["role"]
            named = [r for r in signal_rows if len(r) >= 4 and r[0] == port and r[2].split("(")[0] == role]
            reported = bool(named) and all(r[3] == entry["off_state"] for r in named) if "off_state" in entry else bool(named)
            if role == "constant":
                header = next((line for line in signal.splitlines() if "ConstantValue" in line and "OwnerPartition" in line), "")
                selected = [line for line in signal.splitlines() if line.split() and line.split()[0] == port]
                reported = bool(named and header and selected) and all(
                    line[header.index("ConstantValue"):header.index("OwnerPartition")].strip() == entry["constant_value"]
                    for line in selected)
            if role in ("clock", "scan_enable") and not named:
                reported = any(r["clock" if role == "clock" else "enable"] == port for r in rows)
            checks[f"task_signal_{port}_{role}"] = reported
            if not requirements["partitions"] and role == "scan_enable":
                checks[f"task_chain_enable_{port}"] = bool(rows) and all(r["enable"] == port for r in rows)
            if not requirements["partitions"] and role == "clock":
                # Associated internal pin clocks are possible; only enforce a
                # sole source-clock row when the spec explicitly forbids crossing.
                if sum(s["role"] == "clock" for s in requirements["signals"]) == 1 and (requirements["config"].get("mix_clocks") == "False" or requirements["chain_count"] is not None):
                    checks[f"task_chain_clock_{port}"] = bool(rows) and all(r["clock"] == port for r in rows)
    if requirements["partitions"]:
        results = []
        for part in requirements["partitions"]:
            selected = [r for r in rows if r["partition"] == part["name"]]
            results.append(bool(selected) and len(selected) == part["chain_count"]
                           and (part["max_length"] is None or max(r["length"] for r in selected) <= part["max_length"])
                           and all((part["clock"] is None or part["clock"] == r["clock"])
                                   and (part["scan_enable"] is None or part["scan_enable"] == r["enable"])
                                   for r in selected))
        checks["task_partition_chains"] = all(results)
        expected = {p["name"] for p in requirements["partitions"]}
        checks["task_no_extra_partition_chains"] = (
            len([r for r in rows if r["partition"] in expected]) == sum(p["chain_count"] for p in requirements["partitions"])
            and all(r["partition"] in expected or (requirements["allow_default_partition"] and r["partition"] == "Default_Partition") for r in rows))
    elif requirements["chain_count"] is not None:
        checks["task_chain_count"] = bool(rows) and len(rows) == requirements["chain_count"]
    if requirements["max_length"] is not None:
        checks["task_max_chain_length"] = bool(rows) and max(r["length"] for r in rows) <= requirements["max_length"]
    if requirements["max_count"] is not None:
        checks["task_max_chain_count"] = bool(rows) and len(rows) <= requirements["max_count"]
    if requirements["drc_zero"]:
        totals = [int(n) for n in re.findall(r"Total violations:\s*(\d+)", next((v for n, v in reports.items() if n.lower().endswith("drc.rpt")), ""))]
        checks["task_drc_zero"] = bool(totals) and all(n == 0 for n in totals)
    elif requirements["allowed_residuals"]:
        drc = "\n".join(v for n, v in reports.items() if n.lower().endswith("drc.rpt"))
        totals = [int(n) for n in re.findall(r"Total violations:\s*(\d+)", drc)]
        failures = [(int(n), rule) for n, rule in re.findall(r"There were (\d+) DRC rule '(DFTR[^']+)' fails", drc)]
        checks["task_drc_allowed_residuals"] = (len(totals) == 1 and totals[0] == sum(n for n, _ in failures)
                                               and all(rule in requirements["allowed_residuals"] for _, rule in failures))
    else:
        unsupported.append("nonzero_DRC_requires_task_specific_model_review")
    if not cfg:
        unsupported.append("scan_cfg_report")
    if not signal and not chain:
        unsupported.append("scan_signal_report")
    if not chain:
        unsupported.append("scan_chain_report")
    return checks, unsupported, missing


def inspect_run(run_dir, script, execution, *, full_insertion, task_spec=None, design_context=()):
    """Check actual artifacts and report invariants independently of model approval.

    This is not a complete task specification validator or an equivalence proof.
    Preprocessing must not inherit full-insertion DRC/chain requirements.
    """
    artifacts, missing = {}, []
    for name in declared_artifacts(script):
        path = (run_dir / name).resolve()
        if not path.is_relative_to(run_dir.resolve()):
            raise ValueError("Output artifact is outside its run")
        if not path.is_file() or not path.stat().st_size:
            missing.append(name)
        elif path.suffix == ".rpt":
            artifacts[name] = path.read_text(encoding="utf-8", errors="replace")
    log_name = f"{run_dir.name}.log"
    artifacts[log_name] = (run_dir / log_name).read_text(encoding="utf-8", errors="replace")
    checks = {"execution_completed": execution["exit_status"] == "completed", "declared_outputs_present": not missing}
    checks["no_unmatched_object_selectors"] = not bool(re.search(r"\[\s*COM-2003\]\s+Nothing matched", artifacts[log_name]))
    observed, preprocessing = [], []
    for index, line in enumerate(artifacts[log_name].splitlines(), 1):
        match = re.search(r"\[\s*CMD-0034\]\s+@\d+:\s*(examine_scan_drc|examine_scan_chain|insert_dft_logic)(?:\s|$)", line)
        if match:
            if match[1] == "insert_dft_logic" and re.search(r"-(?:connect_icg_only|replace_only|replace_unscan)\b", line):
                preprocessing.append({"command": line[match.start():], "line": index})
            else:
                observed.append({"command": match[1], "line": index})
    workflow = {"source": log_name, "observed": observed,
                "preprocessing": preprocessing,
                "passed": [item["command"] for item in observed] == list(STAGES) and checks["execution_completed"]
                          and all(item["line"] < observed[0]["line"] for item in preprocessing)}
    task_requirements = _task_requirements(task_spec)
    if full_insertion:
        totals = [int(n) for name, text in artifacts.items() if name.endswith(".rpt")
                  for n in re.findall(r"Total violations:\s*(\d+)", text)]
        checks.update(workflow_order=workflow["passed"], drc_report_present=bool(totals))
        chain_reports = [text for name, text in artifacts.items() if name.endswith(".rpt") and "ChainProperty" in text]
        checks["scan_chain_rows_present"] = any(re.search(r"(?m)^\s*[IW]\s+\d+\s+[1-9]\d*\s+\S+", text) for text in chain_reports)
        if not task_requirements or task_requirements["drc_zero"]:
            checks["reported_drc_zero"] = bool(totals) and all(n == 0 for n in totals)
        if task_requirements:
            overrides = re.findall(r"CMD-0034\]\s+@\d+:\s*set_scan_drc_rule_handling\s+\{([^}]+)\}\s+(\w+)", artifacts[log_name])
            checks["only_task_permitted_drc_downgrades"] = all(
                level.lower() not in ("ignore", "warning") or set(ids.split()) <= set(task_requirements["allowed_ignores"])
                for ids, level in overrides)
        # The currently supported single-bit insertion reports expose both
        # scannable cells and chained cells. Check their consistency when present;
        # this does not independently prove that the scannable classification is correct.
        for name, text in artifacts.items():
            if not name.endswith(".rpt") or "Scannable FF Count" not in text:
                continue
            wrapper_bits = re.search(r"(?m)^\s*\d+\s+Wrapper Chain Cell Bit Count\s+(\d+)", text)
            if wrapper_bits and int(wrapper_bits[1]) > 0:
                # Wrapper insertion adds dedicated FFs; CTL segments are counted
                # as objects in Cell Count but expanded in Bit Count/chain lengths.
                # Compare each chain class to its own actual bit total, not to
                # the pre-existing scannable FF count.
                internal_bits = re.search(r"(?m)^\s*\d+\s+Scan Chain Cell Bit Count\s+(\d+)", text)
                tables = [re.findall(r"(?m)^\s*([IW])\s+\S+\s+(\d+)\s+", report)
                          for report_name, report in artifacts.items() if report_name.endswith(".rpt") and "ChainProperty" in report]
                checks["reported_chain_bit_totals"] = bool(internal_bits and tables) and all(
                    sum(int(n) for c, n in rows if c == "I") == int(internal_bits[1]) and
                    sum(int(n) for c, n in rows if c == "W") == int(wrapper_bits[1]) for rows in tables)
                continue
            scannable = re.search(r"(?m)^\s*\d+\s+Scannable FF Count\s+(\d+)", text)
            chained = re.search(r"(?m)^\s*\d+\s+Scan Chain Cell Count\s+(\d+)", text)
            tables = [re.findall(r"(?m)^I\s+\S+\s+(\d+)\s+\S+\s+\S+\s+\S+\s+\S+", report)
                      for report_name, report in artifacts.items() if report_name.endswith(".rpt") and "ChainProperty" in report]
            coverage = bool(scannable and chained and tables) and int(scannable[1]) == int(chained[1]) > 0 and all(
                lengths and sum(map(int, lengths)) == int(chained[1]) for lengths in tables)
            checks["reported_chain_coverage"] = checks.get("reported_chain_coverage", True) and bool(coverage)
    check_details = {}
    task_checks, unsupported, task_missing = _task_checks(run_dir, artifacts, task_requirements, full_insertion=full_insertion, design_context=design_context, check_details=check_details)
    checks.update(task_checks)
    acceptance = {"accepted": all(value is True for value in checks.values()), "checks": checks, "missing_outputs": missing + task_missing,
                  "check_details": check_details,
                  "scope": "Execution, declared artifacts" + (", workflow and reported DRC" if full_insertion else "; semantic acceptance not established")}
    if task_requirements:
        acceptance["task_spec"] = {"requirements": task_requirements, "unsupported": unsupported,
                                    "missing_outputs": task_missing}
    (run_dir / "acceptance.json").write_text(json.dumps(acceptance, indent=2), encoding="utf-8")
    return acceptance, artifacts, workflow
