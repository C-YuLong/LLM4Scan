"""Run the local Luna development milestone in the official Linux image."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tomllib


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", default="task_1/case4")
    parser.add_argument("--output", required=True, help="New directory name under dev/results")
    parser.add_argument("--image", help="Run a built submission image without mounting workspace source")
    parser.add_argument("--initial-plan", type=Path, help="Development-only replay: seed first response from a saved model plan; subsequent Luna calls remain live")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config_path = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    provider = config["model_providers"][config["model_provider"]]
    # Credentials travel over stdin, never command arguments or a file.
    environment = {
        "LLM_BASE_URL": provider["base_url"],
        "LLM_API_KEY": os.environ[provider["env_key"]],
        "LLM_MODEL": "gpt-5.6-luna",
        "SCANINSERTION_LICENSE_SERVER": "8273@121.40.209.111",
        "CASE_ID": args.case,
    }
    if Path(args.output).name != args.output or args.output in {".", ".."}:
        raise ValueError("output must be one new directory name")
    source = (root / "official/extracted/public_cases" / args.case / "input").resolve()
    if not source.is_relative_to(root / "official/extracted/public_cases") or not source.is_dir():
        raise ValueError("Invalid case")
    def linux(path):
        return "/mnt/" + path.drive[0].lower() + path.as_posix()[2:]
    bootstrap = "import os,sys,json; os.environ.update(json.loads(sys.stdin.readline())); os.execv(sys.executable, [sys.executable, '/submission/agent_system'] + sys.argv[1:])"
    command = [
        "wsl", "-d", "podman-machine-default", "-u", "user", "--", "podman", "run", "--rm", "-i",
        "-v", f"{linux(source)}:/input:ro",
        "-v", f"{linux(root / 'dev/results')}:/results:rw",
        "--entrypoint", "python3", args.image or "docker.io/library/scan-agent-base:ubuntu24",
        "-c", bootstrap, "-input", "/input", "-output", "/results/" + args.output,
    ]
    if not args.image:
        mount_index = command.index("--entrypoint")
        command[mount_index:mount_index] = ["-v", f"{linux(root / 'submission')}:/submission:ro"]
    if args.initial_plan:
        saved = args.initial_plan.resolve(strict=True)
        replay_bootstrap = '''import os, sys, json
os.environ.update(json.loads(sys.stdin.readline()))
sys.path.insert(0, '/submission')
import scan_agent
original_generate = scan_agent.generate
first = True
def seeded_generate(*args, **kwargs):
    global first
    if first:
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
    return subprocess.run(command, input=json.dumps(environment).encode()).returncode


if __name__ == "__main__":
    raise SystemExit(main())
