"""Original checks for diagnosis citations, runner paths and duplicate findings."""

import re
import telemetry


_RUNNER_PATH_ASSIGNMENT = re.compile(
    r"set\s+(?:script_dir|base_dir|out_rpt_dir|out_res_dir)\s+[^\n;]+"
)

@telemetry.tracked("diagnosis_check", "检查模型诊断引用并去重")
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
