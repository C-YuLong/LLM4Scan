"""Replay submission 3738's eight failure paths, without APIs or Scan execution.

Uses only recorded responses and task inputs, never golden/reference solutions.
Exit zero means the expected local observations hold, NOT eight cases passed.
"""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "submission"))

from acceptance import extract_task_constraints, inspect_run
from model_contract import ModelResponseError, TASK2, TASK_CONSTRAINTS, validate
from repair_task import sanitize_diagnoses, explicit_diagnosis_gaps
from scan_runner import declared_artifacts


def read_json(archive, name):
    return json.loads(archive.read(name))


def task_spec(case):
    return (ROOT / "official/extracted/public_cases" / case / "input/task_spec.md").read_text(encoding="utf-8")


def replay_extraction(archive, case, output):
    recorded = read_json(archive, f"{case}/task_contract.json")
    responses = deepcopy(recorded["attempts"])
    failure_member = f"{case}/model_response_failure.json"
    shape_error = None
    if failure_member in archive.namelist():
        failure = read_json(archive, failure_member)
        responses.append({"model_output": failure["plan"], "usage": failure["usage"]})
        shape_error = failure["validation_error"]
    calls = []

    def recorded_response(prompt, timeout, instruction, *, output_schema):
        # A request beyond the actual recording is an error, never a made-up answer.
        response = responses[len(calls)]
        raw, usage = response["model_output"], response["usage"]
        calls.append({"prompt": json.loads(prompt), "instruction": instruction})
        try:
            validate(raw, output_schema)
        except ValueError as exc:
            raise ModelResponseError(str(exc), raw, usage) from exc
        return raw, usage

    target = output / case
    target.mkdir(parents=True)
    error = None
    try:
        extract_task_constraints(task_spec(case), recorded_response, 300, target)
    except ValueError as exc:
        error = str(exc)
    (target / "replay_requests.json").write_text(json.dumps(calls, ensure_ascii=False, indent=2), encoding="utf-8")
    replayed = json.loads((target / "task_contract.json").read_text(encoding="utf-8"))
    # Validate the saved second response separately: C3 now accepts its first
    # response, so the malformed correction is no longer reached on this tape.
    saved_shape_error = None
    if shape_error:
        try:
            validate(responses[-1]["model_output"], TASK_CONSTRAINTS)
        except ValueError as exc:
            saved_shape_error = str(exc)
    return {
        "case": case,
        "stage": "task_constraint_extraction",
        "recorded_errors": [a.get("validation_error") for a in recorded["attempts"]],
        "recorded_schema_error": shape_error,
        "saved_invalid_correction_still_rejected": saved_shape_error,
        "current_stage_accepted": error is None,
        "current_error": error,
        "recorded_responses_consumed": len(calls),
        "recorded_responses_available": len(responses),
        "current_attempt_errors": [a.get("validation_error") for a in replayed["attempts"]],
        "note": "Historical responses were generated under old prompts; this does not test new model behavior.",
    }


def restore_diagnosis_plan(saved):
    """Undo only recorded sanitization, preserving issue text and original order."""
    plan = deepcopy(saved)
    rejected = {item["position"]: item["issue"] for item in plan.pop("rejected_diagnoses")}
    accepted = iter(plan["issues"])
    count = len(plan["issues"]) + len(rejected)
    plan["issues"] = [rejected[i] if i in rejected else next(accepted) for i in range(1, count + 1)]
    for issue in plan["issues"]:
        if "model_violated_requirement" in issue:
            issue["violated_requirement"] = issue.pop("model_violated_requirement")
    return plan


