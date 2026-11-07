"""Small component registry; current generation and legacy verification stay intact."""
import json
from pathlib import Path

from configuration import load_config
from llm_client import ModelClient
from scan_runner import declared_artifacts


class Runtime:
    def __init__(self, config, source=None):
        self.config=config
        self.generator=ModelClient(config.model(config.generator["model_profile"])).generate
        self.verifier_client=(ModelClient(config.model(config.verifier["model_profile"]))
                              if config.verifier["backend"] in {"direct_agent","guided_agent"} else None)
        self.design_cache={}
        self.input_identity={}
        if source is not None:
            from verifier.tools import digest
            self.input_identity={p.resolve():digest(p) for p in Path(source).rglob("*") if p.is_file() and p.suffix in {".v",".sv",".lib",".ctl",".md"}}

    def inspect(self, legacy, source, run_dir, script, execution, deadline, **kwargs):
        acceptance,artifacts,workflow=legacy(run_dir,script,execution,**kwargs)
        if self.verifier_client is None:
            return acceptance,artifacts,workflow
        if self.config.verifier["backend"]=="guided_agent":
            from verifier.guided_agent import verify
        else:
            from verifier.direct_agent import verify
        from verifier.tools import encoded
        directory=Path(run_dir)/"verification"
        # Reuse legacy's artifact gathering, not its full task-specific verdict.
        missing=[]
        for name in declared_artifacts(script):
            path=(Path(run_dir)/name).resolve()
            if not path.is_relative_to(Path(run_dir).resolve()) or not path.is_file() or path.stat().st_size==0:
                missing.append(name)
        from verifier.tools import digest
        changed=[str(path) for path,checksum in self.input_identity.items() if not path.is_file() or digest(path)!=checksum]
        if missing or changed:
            directory.mkdir(exist_ok=False)
            result=dict(status="FAIL",summary="Input changed" if changed else "Declared outputs are missing or empty",missing_outputs=missing,
                        items=[],unverified=[],run_id=Path(run_dir).name,backend=self.config.verifier["backend"])
            (directory/"result.json").write_text(encoded(result)+"\n")
        else:
            result=verify(source,run_dir,script,execution,directory,self.config.verifier,self.verifier_client,
                          deadline=deadline,design_cache=self.design_cache)
        if self.config.pipeline["verification_mode"]=="shadow":
            return acceptance,artifacts,workflow
        feedback={k:result.get(k) for k in ("status","summary","items","unverified","forced_program_failures","unresolved_program_failures","interpreted_program_failures","missing_outputs") if k in result}
        counterexamples=[]
        for key in result.get("forced_program_failures",[]):
            from verifier.diagnostics import evidence_path
            path=evidence_path(directory,key)
            if path.is_file():
                check=json.loads(path.read_text())
                counterexamples.append(dict(evidence_id=key,operation=check.get("operation"),
                                            observed=check.get("observed"),violations=check.get("violations",check.get("rows",[]))[:5]))
        counterexamples.extend(result.get("checked_counterexamples",[]))
        if counterexamples: feedback["counterexamples"]=counterexamples
        chosen=dict(accepted=result["status"]=="PASS",checks={self.config.verifier["backend"]+"_verified":result["status"]=="PASS"},
                    missing_outputs=missing,verification_status=result["status"],check_details=feedback,
                    scope="Direct task verification",_evidence_source="verification/acceptance.json")
        (directory/"acceptance.json").write_text(encoded(chosen)+"\n",encoding="utf-8")
        return chosen,artifacts,workflow

    def check_publication(self, target, run_dir):
        if self.verifier_client is None or self.config.pipeline["verification_mode"]!="gate":
            return True
        from verifier.tools import digest,encoded
        target,run_dir=Path(target),Path(run_dir)
        final=target/"final_results"; expected={}
        for group in ("deliverables","reports"):
            for path in (run_dir/group).rglob("*"):
                if path.is_file():
                    name=path.relative_to(run_dir).as_posix()
                    if name==f"deliverables/{run_dir.name}.dofile":name="deliverables/final.dofile"
                    expected[name]=path
        expected["final.log"]=run_dir/(run_dir.name+".log")
        checks=dict(file_set=set(expected)=={p.relative_to(final).as_posix() for p in final.rglob("*") if p.is_file()},
                    bytes_match=all((final/key).is_file() and digest(final/key)==digest(path) for key,path in expected.items()),
                    decision_matches=json.loads((target/"decision_log.json").read_text()).get("final_run")==run_dir.name)
        (run_dir/"verification/publication.json").write_text(encoded(checks)+"\n")
        return all(checks.values())


def from_args(args, source):
    selections={key:getattr(args,key,None) for key in ("generator","verifier","verification_mode")}
    config_path=getattr(args,"config",None)
    if config_path is None and not any(v is not None for v in selections.values()):
        return None
    config=load_config(config_path,**selections)
    if (Path(source)/"original.dofile").exists() and config.verifier["backend"]!="legacy":
        raise ValueError("Model verifiers currently support Task1 only")
    return Runtime(config,source)
