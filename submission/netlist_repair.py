"""Task-two working-copy edits and real EQY proof against unchanged inputs."""
import difflib
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time


def repaired_script(body, run_id):
    lines = body.splitlines()
    loads = [i for i, line in enumerate(lines) if re.match(r'^\s*load_netlist\s+', line)]
    if not loads:
        raise ValueError('Netlist repair requires an explicit load_netlist command')
    lines[loads[0]] = f'load_netlist "$out_res_dir/pre_scan_{run_id}.v"'
    for i in loads[1:]:
        lines[i] = '# Input modules are included in the runner-owned repaired netlist.'
    return '\n'.join(lines) + '\n'


def edited_netlists(source, edits):
    """Apply the exact edit contract without changing any input or output file."""
    source = Path(source).resolve()
    files = sorted((source / 'netlist').rglob('*.v'))
    originals = {p.relative_to(source).as_posix(): p.read_text(encoding='utf-8') for p in files}
    modified, errors = dict(originals), []
    for index, edit in enumerate(edits):
        name, before, after = edit['path'], edit['before'], edit['after']
        if name not in originals or not before or modified[name].count(before) != 1:
            count = modified[name].count(before) if name in originals and before else 0
            errors.append(f'netlist_edits[{index}] ({name}): before has {count} exact matches; copy one unique original source span, preserving spaces, escapes and punctuation')
            continue
        modified[name] = modified[name].replace(before, after, 1)
    if errors:
        raise ValueError('\n'.join(errors))
    if modified == originals:
        raise ValueError('Netlist edits made no change')
    return originals, modified


def prove(source, run_dir, edits, script, deadline):
    """No assumptions, black boxes, library edits or model-generated proof scripts."""
    source, run_dir = Path(source).resolve(), Path(run_dir).resolve()
    originals, modified = edited_netlists(source, edits)
    tops = re.findall(r'(?m)^\s*present_design\s+([\w$]+)\s*$', script)
    if len(tops) != 1:
        raise ValueError('EQY requires the actual single explicit present_design target')
    work = run_dir / 'equivalence'
    work.mkdir()
    gold = work / 'original.v'
    gate = run_dir / 'deliverables' / f'pre_scan_{run_dir.name}.v'
    gold.write_text('\n'.join(originals.values()), encoding='utf-8')
    gate.write_text('\n'.join(modified.values()), encoding='utf-8')
    diff = ''.join(''.join(difflib.unified_diff(originals[n].splitlines(True), modified[n].splitlines(True),
                    fromfile='input/'+n, tofile=gate.name+':'+n)) for n in originals)
    (work / 'netlist.diff').write_text(diff, encoding='utf-8')
    libs = sorted((source / 'lib').rglob('*.lib'))
    # -lib would turn cells into black boxes and cannot prove functionality.
    reads = '\n'.join(f'read_liberty -ignore_miss_func "{p.as_posix()}"' for p in libs)
    sections = []
    for kind, path in (('gold', gold), ('gate', gate)):
        # A repaired cell may keep its instance name but change its D pin's
        # meaning (enabled-FF -> mux + FF). Flattening that name creates false
        # EQY cutpoints such as u/D. Hide cell names, not nets or ports, before
        # flattening; shared functional wires remain candidates to prove.
        sections.append(f'[{kind}]\n{reads}\nread_verilog "{path.as_posix()}"\n'
                        f'rename -hide */c:*\nprep -top {tops[0]} -flatten\n'
                        # Purge unused alias names so EQY does not cut two
                        # names of one net into independent partition inputs.
                        'clk2fflogic\nopt_clean -purge\ncheck -assert\n')
    config = work / 'proof.eqy'
    config.write_text('\n'.join(sections) + '\n[strategy sat]\nuse sat\ndepth 10\n', encoding='utf-8')
    report = run_dir / 'reports/lec_report.rpt'
    process, code, timed_out = None, None, False
    # EQY creates thousands of small partition files. On a Windows bind mount
    # those writes dominated a measured 381 s proof (70 s on native scratch,
    # including archiving). Keep every proof file in one archive, and copy the
    # real status/log files to the existing evidence location. Proof rules and
    # the caller's deadline are unchanged.
    with tempfile.TemporaryDirectory(prefix='scan-eqy-') as scratch, report.open('wb') as stream:
        result_dir = Path(scratch) / 'result'
        try:
            process = subprocess.Popen(['eqy', '-d', str(result_dir), str(config)],
                cwd=work, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            code = process.wait(timeout=max(0.001, deadline-time.monotonic()))
        except (subprocess.TimeoutExpired, TimeoutError):
            timed_out = True
        finally:
            if process is not None and process.poll() is None:
                import os
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            if result_dir.exists():
                with tarfile.open(work / 'result.tar.gz', 'w:gz') as archive:
                    archive.add(result_dir, arcname='result')
                saved = work / 'result'
                saved.mkdir()
                for name in ('PASS', 'FAIL', 'UNKNOWN', 'logfile.txt'):
                    if (result_dir / name).is_file():
                        shutil.copyfile(result_dir / name, saved / name)
    status = 'PASS' if (work / 'result/PASS').exists() else 'NOT_PROVEN'
    result = {'tool': 'Yosys EQY', 'top': tops[0], 'original_sources': list(originals),
              'candidate': gate.relative_to(run_dir).as_posix(), 'returncode': code,
              'status': status, 'timed_out': timed_out, 'passed': code == 0 and status == 'PASS' and not timed_out,
              'proof_archive': 'equivalence/result.tar.gz',
              'scope': 'Original versus final-used pre-scan netlist; no added input assumptions.'}
    (run_dir / 'lec_result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result