def replay_diagnosis(archive, output):
    case = "task_2/case4"
    spec = task_spec(case)
    recorded = read_json(archive, f"{case}/model_plan.json")
    script = archive.read(f"{case}/runs/R1/deliverables/R1.dofile").decode("utf-8")
    original = read_json(archive, f"{case}/runs/R1/acceptance.json")
    requirements = read_json(archive, f"{case}/task_contract.json")["requirements"]
    source = ROOT / "official/extracted/public_cases" / case / "input"
    schema = deepcopy(TASK2)
    schema["properties"]["issues"]["items"]["properties"]["original_source"] = {
        "type": "string", "enum": ["original.dofile"] + sorted(
            p.relative_to(source).as_posix() for p in (source / "netlist").rglob("*.v"))}
    attempts = []
    for attempt in recorded["diagnostic_attempts"]:
        plan = restore_diagnosis_plan(attempt["plan"])
        schema_error = None
        try:
            validate(plan, schema)
        except ValueError as exc:
            schema_error = str(exc)
        # Also expose the guard's evidence rejection, even when the new source
        # enum would stop this response before sanitization in the live flow.
        sources = {name: (source / name).read_text(encoding="utf-8")
                   for name in {i["original_source"] for i in plan["issues"]}
                   if name != "original.dofile" and name in schema["properties"]["issues"]["items"]["properties"]["original_source"]["enum"]}
        sanitize_diagnoses(plan, script, spec, sources)
        gaps = explicit_diagnosis_gaps(script, requirements, original["checks"], plan["issues"])
        attempts.append({"schema_error": schema_error, "plan": plan, "coverage_gaps": gaps})
    target = output / case
    target.mkdir(parents=True)
    (target / "diagnosis_replay.json").write_text(json.dumps(attempts, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "case": case,
        "stage": "diagnosis_evidence_and_coverage",
        "current_stage_accepted": not (attempts[-1]["schema_error"] or attempts[-1]["coverage_gaps"]
                                        or attempts[-1]["plan"]["rejected_diagnoses"]),
        "attempts": [{"schema_error": a["schema_error"], "citable_issues": len(a["plan"]["issues"]),
                      "rejected_issues": len(a["plan"]["rejected_diagnoses"]), "coverage_gaps": a["coverage_gaps"]}
                     for a in attempts],
        "note": "Old invalid citations remain rejected; no corrected model diagnoses were fabricated.",
    }


def replay_artifacts(archive, output):
    case = "task_2/case5"
    prefix = f"{case}/runs/R2/"
    script = archive.read(prefix + "deliverables/R2.dofile").decode("utf-8")
    execution = read_json(archive, prefix + "execution.json")
    contract = read_json(archive, f"{case}/task_contract.json")
    paths = declared_artifacts(script)
    run = output / case / "runs/R2"
    # Reinspect exact reports and the complete log. Do not copy huge unrelated
    # deliverables or create placeholders for files missing from the archive.
    replay_names = ["R2.log", "execution.json", "deliverables/R2.dofile"] + [n for n in paths if n.endswith(".rpt")]
    copied = []
    for name in replay_names:
        if prefix + name not in archive.namelist():
            continue
        target = run / name
        if not target.resolve().is_relative_to(run.resolve()):
            raise ValueError("Recorded artifact escapes replay directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(prefix + name) as src, target.open("xb") as dst:
            shutil.copyfileobj(src, dst)
        copied.append(name)
    acceptance, _, _ = inspect_run(run, script, execution, full_insertion=True,
                                    task_requirements=contract["requirements"],
                                    uncovered=contract["extraction"]["uncovered"])
    return {
        "case": case,
        "stage": "artifact_paths_and_recorded_run_acceptance",
        "path_parser_completed": True,
        "declared_paths": paths,
        "current_stage_accepted": acceptance["accepted"],
        "recorded_execution_status": execution["exit_status"],
        "recorded_tool_errors": execution["errors"],
        "failed_checks": [name for name, passed in acceptance["checks"].items() if passed is not True],
        "outputs_absent_from_archive": [n for n in paths if prefix + n not in archive.namelist()],
        "outputs_intentionally_not_copied": [n for n in paths if prefix + n in archive.namelist() and n not in copied],
        "note": "Partial artifact replay, not a Scan rerun. Real tool error must still reject this run.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ROOT / "dev/results/platform3738/intermediate_files.zip")
    parser.add_argument("--output", type=Path, required=True, help="New directory under dev/results; existing results are never overwritten")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "dev/results"):
        parser.error("Keep private replay artifacts under the Git-ignored dev/results directory")
    with zipfile.ZipFile(args.archive) as archive:
        output.mkdir(parents=True, exist_ok=False)
        results = [replay_extraction(archive, case, output) for case in (
            "task_1/case2", "task_1/case3", "task_1/case5", "task_1/case6", "task_2/case2", "task_2/case3")]
        results += [replay_diagnosis(archive, output), replay_artifacts(archive, output)]
    expected_accepts = {"task_1/case5", "task_1/case6", "task_2/case3"}
    checks = {
        "extraction_acceptance_matches_observed_fixes": all(
            r["current_stage_accepted"] == (r["case"] in expected_accepts) for r in results[:6]),
        "saved_bad_schema_still_rejected": bool(results[5]["saved_invalid_correction_still_rejected"]),
        "invalid_diagnoses_remain_blocked": all(a["coverage_gaps"] for a in results[6]["attempts"]),
        "wrong_diagnosis_source_rejected_by_schema": bool(results[6]["attempts"][0]["schema_error"]),
        "braced_paths_parsed": bool(results[7]["declared_paths"]),
        "tool_failure_remains_rejected": not results[7]["current_stage_accepted"] and "execution_completed" in results[7]["failed_checks"],
    }
    summary = {"scope": "Offline failure-path replay only; no API or Scan execution, no new case passes",
               "recording": str(args.archive), "checks": checks, "cases": results}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    for result in results:
        print(f"{result['case']}: {result['stage']} -> {'accepted at this step only' if result['current_stage_accepted'] else 'still rejected'}")
    print(f"Replay observations consistent: {all(checks.values())}; report: {output / 'summary.json'}")
    print("No new end-to-end passes or official score. No API calls or Scan execution.")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
