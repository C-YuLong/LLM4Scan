"""Unchanged Task 2 execute/diagnose/repair/verify orchestration."""

import difflib
import json
import os
from pathlib import Path
import re
import time
import telemetry

from scan_runner import run_scan, generated_script, record_change, publish_run, resolve_selectors
from generator.task2 import diagnose_script, revise_script as revise_task2, stage_lines, order_full_flow
from verifier.acceptance import STAGES
from verifier.diagnosis import sanitize_diagnoses
from verifier.task2 import verify_round


def repair(source, target, started, deadline, generate, design_outline):
    with telemetry.phase("input", "读取原始脚本并迁移工作路径"):
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
    plan, usage, instruction, modules, instances = diagnose_script(source, spec, relocated, log1, guide, deadline, generate, design_outline)
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
        revised, retry_usage = revise_task2(spec, guide, plan, modules, instances, second, missing, feedback, deadline, instruction, generate)
        (target / f"model_repair_R{len(runs) + 1}.json").write_text(json.dumps({"plan": revised, "usage": retry_usage}, ensure_ascii=False, indent=2), encoding="utf-8")
        plan["dofile"] = revised["dofile"]
        for issue in plan["issues"]:
            issue["fix_action"] = revised["repair_action"]
        previous = fixed
    print(json.dumps({"repair_completed": bool(success), "issues": len(history), "rounds": len(runs), "elapsed": round(time.monotonic() - started, 2)}))
    return 0 if success else 1
