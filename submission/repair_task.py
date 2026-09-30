"""Task-two configuration repair milestone; input netlist edits are unsupported."""

import difflib
import json
import os
from pathlib import Path
import re
import time

from scan_runner import run_scan, generated_script, record_change, publish_run, resolve_selectors
from acceptance import STAGES, inspect_run
from model_contract import TASK2, REPAIR, verification as verification_schema


_RUNNER_PATH_ASSIGNMENT = re.compile(
    r"set\s+(?:script_dir|base_dir|out_rpt_dir|out_res_dir)\s+[^\n;]+"
)

def sanitize_diagnoses(plan, original_script):
    """Keep only independently citable findings from a model diagnosis.

    The model is useful for naming a root cause, but it is not allowed to
    create evidence.  In particular, path assignments injected by the runner
    are implementation details and must never become scored findings.  The
    rejected records are retained for audit/debugging, while the returned
    issue list is the stable input to the verifier.
    """
    raw = plan.get("issues", [])
    if not isinstance(raw, list):
        raise ValueError("Model diagnosis issues must be an array")
    accepted, rejected = [], []
    seen_pairs = set()
    for position, issue in enumerate(raw, 1):
        reason = None
        if not isinstance(issue, dict):
            reason = "issue is not an object"
        else:
            command = issue.get("original_command")
            root = issue.get("root_cause")
            required = ("phenomenon", "located_object", "root_cause", "violated_requirement", "original_command", "fix_action")
            if any(not isinstance(issue.get(field), str) or not issue[field].strip() for field in required):
                reason = "missing non-empty diagnosis field"
            elif command not in original_script:
                reason = "original_command is not an exact substring of the original script"
            elif _RUNNER_PATH_ASSIGNMENT.fullmatch(command.strip()):
                reason = "runner-owned path assignment is not a task defect"
            if reason is None and isinstance(issue, dict) and all(
                    isinstance(issue.get(field), str) and issue[field].strip()
                    for field in ("phenomenon", "located_object", "root_cause", "violated_requirement", "original_command", "fix_action")):
                command_key = re.sub(r"\s+", " ", command.strip())
                root_key = re.sub(r"\s+", " ", root.strip().lower())
                pair = (command_key, root_key)
                if pair in seen_pairs:
                    reason = "duplicate command and root cause"
                else:
                    seen_pairs.add(pair)
        if reason:
            rejected.append({"position": position, "reason": reason, "issue": issue})
        else:
            accepted.append(issue)
    plan["issues"] = accepted
    plan["rejected_diagnoses"] = rejected
    return plan


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


