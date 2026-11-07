"""Verify saved candidates without Scan or modifications to their original directories."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from configuration import load_config
from llm_client import ModelClient
from verifier.direct_agent import verify
import telemetry


def environment(path=None):
    env=dict(os.environ)
    if path:
        for line in Path(path).read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key,value=line.split("=",1)
                env[key.strip()]=value.strip().strip('"').strip("'")
    return env


def run_candidates(source, output, destination, config, client, run=None, all_runs=False):
    if config.verifier["backend"]=="guided_agent":
        from verifier.guided_agent import verify as review
    else:review=verify
    candidates=sorted((output/"runs").glob("R*"),key=lambda p:int(p.name[1:]) if p.name[1:].isdigit() else -1)
    candidates=[p for p in candidates if p.is_dir() and re.fullmatch(r"R\d+",p.name)]
    if run:
        candidates=[p for p in candidates if p.name==run]
    elif not all_runs:
        candidates=candidates[-1:]
    if not candidates: raise ValueError("No matching saved candidates")
    cache={}; rows=[]
    for candidate in candidates:
        target=destination/candidate.name
        script_path=candidate/"deliverables"/(candidate.name+".dofile")
        execution=json.loads((candidate/"execution.json").read_text())
        result=review(source,candidate,script_path.read_text(),execution,target,config.verifier,client,design_cache=cache)
        rows.append(dict(run_id=candidate.name,status=result["status"],summary=result["summary"],metrics=result["metrics"],
                         report=str(target/"result.json")))
        print(json.dumps(rows[-1],ensure_ascii=False),flush=True)
    return rows


def docker_run(args,config,env):
    target=args.result_dir.resolve();target.mkdir(parents=True,exist_ok=False)
    command=["docker","--context",args.context,"run","--rm","-i","--platform","linux/amd64",
             "--mount",f"type=bind,source={target},target=/verification", "--entrypoint","python3"]
    worker=["--worker","--result-dir","/verification","--verifier",args.verifier]
    if args.batch:
        command += ["--mount",f"type=bind,source={args.batch.resolve()},target=/candidates,readonly",
                    "--mount",f"type=bind,source={args.input_root.resolve()},target=/inputs,readonly"]
        worker += ["--batch","/candidates","--input-root","/inputs"]
    else:
        command += ["--mount",f"type=bind,source={args.input.resolve()},target=/input,readonly",
                    "--mount",f"type=bind,source={args.output.resolve()},target=/candidate,readonly"]
        worker += ["--input","/input","--output","/candidate"]
    if args.config:
        command += ["--mount",f"type=bind,source={args.config.resolve()},target=/agent-config.toml,readonly"]
        worker += ["--config","/agent-config.toml"]
    if args.run: worker += ["--run",args.run]
    if args.all_runs: worker += ["--all-runs"]
    bootstrap="import os,sys,json;os.environ.update(json.loads(sys.stdin.readline()));os.execv(sys.executable,[sys.executable,'/submission/offline_verify.py']+sys.argv[1:])"
    command += [args.image,"-c",bootstrap,*worker]
    # Only selected model env references, not the entire host environment.
    profile=config.value["models"][config.verifier["model_profile"]]
    forwarded={profile[key]:env[profile[key]] for key in ("model_env","base_url_env","api_key_env") if key in profile}
    process=subprocess.run(command,input=json.dumps(forwarded)+"\n",text=True)
    return process.returncode


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output",type=Path,help="Existing case output (runs/Rn)")
    group.add_argument("--batch",type=Path,help="Existing benchmark directory containing cases/")
    parser.add_argument("--input",type=Path)
    parser.add_argument("--input-root",type=Path,default=Path(__file__).resolve().parents[1]/"official/extracted/public_cases")
    parser.add_argument("--result-dir",type=Path,required=True,help="New directory; historical results are never overwritten")
    parser.add_argument("--run",help="One saved run, e.g. R3")
    parser.add_argument("--all-runs",action="store_true")
    parser.add_argument("--config",type=Path)
    parser.add_argument("--verifier",default=None)
    parser.add_argument("--env-file",type=Path)
    parser.add_argument("--docker",action="store_true",help="Use the independent Ourmethod image and its Yosys")
    parser.add_argument("--image",default="scanscan:ourmethod")
    parser.add_argument("--context",default="colima-llm4scan")
    parser.add_argument("--worker",action="store_true",help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args.output and (not args.input or not (args.input/"task_spec.md").is_file()): parser.error("--output requires --input containing task_spec.md")
    if args.run and (args.all_runs or not re.fullmatch(r"R\d+",args.run)): parser.error("Use --run Rn or --all-runs")
    env=environment(args.env_file)
    config=load_config(args.config,verifier=args.verifier or ("direct_agent" if not args.config else None),verification_mode="gate",environ=env,model_roles=("verifier",))
    if config.verifier["backend"] not in {"direct_agent","guided_agent"}: parser.error("Offline review requires a model verifier backend")
    args.verifier=config.pipeline["verifier"]
    target=args.result_dir.resolve()
    # Prevent either self-observation or accidental writes into historical/input trees.
    for original in [p.resolve() for p in (args.output,args.batch,args.input,args.input_root if args.batch else None) if p]:
        if target.is_relative_to(original): parser.error("--result-dir must be outside historical/input directories")
    if args.docker:
        return docker_run(args,config,env)
    target.mkdir(parents=True,exist_ok=args.worker)
    if any(target.iterdir()): raise FileExistsError("Result directory must be empty")
    (target/"config_snapshot.json").write_text(json.dumps(config.snapshot(),indent=2)+"\n")
    telemetry.start(target)
    started=time.monotonic();rows=[];code=2
    try:
        client=ModelClient(config.model(config.verifier["model_profile"]))
        if args.batch:
            for case in sorted((args.batch/"cases").iterdir()):
                match=re.fullmatch(r"task_1_(case\d+)",case.name)
                if not match: continue
                source=args.input_root/"task_1"/match[1]/"input"
                items=run_candidates(source,case,target/case.name,config,client,args.run,args.all_runs)
                rows.extend(dict(item,case=case.name) for item in items)
        else:
            if (args.input/"original.dofile").exists(): parser.error("Model verifiers support Task1 only")
            rows=run_candidates(args.input,args.output,target,config,client,args.run,args.all_runs)
        summary=dict(rows=rows,elapsed_seconds=round(time.monotonic()-started,3),
                     counts={status:sum(r["status"]==status for r in rows) for status in ("PASS","FAIL","UNKNOWN")})
        (target/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n")
        lines=["# Saved-candidate verification","","| Case | Run | Status | Calls | Tokens | Seconds |","|---|---|---|---:|---:|---:|"]
        lines += [f"| {r.get('case','')} | {r['run_id']} | {r['status']} | {r['metrics']['model_calls']} | {r['metrics']['total_tokens']} | {r['metrics']['elapsed_seconds']} |" for r in rows]
        (target/"report.md").write_text("\n".join(lines)+"\n")
        code=1 if any(r["status"]=="FAIL" for r in rows) else 2 if not rows or any(r["status"]=="UNKNOWN" for r in rows) else 0
        return code
    finally:
        telemetry.finish(code)


if __name__=="__main__":
    raise SystemExit(main())
