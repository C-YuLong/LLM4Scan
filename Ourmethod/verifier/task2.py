"""Original Task 2 model verification and extraction of actual evidence."""

import json
import re
import time
import telemetry

from .acceptance import inspect_run
from model_contract import verification as verification_schema


def verify_round(target, run2, fixed, second, plan, relocated, spec, guide, deadline, change_id, generate):
    run_id = run2.name
    log_name = f"{run_id}.log"
    acceptance, artifacts, workflow_check = inspect_run(run2, fixed, second, full_insertion=True, task_spec=spec)
    with telemetry.phase("evidence", f"整理 {run_id} 的模型核验证据"):
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