def repair(source, target, started, deadline, generate, design_outline):
    original = (source / "original.dofile").read_text(encoding="utf-8")
    run1 = target / "runs/R1"
    # Preserve command semantics; replace only the supplied example's path setup.
    relocated = original
    for name, value in {
        "script_dir": source, "base_dir": run1,
        "out_rpt_dir": run1 / "reports", "out_res_dir": run1 / "deliverables",
    }.items():
        relocated = re.sub(r"(?m)^set\s+" + name + r"\s+[^\n]*$", lambda m: f"set {name} {{{value.as_posix()}}}", relocated)
    (target / "diffs").mkdir()
    (target / "diffs/original_to_R1_paths.diff").write_text("".join(difflib.unified_diff(original.splitlines(True), relocated.splitlines(True), fromfile="input/original.dofile", tofile="runs/R1/deliverables/R1.dofile")), encoding="utf-8")
    first = run_scan(relocated, run1, max(1, deadline - time.monotonic()))
    log1 = (run1 / "R1.log").read_text(encoding="utf-8", errors="replace")
    spec = (source / "task_spec.md").read_text(encoding="utf-8")
    guide = (Path(__file__).parent / "commands.md").read_text(encoding="utf-8")
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
    # Validate model findings before any verifier sees them.  This prevents a
    # hallucinated path/configuration complaint from becoming a scored issue,
    # while preserving it in model_plan.json for audit.
    sanitize_diagnoses(plan, relocated)
    (target / "model_plan.json").write_text(json.dumps({"plan": plan, "usage": usage}, ensure_ascii=False, indent=2), encoding="utf-8")
    for issue in plan["issues"]:
        for field in ("phenomenon", "located_object", "root_cause", "violated_requirement", "fix_action"):
            issue[field] = resolve_selectors(issue[field], instances)
    original_stages = stage_lines(relocated)
    if [command for _, command in original_stages] != list(STAGES):
        # Workflow is a documented invariant, not a model opinion. Keep one root cause.
        plan["issues"] = [issue for issue in plan["issues"] if not any(command in issue["original_command"] for command in STAGES)]
        plan["issues"].append({
            "phenomenon": "原始完整插链流程的阶段顺序不符合工具要求。",
            "located_object": "original.dofile 中 DRC、链预生成与插入语句",
            "root_cause": "原始顺序为 " + " → ".join(command for _, command in original_stages) + "；工具要求 examine_scan_drc → examine_scan_chain → insert_dft_logic。",
            "violated_requirement": "按照工具规定顺序完成 DRC、扫描链预生成及插入。",
            "original_command": relocated.splitlines()[original_stages[0][0]],
            "fix_action": "由程序按已核实的单次完整插链阶段顺序组织这三个命令。",
        })
    runs, changes, history = [first], [], []
    previous = relocated
    while True:
        run_id = f"R{len(runs) + 1}"
        old_id = runs[-1]["tool_call_id"]
        run2 = target / "runs" / run_id
        fixed = generated_script(source, run2, order_full_flow(resolve_selectors(plan["dofile"], instances)))
        change_id = f"F{len(changes) + 1}"
        changes.append(record_change(target, old_id, run_id, previous, fixed, change_id))
        if time.monotonic() >= deadline:
            raise TimeoutError("No remaining budget for repaired Scan execution")
        second = run_scan(fixed, run2, deadline - time.monotonic())
        runs.append(second)
        issues, verification, missing, feedback = verify_round(target, run2, fixed, second, plan, relocated, spec, guide, deadline, change_id, generate)
        previous_issues = {item["issue_id"]: item for item in history}
        for current in issues:
            if current["issue_id"] in previous_issues:
                current["attempts"] = previous_issues[current["issue_id"]]["attempts"] + current["attempts"]
        history = issues
        success = bool(issues) and second["exit_status"] == "completed" and not missing and all(value is True for value in feedback["independent_checks"].values()) and verification["all_requirements_met"] is True and all(i["attempts"][-1]["verify"]["resolved"] for i in issues)
        decision = {
            "case_id": os.environ.get("CASE_ID", source.parent.name or source.name), "task": "task2", "final_run": run_id if success else None,
            "summary": verification["summary"], "independent_checks": feedback["independent_checks"], "issue_resolutions": issues,
            "rejected_diagnoses": plan.get("rejected_diagnoses", []) + feedback["rejected_diagnoses"],
            "tool_runs": [{"tool_call_id": r["tool_call_id"], "log_file": f'runs/{r["tool_call_id"]}/{r["tool_call_id"]}.log', "exit_status": r["exit_status"]} for r in runs],
            "file_changes": changes,
        }
        if success:
            publish_run(target, run2)
        (target / "decision_log.json").write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")
        if success or deadline - time.monotonic() <= 30:
            break
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
        (target / f"model_repair_R{len(runs) + 1}.json").write_text(json.dumps({"plan": revised, "usage": retry_usage}, ensure_ascii=False, indent=2), encoding="utf-8")
        plan["dofile"] = revised["dofile"]
        for issue in plan["issues"]:
            issue["fix_action"] = revised["repair_action"]
        previous = fixed
    print(json.dumps({"repair_completed": bool(success), "issues": len(history), "rounds": len(runs), "elapsed": round(time.monotonic() - started, 2)}))
    return 0 if success else 1


