"""Execute a dofile in a fresh Scan process and retain its actual artifacts."""

import argparse
import difflib
import json
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


def publish_run(target, run_dir):
    """Copy only the accepted round; do not regenerate any deliverable."""
    final = target / "final_results"
    shutil.copytree(run_dir / "deliverables", final / "deliverables")
    (final / f"deliverables/{run_dir.name}.dofile").rename(final / "deliverables/final.dofile")
    pre_scan = final / f'deliverables/pre_scan_{run_dir.name}.v'
    if pre_scan.exists():
        pre_scan.rename(final / 'deliverables/pre_scan_final.v')
    shutil.copytree(run_dir / "reports", final / "reports")
    shutil.copyfile(run_dir / f"{run_dir.name}.log", final / "final.log")


def declared_artifacts(script):
    """Literal paths, including the official examples' single-file `file join`."""
    paths = {
        ("deliverables/" if (braced or bare) == "out_res_dir" else "reports/") + filename
        for braced, bare, filename in re.findall(
            r'\$(?:\{(out_res_dir|out_rpt_dir)\}|(out_res_dir|out_rpt_dir))/([^"\s}]+)', script)
    }
    paths.update(
        ("deliverables/" if (braced or bare) == "out_res_dir" else "reports/") + filename
        for braced, bare, filename in re.findall(
            r'\[file\s+join\s+\$(?:\{(out_res_dir|out_rpt_dir)\}|(out_res_dir|out_rpt_dir))\s+([^\s${}\[\]";]+)\s*\]', script)
    )
    if not paths:
        raise ValueError("No literal artifact paths declared by the supported dofile flow")
    return sorted(paths)


def run_scan(dofile, run_dir, timeout, *, netlist_edits=(), source=None):
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
    lec = None
    if netlist_edits:
        from netlist_repair import prove
        lec = prove(source, run_dir, netlist_edits, dofile, started + timeout)
        proven_bytes = (run_dir / lec['candidate']).read_bytes()
    with log.open("wb") as stream:
        process = None
        try:
            if lec is not None and not lec['passed']:
                stream.write(b'[AGENT] EQY did not prove equivalence; Scan was not launched.\n')
                raise ValueError('EQY did not prove equivalence')
            process = subprocess.Popen(
                ["dftexp_scan", "-f", str(script)],
                cwd=run_dir, stdout=stream, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            code = process.wait(timeout=max(0.001, started + timeout - time.monotonic()))
        except (subprocess.TimeoutExpired, TimeoutError) as exc:
            status = "aborted"
            failure = exc
        except OSError as exc:
            failure = exc
        except ValueError as exc:
            if lec is None or lec['passed']:
                raise
            failure = exc
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                code = process.wait()
    text = log.read_text(encoding="utf-8", errors="replace")
    if lec is not None:
        lec['candidate_unchanged_after_scan'] = (run_dir / lec['candidate']).read_bytes() == proven_bytes
        lec['passed'] = lec['passed'] and lec['candidate_unchanged_after_scan']
        (run_dir / 'lec_result.json').write_text(json.dumps(lec, indent=2), encoding='utf-8')
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
        "scan_invoked": process is not None,
    }
    if source is not None:
        from control_context import constant_output_check, mapping_input_losses
        result['cell_mapping_input_losses'] = mapping_input_losses(
            Path(source), text, candidate=run_dir / lec['candidate'] if lec is not None else None)
        # Wrapper outputs legitimately become test-mode controllable. This
        # narrow constant check applies only without actual dedicated wrapper
        # bits; it is not a functional-mode wrapper proof.
        wrapped = any(re.search(r'Wrapper Chain Cell Bit Count\s+[1-9]\d*', path.read_text(encoding='utf-8', errors='replace'))
                      for path in reports.glob('*.rpt'))
        if code == 0 and not errors and not wrapped:
            outputs = [run_dir / name for name in declared_artifacts(dofile)
                       if name.endswith('.v') and (run_dir / name).is_file()
                       and (lec is None or name != lec['candidate'])]
            try:
                result['constant_output_integrity'] = constant_output_check(
                    Path(source), dofile, outputs, started + timeout,
                    candidate=run_dir / lec['candidate'] if lec is not None else None)
            except (subprocess.TimeoutExpired, TimeoutError) as exc:
                failure = exc
                result.update(exit_status='aborted', failure_type=type(exc).__name__,
                    constant_output_integrity={'checked': False, 'reason': 'Current run deadline expired during constant-output checking'})
    result['artifacts'] = [str(p.relative_to(run_dir)) for p in run_dir.rglob('*') if p.is_file()]
    result['elapsed_seconds'] = round(time.monotonic() - started, 3)
    (run_dir / "execution.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    if failure is not None and not isinstance(failure, (subprocess.TimeoutExpired, ValueError)):
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
