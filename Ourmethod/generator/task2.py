"""Original Task 2 diagnosis, script generation, rewriting and stage ordering."""

import json
import time

from verifier.acceptance import STAGES
from model_contract import TASK2, REPAIR


def stage_lines(script):
    return [(i, line.split()[0]) for i, line in enumerate(script.splitlines()) if line.strip() and line.split()[0] in STAGES]


def order_full_flow(script):
    """Compile the documented order for a single full-insertion flow only."""
    lines = script.splitlines()
    stages = stage_lines(script)
    if sorted(command for _, command in stages) != sorted(STAGES):
        raise ValueError("Expected exactly one DRC, chain preview and insertion stage")
    # Preserve intervening report statements; reorder only the three stage slots.
    commands = {command: lines[index] for index, command in stages}
    for (index, _), command in zip(stages, STAGES):
        lines[index] = commands[command]
    return "\n".join(lines) + "\n"


def diagnose_script(source, spec, relocated, log1, guide, deadline, generate, design_outline):
    instruction = (
        "Repair a Scan Insertion dofile from actual task, source and tool evidence. "
        "hierarchical_instances contains selector tokens such as [[H12]]; use these in dofile "
        "instead of retyping long paths, e.g. set_scan_element false {[[H12]]}. The runner "
        "expands each token to its exact source instance path. "
        "module_declarations includes the actual top interface. Large-design hierarchy "
        "context is filtered to major hierarchy and clock/JTAG/DFT controls, not exhaustive. "
        "Return JSON with dofile, issues "
        "(array of {phenomenon, located_object, root_cause, violated_requirement, "
        "original_command, fix_action}). original_command must be an exact nonempty substring "
        "of the original working dofile, used as static evidence; identify all independent defects "
        "including ones masked by early errors. Do not claim masked errors were observed at runtime. "
        "Count each independent root cause ONCE: missing load_netlist, failed present_design and "
        "missing downstream outputs are one root cause, not three issues. The runner relocated "
        "path variables correctly for this run; these assignments are NOT defects and are NOT "
        "competition requirements. Never count runner implementation choices as task defects. "
        "Emit exactly one issue object per independent root cause. If two unrelated causes "
        "occur on the same command, emit two objects with distinct root_cause values; if one "
        "cause affects several commands, emit one object and cite the smallest exact command "
        "that proves it. Never combine unrelated causes with 'and'. Missing runner-requested "
        "auxiliary evidence exports is not a defect unless the task explicitly requires that "
        "named output. A task statement saying a DRC rule may be ignored is permission, not a "
        "requirement to add an ignore configuration; report such a rule only when actual DRC "
        "evidence shows its unhandled effect violates another explicit requirement. "
        "In the repaired script, apply task-permitted DFTR-TIE0/DFTR-TIE1 Ignore handling "
        "before DRC so exempt tie warnings do not remain in the final report. This is not "
        "by itself a separately scored original defect. "
        "Do not modify input netlists, library or protected files. No fabricated results. "
        "The runner, not Tcl, creates decision_log.json and publishes the accepted script "
        "as final.dofile after verification. Never write either of those files in dofile. "
        "The runner defines input_dir, out_res_dir and out_rpt_dir. Use those variables, "
        "export with literal paths like $out_res_dir/file.v or $out_rpt_dir/file.rpt; "
        "do not redefine them. Correct all confirmed faults in one script and run the full flow. "
        "Use only documented commands. End with exit."
        " The replacement must contain exactly one examine_scan_drc, exactly one "
        "examine_scan_chain and exactly one insert_dft_logic in that order; do not "
        "add a second DRC merely to produce another report. Treat omitted optional "
        "scan-enable -usage as the documented default only when the task guide says "
        "so, and do not call a default setting a defect without task or runtime evidence. "
        "Likewise, reset/clock inactive polarity is task-specific: cite the task's "
        "explicit polarity or an actual DRC consequence, never a generic off_state rule."
        " Preserve every original clock, partition, internal-clock and chain-length "
        "setting unless the task explicitly contradicts it or the actual report proves "
        "that particular setting is the cause; do not simplify a multidomain flow."
        " Always export rpt_scan_cfg, rpt_scan_signal, rpt_scan_chain, "
        "rpt_scan_chain_cell and rpt_insertion_info after insertion, plus the DRC report, "
        "so acceptance can examine actual configuration and coverage. These are evidence "
        "exports, not additional alleged defects when absent from the original script."
    )
    modules, instances = design_outline(source)
    prompt = json.dumps({"task_spec": spec, "original_working_dofile": relocated, "actual_run1_log": log1[:22000], "tool_guide": guide, "module_declarations": modules, "hierarchical_instances": instances, "input_files": [str(p.relative_to(source)) for p in source.rglob("*") if p.suffix in {".v", ".lib", ".ctl"}]}, ensure_ascii=False)
    plan, usage = generate(prompt, max(1, deadline - time.monotonic()), instruction, output_schema=TASK2)
    return plan, usage, instruction, modules, instances


def revise_script(spec, guide, plan, modules, instances, second, missing, feedback, deadline, instruction, generate):
    revised, retry_usage = generate(json.dumps({
        "task": spec, "tool_guide": guide, "previous_dofile": plan["dofile"],
        "module_declarations": modules, "hierarchical_instances": instances,
        "execution": second, "missing_outputs": missing, "acceptance_feedback": feedback,
    }, ensure_ascii=False), deadline - time.monotonic(),
        instruction + " For this follow-up return only dofile and repair_action strings. "
        "Correct the remaining acceptance failures using actual reports, including signal "
        "polarities and scannability. Do not ignore additional DRC rules or exclude cells "
        "to hide failures. Return the complete replacement body, without runner variable "
        "assignments. Use exactly one examine_scan_drc, examine_scan_chain and full "
        "insert_dft_logic; do not add preprocessing insert_dft_logic calls. "
        "Do not assume an earlier verifier resolved=true proves every setting is correct. "
        "In particular inspect scan-enable inactive level against functional clock-gating "
        "bypass polarity when most FFs have clock DRC violations.", output_schema=REPAIR)
    return revised, retry_usage
