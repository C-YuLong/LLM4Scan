"""Run locally using authorized gproxy/Luna, official DeepSeek Flash, or no model."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tomllib


def main():
    parser = argparse.ArgumentParser()
    source_args = parser.add_mutually_exclusive_group()
    source_args.add_argument("--case", default="task_1/case4")
    source_args.add_argument("--input-dir", type=Path, help="Custom development input directory; mounted read-only")
    output_args = parser.add_mutually_exclusive_group(required=True)
    output_args.add_argument("--output", help="New directory name under dev/results")
    output_args.add_argument("--output-dir", type=Path, help="New explicit directory for an organized local benchmark run")
    runtime_args = parser.add_mutually_exclusive_group()
    runtime_args.add_argument("--image", help="Run a built submission image without mounting workspace source")
    runtime_args.add_argument('--runtime-image', default='localhost/scanscan:dev-runtime',
                              help='Dependency image used with mounted workspace source')
    parser.add_argument('--implementation', choices=('submission', 'ourmethod'), default='submission',
                        help='Select the workspace implementation to run')
    parser.add_argument('--verifier', choices=('legacy', 'direct_agent', 'guided_agent'),
                        help='Ourmethod verifier selection')
    parser.add_argument('--verification-mode', choices=('gate', 'shadow'),
                        help='Ourmethod verification mode')
    parser.add_argument('--provider', choices=('gproxy', 'deepseek'), default='gproxy',
                        help='gproxy/Luna or official DeepSeek Flash; credentials stay provider-specific')
    parser.add_argument('--competition-api', action='store_true',
                        help='Disabled: platform and DashScope credentials are not authorized')
    parser.add_argument("--initial-plan", type=Path, help="Development-only replay: seed first response from a saved model plan; subsequent Luna calls remain live")
    parser.add_argument("--tool-dofile", type=Path, help="Development fixture feasibility only: execute this oracle body without a model")
    parser.add_argument('--netlist-edits', type=Path, help='Fixture-only JSON with edits; requires --tool-dofile')
    args = parser.parse_args()
    if args.competition_api:
        parser.error('Platform and DashScope credentials will not be read or used.')
    if args.implementation != 'ourmethod' and (args.verifier or args.verification_mode):
        parser.error('--verifier and --verification-mode require --implementation ourmethod')
    if args.implementation == 'ourmethod' and (args.initial_plan or args.tool_dofile or args.netlist_edits):
        parser.error('The replay and fixture modes currently support submission only')
    if args.netlist_edits and not args.tool_dofile:
        parser.error('--netlist-edits is only allowed for no-model fixture qualification')
    root = Path(__file__).resolve().parents[1]
    # Credentials travel over stdin, never command arguments or a file.
    environment = {
        "SCANINSERTION_LICENSE_SERVER": "8273@121.40.209.111",
        "CASE_ID": args.input_dir.name if args.input_dir else args.case,
    }
    if not args.tool_dofile and args.provider == 'deepseek':
        environment.update(LLM_BASE_URL='https://api.deepseek.com',
                           LLM_API_KEY=os.environ['DEEPSEEK_API_KEY'],
                           LLM_MODEL='deepseek-flash', LLM_API_MODE='responses',
                           LLM_REASONING_EFFORT='none')
    elif not args.tool_dofile:
        config_path = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        provider = config['model_providers']['gproxy']
        if (provider['base_url'].rstrip('/') != 'https://gproxy.lin.pub/codex/v1'
                or provider['env_key'] != 'CODEX_KEY' or provider['wire_api'] != 'responses'):
            parser.error('gproxy configuration differs from the authorized URL, credential or protocol; no key was read.')
        environment.update(LLM_BASE_URL=provider['base_url'], LLM_API_KEY=os.environ['CODEX_KEY'],
                           LLM_MODEL='gpt-5.6-luna', LLM_API_MODE='responses')
    if args.output is not None and (Path(args.output).name != args.output or args.output in {".", ".."}):
        raise ValueError("output must be one new directory name")
    source = args.input_dir.resolve(strict=True) if args.input_dir else (root / "official/extracted/public_cases" / args.case / "input").resolve()
    if not source.is_dir() or (not args.input_dir and not source.is_relative_to(root / "official/extracted/public_cases")):
        raise ValueError("Invalid case")
    destination = args.output_dir.resolve() if args.output_dir else root / "dev/results" / args.output
    destination.mkdir(parents=True, exist_ok=False)
    def linux(path):
        return "/mnt/" + path.drive[0].lower() + path.as_posix()[2:]
    bootstrap = "import os,sys,json; os.environ.update(json.loads(sys.stdin.readline())); os.execv(sys.executable, [sys.executable, '/submission/agent_system'] + sys.argv[1:])"
    container_output = "/output" if args.output_dir else "/results/" + args.output
    command = [
        "wsl", "-d", "podman-machine-default", "-u", "user", "--", "podman", "run", "--rm", "-i",
        "-v", f"{linux(source)}:/input:ro",
        "-v", f"{linux(destination)}:{container_output}:rw",
        "--entrypoint", "python3", args.image or args.runtime_image,
        "-c", bootstrap, "-input", "/input", "-output", container_output,
    ]
    if args.verifier:
        command += ['--verifier', args.verifier]
    if args.verification_mode:
        command += ['--verification-mode', args.verification_mode]
    if not args.image:
        mount_index = command.index("--entrypoint")
        implementation = 'Ourmethod' if args.implementation == 'ourmethod' else 'submission'
        command[mount_index:mount_index] = ["-v", f"{linux(root / implementation)}:/submission:ro",
                                          '-v', f'{linux(root / "knowledge")}:/knowledge:ro']
    if args.initial_plan:
        saved = args.initial_plan.resolve(strict=True)
        replay_bootstrap = '''import os, sys, json
os.environ.update(json.loads(sys.stdin.readline()))
sys.path.insert(0, '/submission')
import scan_agent
from model_contract import TASK_CONSTRAINTS
original_generate = scan_agent.generate
first = True
def seeded_generate(*args, **kwargs):
    global first
    if first and kwargs.get('output_schema') != TASK_CONSTRAINTS:
        first = False
        with open('/initial_plan.json', encoding='utf-8') as stream:
            return json.load(stream)['plan'], {'development_initial_plan_replay': True}
    return original_generate(*args, **kwargs)
scan_agent.generate = seeded_generate
sys.argv = ['agent_system'] + sys.argv[1:]
raise SystemExit(scan_agent.main())
'''
        command[command.index(bootstrap)] = replay_bootstrap
        mount_index = command.index("--entrypoint")
        command[mount_index:mount_index] = ["-v", f"{linux(saved)}:/initial_plan.json:ro"]
    if args.tool_dofile:
        if args.initial_plan:
            parser.error("fixture execution cannot use a model replay")
        fixture_bootstrap = '''import os, sys, json
from pathlib import Path
os.environ.update(json.loads(sys.stdin.readline()))
sys.path.insert(0, '/submission')
from scan_runner import generated_script, run_scan, publish_run
from acceptance import inspect_run
from netlist_repair import repaired_script
target = Path(sys.argv[-1])
run = target / 'runs/R1'
edits = json.loads(Path('/fixture_edits.json').read_text())['edits'] if Path('/fixture_edits.json').exists() else []
body = Path('/fixture.dofile').read_text()
script = generated_script(Path('/input'), run, repaired_script(body, 'R1') if edits else body)
execution = run_scan(script, run, 480, netlist_edits=edits, source=Path('/input'))
acceptance, _, _ = inspect_run(run, script, execution, full_insertion=True, task_spec=Path('/input/task_spec.md'))
decision = {'fixture_validation': True, 'final_run': 'R1' if acceptance['accepted'] else None,
            'summary': 'Fixture feasibility only: oracle body supplied, no model.',
            'issue_resolutions': [], 'file_changes': []}
if acceptance['accepted']:
    publish_run(target, run)
(target / 'decision_log.json').write_text(json.dumps(decision, indent=2))
print(json.dumps({'fixture_validation': True, 'accepted': acceptance['accepted']}))
raise SystemExit(0 if acceptance['accepted'] else 1)
'''
        command[command.index(bootstrap)] = fixture_bootstrap
        mount_index = command.index("--entrypoint")
        command[mount_index:mount_index] = ["-v", f"{linux(args.tool_dofile.resolve(strict=True))}:/fixture.dofile:ro"]
        if args.netlist_edits:
            command[mount_index:mount_index] = ['-v', f'{linux(args.netlist_edits.resolve(strict=True))}:/fixture_edits.json:ro']
    return subprocess.run(command, input=json.dumps(environment).encode()).returncode


if __name__ == "__main__":
    raise SystemExit(main())
