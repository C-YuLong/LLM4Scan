"""Run a bounded, sequential public-case regression stage.

Each case gets a fresh result directory.  The batch summary is intentionally
small and records execution and independent acceptance separately.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime


DEFAULT_CASES = tuple(f"task_1/case{i}" for i in range(1, 7)) + tuple(f"task_2/case{i}" for i in range(1, 6))
AUDITS = {"task_1/case2": "ac97", "task_2/case1": "picorv32", "task_2/case2": "ethernet", "task_2/case3": "tv80", "task_2/case4": "cv32e40p"}
AUDITS.update({"task_1/case5": "openc906", "task_2/case5": "veer"})


def _tokens(result_dir):
    total = {key: 0 for key in ("input_tokens", "output_tokens", "total_tokens")}
    records, calls, cached, cache_complete = [], 0, 0, True
    paths = sorted(result_dir.glob("*.json"))
    if (result_dir / "verification.json").exists() and any(result_dir.glob("verification_R*.json")):
        paths = [path for path in paths if path.name != "verification.json"]
    for path in paths:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            continue
        if path.name == 'decision_log.json' and value.get('failure_type') in {'HTTPError', 'TimeoutError'}:
            records.append({'file': path.name, 'unknown_usage_reason': 'Failed or interrupted run may have requests without returned usage',
                            'failure_type': value['failure_type'], 'http_status': value.get('http_status')})
        attempts = value.get('diagnostic_attempts') or value.get('attempts')
        if not attempts and 'usage' not in value:
            continue
        # Per-attempt usage is authoritative: diagnostic/extraction roots hold
        # aggregates, while verification roots hold only the last attempt.
        for index, attempt in enumerate(attempts or [value], 1):
            calls += 1
            usage = attempt.get('usage') or {}
            if usage.get('failed_http_statuses'):
                records.append({'file': path.name, 'attempt': index, 'unknown_usage_reason': 'HTTP retries did not return provider usage',
                                'http_statuses': usage['failed_http_statuses']})
            missing = [key for key in total if not isinstance(usage.get(key), int)]
            if missing:
                records.append({"file": path.name, "attempt": index, "missing": missing})
            else:
                for key in total:
                    total[key] += usage[key]
            details = usage.get('input_tokens_details') or usage.get('prompt_tokens_details') or {}
            hit = details.get('cached_tokens')
            if not isinstance(hit, int):
                cache_complete = False
            else:
                cached += hit
    return {"totals": total, "incomplete": records, "calls": calls,
            "scope": "Recorded response usage only; failed/interrupted requests may be unreported, not a billing statement",
            "cached_input_tokens": cached if calls and cache_complete else None,
            "uncached_input_tokens": total['input_tokens'] - cached if calls and cache_complete and not records else None} if calls or records else None


def _runtime_acceptance(result_dir):
    decision = result_dir / "decision_log.json"
    if not decision.is_file():
        return {"available": False, "passed": None}
    value = json.loads(decision.read_text(encoding="utf-8"))
    final_id = value.get("final_run")
    acceptance = result_dir / "runs" / str(final_id) / "acceptance.json" if final_id else None
    if not acceptance or not acceptance.is_file():
        return {"available": False, "passed": False, "error": "final run acceptance.json missing"}
    data = json.loads(acceptance.read_text(encoding="utf-8"))
    checks = data.get("checks", {})
    return {"available": True, "passed": bool(checks) and all(checks.values()), "checks": checks}


def _validate_unique_outputs(results, prefix, names):
    if len(set(names)) != len(names):
        raise ValueError("duplicate output names are not allowed")
    summary_path = results / f"{prefix}_summary.json"
    if summary_path.exists() or any((results / name).exists() for name in names):
        raise ValueError("output prefix already exists; choose a new prefix")


def _audit(root, result_dir, case):
    mode = AUDITS.get(case)
    command = [sys.executable, str(root / "dev" / "verify_flow.py"), "--output", str(result_dir)]
    acceptance = result_dir / "flow_acceptance.json"
    if mode:
        command.append(f"--{mode}")
    if case in ("task_1/case3", "task_1/case6"):
        mode = "ctl" if case == "task_1/case3" else "des"
        command = [sys.executable, str(root / "dev" / "verify_wrapper.py"), "--case", mode, "--output", str(result_dir)]
        acceptance = result_dir / "wrapper_acceptance.json"
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True)
    if acceptance.is_file():
        try:
            data = json.loads(acceptance.read_text(encoding="utf-8"))
            checks = data.get("checks", {})
            if case in ("task_1/case1", "task_1/case4") and checks.get("accepted_final_run"):
                windows = os.name == "nt"
                def linux(path):
                    return "/mnt/" + path.drive[0].lower() + path.as_posix()[2:] if windows else str(path)
                source = root / "official/extracted/public_cases" / case / "input"
                structural = subprocess.run([
                    *(["wsl", "-d", "podman-machine-default", "-u", "user", "--"] if windows else []), "podman", "run", "--rm", "--memory", "2g", "--cpus", "2",
                    "-v", f"{linux(root / 'dev')}:/devwork:rw", "-v", f"{linux(source)}:/input:ro",
                    "--entrypoint", "python3", "docker.io/library/scan-agent-base:ubuntu24",
                    "/devwork/verify_preprocessing.py", "--output", "/devwork/results/" + result_dir.name,
                ], cwd=root, capture_output=True)
                checks["structural_audit_completed"] = structural.returncode == 0
                structure_path = result_dir / "structural_check/acceptance.json"
                if structure_path.is_file():
                    structure = json.loads(structure_path.read_text(encoding="utf-8"))
                    checks.update({"structural_" + key: value for key, value in structure["checks"].items()})
                mode = "opene902_preprocessing_structure"
            return {"supported": True, "passed": bool(checks) and all(checks.values()), "checks": checks,
                    "exit_code": completed.returncode, "semantic_scope": mode or "provenance_only"}
        except (OSError, json.JSONDecodeError) as exc:
            return {"supported": True, "passed": False, "checks": {}, "exit_code": completed.returncode,
                    "semantic_scope": mode or "provenance_only", "error": f"invalid audit output: {exc}"}
    return {"supported": True, "passed": False, "checks": {}, "exit_code": completed.returncode,
            "semantic_scope": mode or "provenance_only", "error": "audit did not produce valid flow_acceptance.json"}


def main():
    root = Path(__file__).resolve().parents[1]
    public_root = root / "official" / "extracted" / "public_cases"
    parser = argparse.ArgumentParser()
    available = sorted(path.parent.relative_to(public_root).as_posix()
                       for path in public_root.glob("task_*/case*/input") if path.is_dir())
    parser.add_argument("--cases", nargs="+", default=list(DEFAULT_CASES), choices=available)
    parser.add_argument("--competition-api", action="store_true", help="Pass through to run_local: use DashScope key from .env")
    parser.add_argument("--output-prefix", help="Unique prefix for per-case directories")
    parser.add_argument("--image", help="Use this built submission image for every case")
    parser.add_argument('--provider', choices=('gproxy', 'deepseek'), default='gproxy',
                        help='Use the selected authorized provider for every case')
    args = parser.parse_args()
    results = root / "dev" / "results"
    prefix = args.output_prefix or ("stage_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    if len(set(args.cases)) != len(args.cases):
        raise SystemExit("duplicate cases are not allowed")
    if Path(prefix).name != prefix or prefix in {".", ".."}:
        raise SystemExit("--output-prefix must be one directory-name prefix")
    names = [f"{prefix}_{case.replace('/', '_')}" for case in args.cases]
    summary_path = results / f"{prefix}_summary.json"
    try:
        _validate_unique_outputs(results, prefix, names)
    except ValueError as exc:
        raise SystemExit(str(exc))
    records = []
    def save_summary():
        summary = {"prefix": prefix, "image": args.image, "provider": args.provider, "cases": records,
                   "requested_cases": list(args.cases), "finished": len(records) == len(args.cases),
                   "failed_cases": [r["case"] for r in records if r["exit_code"] != 0 or
                                    r["independent_acceptance"].get("passed") is not True or
                                    r["runtime_acceptance"].get("passed") is not True],
                   "created_at": datetime.now().isoformat(timespec="seconds")}
        summary["all_passed"] = summary["finished"] and not summary["failed_cases"]
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    save_summary()
    for case, name in zip(args.cases, names):
        output = results / name
        started = time.monotonic()
        command = [sys.executable, str(root / "dev" / "run_local.py"), "--case", case,
                   "--output", name, '--provider', args.provider]
        if args.image:
            command.extend(["--image", args.image])
        if args.competition_api:
            command.append("--competition-api")
        completed = subprocess.run(command, cwd=root)
        audit = _audit(root, output, case) if output.is_dir() else {"supported": True, "passed": False,
                                                                      "error": "runner produced no result directory"}
        records.append({"case": case, "output": name, "exit_code": completed.returncode,
                        "status": "completed" if completed.returncode == 0 else "failed",
                        "duration_seconds": round(time.monotonic() - started, 3),
                        "independent_acceptance": audit,
                        "runtime_acceptance": _runtime_acceptance(output) if output.is_dir() else {"available": False, "passed": False},
                        "token_usage": _tokens(output) if output.is_dir() else None})
        save_summary()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    print(json.dumps(summary, ensure_ascii=False))
    return 1 if summary["failed_cases"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
