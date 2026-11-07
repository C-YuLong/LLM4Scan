"""Execute a dofile in a fresh Scan process and retain its actual artifacts."""

import argparse
import difflib
import json
import telemetry
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import time


def resolve_selectors(text, instances):
    """Expand model-selected IDs to exact source paths; never fuzzy-match names."""
    paths = {item["selector"]: item["path"] for item in instances}
    return re.sub(r"\[\[H\d+\]\]", lambda match: paths[match[0]], text)


def generated_script(source, run_dir, body):
    """Paths belong to the runner, never to a generated candidate."""
    if re.search(r"(?m)^\s*set\s+(?:input_dir|out_res_dir|out_rpt_dir)\s", body):
        raise ValueError("Generated body must not redefine runner-owned paths")
    return (f'set input_dir {{{source.as_posix()}}}\n'
            f'set out_res_dir {{{(run_dir / "deliverables").as_posix()}}}\n'
            f'set out_rpt_dir {{{(run_dir / "reports").as_posix()}}}\n' + body)


def record_change(target, old_id, run_id, previous, script, change_id):
    diff_path = f"diffs/dofile_{old_id}_to_{run_id}.diff"
    path = f"runs/{run_id}/deliverables/{run_id}.dofile"
    (target / "diffs").mkdir(exist_ok=True)
    (target / diff_path).write_text("".join(difflib.unified_diff(
        previous.splitlines(True), script.splitlines(True),
        fromfile=f"runs/{old_id}/deliverables/{old_id}.dofile", tofile=path)), encoding="utf-8")
    return {"change_id": change_id, "type": "dofile", "path": path, "diff_path": diff_path, "lec_ref": ""}


@telemetry.tracked("publish", "复制通过验收的最终产物", run_arg=1)
def publish_run(target, run_dir):
    """Copy only the accepted round; do not regenerate any deliverable."""
    final = target / "final_results"
    shutil.copytree(run_dir / "deliverables", final / "deliverables")
    (final / f"deliverables/{run_dir.name}.dofile").rename(final / "deliverables/final.dofile")
    shutil.copytree(run_dir / "reports", final / "reports")
    shutil.copyfile(run_dir / f"{run_dir.name}.log", final / "final.log")


def declared_artifacts(script):
    """Artifact paths for the literal $out_*_dir/filename syntax we generate."""
    paths = {
        ("deliverables/" if variable == "out_res_dir" else "reports/") + filename
        for variable, filename in re.findall(r'\$(out_res_dir|out_rpt_dir)/([^"\s}]+)', script)
    }
    if not paths:
        raise ValueError("No literal artifact paths declared by the supported dofile flow")
    return sorted(paths)


@telemetry.tracked("scan", "执行真实 Scan 工具", run_arg=1)
def run_scan(dofile, run_dir, timeout, *, stages=None, source=None):
    run_dir = Path(run_dir).resolve()
    deliverables = run_dir / "deliverables"
    reports = run_dir / "reports"
    deliverables.mkdir(parents=True, exist_ok=False)
    reports.mkdir()
    script = deliverables / (run_dir.name + ".dofile")
    script.write_text(dofile, encoding="utf-8")
    log = run_dir / (run_dir.name + ".log")
    started = time.monotonic()
    status = "error"
    code = None
    failure = None
    with log.open("wb") as stream:
        process = None
        try:
            process = subprocess.Popen(
                ["dftexp_scan", "-f", str(script)],
                cwd=run_dir, stdout=stream, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            code = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, TimeoutError) as exc:
            status = "aborted"
            failure = exc
        except OSError as exc:
            failure = exc
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                code = process.wait()
    text = log.read_text(encoding="utf-8", errors="replace")
    errors = [line for line in text.splitlines() if re.search(r"\[ERROR\]|^Error:", line)]
    if status != "aborted" and code == 0 and not errors:
        status = "completed"
    result = {
        "tool_call_id": run_dir.name,
        "exit_status": status,
        "returncode": code,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "errors": errors,
        "failure_type": type(failure).__name__ if failure else None,
        "artifacts": [str(p.relative_to(run_dir)) for p in run_dir.rglob("*") if p.is_file()],
    }
    if stages is not None and source is not None:
        from stage_artifacts import build_stage_artifacts
        stage_record=build_stage_artifacts(dofile,text,stages,source,run_dir)
        (run_dir/"stage_artifacts.json").write_text(json.dumps(stage_record,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        result["stage_artifacts"]="stage_artifacts.json"
        result["artifacts"].append("stage_artifacts.json")
    (run_dir / "execution.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    if failure is not None and not isinstance(failure, subprocess.TimeoutExpired):
        raise failure
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dofile", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--timeout", type=float, required=True)
    args = parser.parse_args()
    result = run_scan(Path(args.dofile).read_text(encoding="utf-8"), args.run_dir, args.timeout)
    print(json.dumps(result))
    return 0 if result["exit_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