def verify_round(target, run2, fixed, second, plan, relocated, spec, guide, deadline, change_id, generate):
    run_id = run2.name
    log_name = f"{run_id}.log"
    acceptance, artifacts, workflow_check = inspect_run(run2, fixed, second, full_insertion=True, task_spec=spec)
    # Existing deterministic checks cover every report row, even when the model
    # view of a large report must be sampled. QA Q24 permits artifact evidence.
    artifacts["acceptance.json"] = (run2 / "acceptance.json").read_text(encoding="utf-8")
    artifacts[f"deliverables/{run_id}.dofile"] = fixed
    missing = acceptance["missing_outputs"]
    independent_checks = acceptance["checks"]
    # Keep command/status messages, not thousands of bare instance-name rows.
    # Full logs remain untouched and references retain original line numbers.
    views = {
        name: "\n".join(f"L{index}: {line}" for index, line in enumerate(text.splitlines(), 1)
                        if len(line) < 1000 and
                        (bool(re.match(r"\[(?:INFO|WARNING|ERROR)\]", line)) if name == log_name else index <= 160))
        for name, text in artifacts.items()
    }
    netlist_ports = {}
    for p in (run2 / "deliverables").glob("*.v"):
        with p.open(encoding="utf-8") as stream:
            netlist_ports[p.name] = [line.strip() for line in stream if re.match(r"\s*(?:module|input|output)\s", line)][:200]
    original_log = (target / "runs/R1/R1.log").read_text(encoding="utf-8", errors="replace")
    original_evidence = {"original_dofile": relocated, "original_run_log": "\n".join(
        line for line in original_log.splitlines() if re.match(r"\[(?:INFO|WARNING|ERROR)\]", line))[:22000]}
    verification, verify_usage = generate(json.dumps({"task": spec, "tool_guide": guide, "original_evidence": original_evidence, "issues": plan["issues"], "execution": second, "independent_acceptance": acceptance, "independent_workflow_check": workflow_check, "missing_outputs": missing, "output_netlist_port_declarations": netlist_ports, "actual_files": views}, ensure_ascii=False), max(1, deadline - time.monotonic()),
        "Verify Scan repair from actual evidence. Return JSON with all_requirements_met boolean, "
        "You are checking a candidate run BEFORE publication. The actual candidate script "
        "is in actual_files under deliverables/Rn.dofile. Once this verification succeeds, "
        "the runner copies that exact script to final_results/deliverables/final.dofile "
        "and writes the root decision_log.json from the actual run history and your evidence. "
        "Those two runner-owned publication files are deliberately absent from this candidate "
        "view; do NOT reject the candidate for their absence and do NOT request Tcl to "
        "generate them. Judge the tool artifacts and the supplied candidate dofile now. "
        "summary string, issues array in the same order as input issues, each containing resolved boolean, "
        "original_defect_supported boolean and support_basis string. original_defect_supported "
        "means the claimed original defect is proved by original_evidence and this task specification; "
        "original_command is a location anchor in the full original script. For an OMITTED required "
        "configuration, that anchor may itself be a legal command: inspect the full original script "
        "to establish the omission and use the original log where available. Static omission evidence "
        "does not require an explicit runtime error naming the missing command. original_evidence "
        "is deliberately separate from repaired actual_files; its absence from actual_files is NOT "
        "a reason to reject original support. Do not label the legal anchor command itself illegal. "
        "a command merely being present or a documented default is not support. Do not mark an "
        "issue supported only because the repaired run succeeds. "
        "Reject path-variable renaming and legal aliases as defects. Distinguish actual original "
        "defects from optional explicit spellings of defaults. The overall all_requirements_met "
        "judgment concerns the final task result, not whether all proposed original findings are true. "
        "When the task does not require DRC zero, do not impose it: judge remaining warnings by "
        "their effect on requested coverage and configuration, not their mere existence. "
        "source (exact key of actual_files), line_start and line_end (integers identifying a "
        "contiguous original line range proving repair). The application will extract the actual "
        "text; do not generate or paraphrase excerpts. Use the displayed L numbers, not positions "
        "within the sampled context. "
        "A contiguous range may include unrelated intervening messages: commands need not be "
        "adjacent. To verify workflow order use the independent_workflow_check's first and last "
        "observed command line numbers; all messages between them are valid to include. "
        "Do not invent evidence. Do not treat clean exit as proof of chain count, coverage or DRC. "
        "A new scan_enable port is intentionally absent before insertion; SCAN-4610 alone is not a failure. "
        "Runner path variable assignments are expected, not defects. Large reports in actual_files "
        "are sampled, but independent_acceptance checks every parsed chain row. For exhaustive "
        "chain-count, chain-length and coverage checks, a true corresponding check in actual_files "
        "acceptance.json is valid program-derived evidence; cite that check's actual line. Do not "
        "demand all report rows be re-sent when that complete check passed. Otherwise a chain-count "
        "reference must show all chain rows or an explicit total, not a single row. Prefer DRC summary and final "
        "chain reports for behavioral verification over an echoed configuration command. "
        "If evidence is insufficient mark unresolved."
        ' Each issue MUST include all these keys: {"resolved":false, '
        '"original_defect_supported":true,"support_basis":"reason about original defect",'
        '"source":"exact actual_files key","line_start":1,"line_end":1}. '
        "Do not put file/line references only inside support_basis; the separate reference keys "
        "are mandatory. Auxiliary evidence exports requested by the runner are not task defects. "
        "Permission to ignore a DRC rule is not an obligation to ignore it. "
        "Never omit an unsupported issue: return its record with original_defect_supported=false. "
        "The issues array MUST have exactly the same length and order as the input issues.",
        output_schema=verification_schema(len(plan["issues"])),
    )
    record = json.dumps({"verification": verification, "independent_checks": independent_checks, "usage": verify_usage}, ensure_ascii=False, indent=2)
    (target / "verification.json").write_text(record, encoding="utf-8")
    (target / f"verification_{run_id}.json").write_text(record, encoding="utf-8")
    issues, rejected = [], []
    if len(verification["issues"]) != len(plan["issues"]) or not plan["issues"]:
        raise ValueError("Missing issue verification records")
    for index, (issue, check) in enumerate(zip(plan["issues"], verification["issues"]), 1):
        if check.get("original_defect_supported") is not True:
            rejected.append({"issue_id": f"I{index}", "run_ref": run_id,
                             "reason": check.get("support_basis", "Original defect not supported by verifier"), "issue": issue})
            continue
        command = issue["original_command"]
        if not command or command not in relocated:
            raise ValueError("Static finding must cite actual original dofile")
        found_line = relocated[:relocated.index(command)].count("\n") + 1
        evidence_source = check.get("source", "")
        actual = artifacts.get(evidence_source, "")
        actual_lines = actual.splitlines()
        line, end_line = check.get("line_start"), check.get("line_end")
        valid_range = isinstance(line, int) and isinstance(end_line, int) and 1 <= line <= end_line <= len(actual_lines)
        supported = check.get("original_defect_supported") is True
        resolved = check["resolved"] and supported and valid_range and second["exit_status"] == "completed"
        excerpt = "\n".join(actual_lines[line - 1:end_line]) if resolved else ""
        issues.append({
            "issue_id": f"I{index}", "phenomenon": issue["phenomenon"],
            "found": {"run_ref": "R1", "source": "runs/R1/deliverables/R1.dofile", "locator": f"L{found_line}", "excerpt": command},
            "diagnosis": {"summary": "核对原始脚本、任务要求和真实运行结果；原始脚本引用为静态证据。", "located_object": issue["located_object"], "root_cause": issue["root_cause"], "violated_requirement": issue["violated_requirement"]},
            "attempts": [{"fix": {"action": issue["fix_action"], "artifact_ref": [change_id]}, "verify": {"run_ref": run_id, "source": f"runs/{run_id}/" + evidence_source if evidence_source else "", "resolved": bool(resolved), "original_defect_supported": supported, "support_basis": check.get("support_basis", ""), "locator": f"L{line}-L{end_line}" if resolved else "", "excerpt": excerpt}}],
        })
    return issues, verification, missing, {"verification": verification, "independent_checks": independent_checks, "actual_files": views, "rejected_diagnoses": rejected}
