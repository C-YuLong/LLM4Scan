"""Original Task 1 generation context and feedback-driven rewrite request."""

import json
from pathlib import Path
import time


TASK1_INSTRUCTIONS = (
    "You are a Scan Insertion engineer. Return only JSON with dofile (string), "
    "requirements (array of {requirement, command}). Every requirement must map to an "
    "exact command substring of dofile. Use supplied tool documentation. Never fabricate "
    "logs, reports, or netlists. Do not modify input files. Do not execute shell commands. "
    "Use $input_dir, $out_res_dir, $out_rpt_dir which are defined by the runner. "
    "Export with literal paths like $out_res_dir/file.v or $out_rpt_dir/file.rpt. "
    "Only generate the task's requested stages; preprocessing tasks must not build chains. "
    "hierarchical_instances provides selector tokens like [[H12]] for actual instances. "
    "Use those tokens in dofile AND requirement commands instead of retyping hierarchy paths, "
    "e.g. add_pseudo_pi {[[H12]]/clk_out}. The runner expands tokens to exact source paths. "
    "Never add ancestors or prepend the top module. "
    "Instance connections map PIN NAME keys to PARENT NET NAME values: append the KEY "
    "to an instance selector, never the connected net value. For .Q(net42), the pin "
    "is [[H12]]/Q, not [[H12]]/net42. Declare pseudo inputs BEFORE defining their clock signals. "
    "A leaf name mentioned by the task is not necessarily a top-level instance. Never silently ignore "
    "a Nothing matched warning for a required object selector. "
    "For full_insertion=true, run exactly one examine_scan_drc, examine_scan_chain, "
    "insert_dft_logic in that order (literal command is insert_dft_logic, never 'full insert_dft_logic'). "
    "If required, a separate -connect_icg_only "
    "preprocessing call is allowed BEFORE those three stages, not after DRC. "
    "Export DRC, scan_chain, scan_cfg, scan_signal, "
    "scan_chain_cell and insertion_info reports for acceptance, plus all requested outputs. "
    "Also return stages: an ordered array of {id (<=12 chars), label (short operation intent), "
    "command (one complete operation command exactly present in dofile), export_command "
    "(the subsequent complete dump_netlist command, or null if no snapshot is exported)}. "
    "Describe each design-operation stage, not every configuration command. Use the same "
    "hierarchy selector tokens in stage commands. Never invent exports; no extra snapshots "
    "are requested. Regenerate stages for every repair candidate. "
    "End with exit. Do not define or change the runner's path variables."
    " When previous_run is provided, also return repair with located_object, root_cause "
    "and action strings explaining the correction. Recheck the original task's distinct "
    "object scopes: ICG exclusions and non-scannable FF hierarchies are separate requirements."
)


def build_prompt(source, specification, full_insertion, limitations, files, modules, instances, guide):
    # Small supplied component definitions expose custom wrapper pins and CTL
    # interfaces; declarations alone omit the information needed to map them.
    component_sources = {name: (source / name).read_text(encoding="utf-8") for name in files
                         if Path(name).suffix in {".v", ".ctl"} and (source / name).stat().st_size <= 32768}
    prompt = json.dumps({"task_spec": specification, "full_insertion": full_insertion, "limitations": limitations, "files": files, "module_declarations": modules, "hierarchical_instances": instances, "component_sources": component_sources, "tool_guide": guide}, ensure_ascii=False)
    return prompt


def revise_script(prompt, plan, result, acceptance, artifacts, old_id, deadline, generate):
    repair_input = json.loads(prompt)
    repair_input["previous_run"] = {"dofile": plan["dofile"], "errors": result["errors"],
                                    "acceptance": acceptance,
                                    "tool_messages": "\n".join(line for line in artifacts[f"{old_id}.log"].splitlines()
                                                               if any(tag in line for tag in ("CMD-0034", "WARNING", "ERROR")))[:20000],
                                    "report_excerpts": {name: text[:12000] for name, text in artifacts.items()
                                                        if name.endswith(".rpt") and "CellNo" not in text}}
    plan, repair_usage = generate(json.dumps(repair_input, ensure_ascii=False), deadline - time.monotonic())
    return plan, repair_usage
