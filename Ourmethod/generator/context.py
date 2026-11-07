"""Design summaries supplied to the existing generation prompts."""

import re
import telemetry


@telemetry.tracked("design_analysis", "分析网表与实例层级")
def design_outline(source):
    """Read declarations and hierarchical instances in the supplied gate netlists.

    This small outline is prompt context, not a Verilog semantic parser. Cell
    connectivity and flattened library instances are not sent to the model.
    """
    modules, names, control_bodies = [], set(), {}
    gate_sample_types, gate_sample_kinds = set(), set()
    paths = sorted((source / "netlist").glob("*.v"))
    for path in paths:
        header, module_name, control_name, body = [], None, None, ""
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                match = re.match(r"\s*module\s+(\S+?)(?:\s|\()", line)
                if match:
                    names.add(match[1])
                    module_name, header = match[1], []
                    gate_kind = re.match(r"SNPS_CLOCK_GATE_(HIGH|LOW)", match[1])
                    sample_gate = gate_kind and gate_kind[1] not in gate_sample_kinds
                    if sample_gate:
                        gate_sample_kinds.add(gate_kind[1])
                        gate_sample_types.add(match[1])
                    control_name = match[1] if sample_gate or (not match[1].startswith("SNPS_CLOCK_GATE") and re.search(r"clk|clock|icg|BUFG", match[1], re.I)) else None
                    body = ""
                if control_name is not None:
                    body += line
                    if len(body) > 4096:
                        control_name = None
                    elif re.match(r"\s*endmodule\b", line):
                        control_bodies[control_name] = body
                        control_name = None
                if module_name is not None:
                    header.append(line.strip())
                    if ";" in line:
                        modules.append((module_name, header))
                        module_name = None
    instances = []
    for path in paths:
        module, current_instance = None, None
        with path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                match = re.match(r"\s*module\s+(\S+?)(?:\s|\()", line)
                if match:
                    module = match[1]
                match = re.match(r"\s*(\S+)\s+(\S+?)\s*\(", line)
                if match and (match[1] in names or re.search(r'(?:clk|clock).*latch', match[2], re.I)):
                    current_instance = {"module": module, "type": match[1], "instance": match[2],
                                        "source": str(path.relative_to(source)), "line": number, "unconnected_pins": []}
                    if match[1] not in names or match[1] in gate_sample_types or (not match[1].startswith("SNPS_CLOCK_GATE") and re.search(r"clk|clock|icg|BUFG", match[1], re.I)):
                        current_instance["connections"] = {}
                    instances.append(current_instance)
                if current_instance is not None:
                    current_instance["unconnected_pins"].extend(re.findall(r"\.(\w+)\s*\(\s*\)", line))
                    if "connections" in current_instance:
                        current_instance["connections"].update((pin, net.strip()) for pin, net in re.findall(r"\.(\w+)\s*\(([^()]*)\)", line))
                    if ";" in line:
                        current_instance = None
    children = {name: [] for name in names}
    for item in instances:
        children[item["module"]].append(item)
    roots = names - {item["type"] for item in instances}
    resolved = []
    pending = [(root, "", ()) for root in sorted(roots)]
    while pending:
        module, prefix, ancestors = pending.pop()
        if module in ancestors:
            raise ValueError("Recursive module hierarchy is unsupported")
        for item in children[module]:
            path = prefix + item["instance"]
            resolved.append({"path": path, "type": item["type"], "unconnected_pins": item["unconnected_pins"]})
            if "connections" in item:
                resolved[-1]["connections"] = item["connections"]
            if item["type"] in control_bodies:
                resolved[-1]["output_pins"] = re.findall(r"(?m)^\s*output\s+(\w+)\s*;", control_bodies[item["type"]])
            if item["type"] in gate_sample_types:
                resolved[-1]["representative_synthesized_clock_gate"] = True
            if item["type"] in children:
                pending.append((item["type"], path + "/", ancestors + (module,)))
    # Synthesized large cores contain tens of thousands of repeated clock-gate
    # wrappers. Keep functional hierarchy and relevant controls, not every copy.
    # Floating hierarchy pins remain visible regardless of module naming.
    if len(resolved) > 2000:
        resolved = [item for item in resolved if item["path"].count("/") < 2 or item["unconnected_pins"] or item["type"] in gate_sample_types
                    or (not item["type"].startswith("SNPS_CLOCK_GATE") and
                        re.search(r"(?:clk|clock).*top|jtag|tap|dft|reset|BUFG", item["type"], re.I))]
    resolved = [item for item in resolved if not item["type"].startswith("SNPS_CLOCK_GATE")
                or item["type"] in gate_sample_types or item["unconnected_pins"]]
    used_types = roots | {item["type"] for item in resolved}
    for index, item in enumerate(resolved):
        item["selector"] = f"[[H{index}]]"
    body_types = gate_sample_types | {item["type"] for item in resolved if item["unconnected_pins"] or item["path"].count("/") < 2}
    return [" ".join(header) if name in roots else control_bodies[name] if name in body_types and name in control_bodies else header[0]
            for name, header in modules if name in used_types], resolved
