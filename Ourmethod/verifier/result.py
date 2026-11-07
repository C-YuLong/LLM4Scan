"""Local final-answer validation; model claims never replace program evidence."""
import json
from copy import deepcopy
from pathlib import Path

from .tool_schema import FINAL_SCHEMA, validate_schema




def parse_final(text):
    """Accept a single closed final object, including a provider's fenced answer.

    Some compatible gateways ignore json_schema and prepend commentary. Never
    accept a partial object, two competing decisions, or trailing prose.
    """
    decoder=json.JSONDecoder(); candidates=[]
    for position,char in enumerate(text):
        if char!="{":continue
        try:value,used=decoder.raw_decode(text[position:])
        except json.JSONDecodeError:continue
        if isinstance(value,dict) and set(value)==set(FINAL_SCHEMA["properties"]):
            candidates.append((value,text[position+used:].strip()))
    if len(candidates)!=1 or candidates[0][1] not in {"","```"}:
        raise ValueError("Expected one complete final verification object")
    return candidates[0][0]


def check_counterexample(witness, item, tools):
    """Validate one positive membership witness, without claiming task semantics."""
    subject=witness["instance"]
    for key in ("original_evidence_id", "actual_evidence_id"):
        if witness[key] not in item["evidence_ids"]:
            raise ValueError(key+": must also be referenced in item.evidence_ids")
        if witness[key] not in tools.records:
            raise ValueError(key+": missing or stale evidence")
    original=tools.records[witness["original_evidence_id"]]
    actual=tools.records[witness["actual_evidence_id"]]
    if (original.get("status")!="FACT" or original.get("operation")!="select"
        or original.get("population_origin")!="original_design"
        or not original.get("artifact", "").startswith("input/")
        or Path(original.get("artifact", "")).suffix not in {".v", ".sv"}):
        raise ValueError("original_evidence_id: expected original netlist object selection")
    if (actual.get("status")!="FACT" or actual.get("operation")!="select"
        or actual.get("population_origin")!="candidate_report" or actual.get("table")!="members"
        or not actual.get("artifact", "").startswith("run/")):
        raise ValueError("actual_evidence_id: expected current-run actual chain members selection")
    if original.get("artifact") not in tools.artifacts or actual.get("artifact") not in tools.artifacts:
        raise ValueError("evidence artifact is not registered for this candidate")
    original_rows=[row for row in original.get("rows",[]) if row.get("path")==subject and row.get("type")]
    actual_rows=[row for row in actual.get("rows",[]) if row.get("instance")==subject and row.get("chain")
                 and row.get("_source")==actual["artifact"] and type(row.get("_line")) is int and row["_line"]>0]
    if subject not in original.get("values",[]) or not original_rows:
        raise ValueError("instance: not an identified object in original evidence")
    if subject not in actual.get("values",[]) or not actual_rows:
        raise ValueError("instance: not an identified actual chain member in evidence")
    if not tools._filter([subject],witness["include"],witness["exclude"]):
        raise ValueError("instance: outside included scope or matches an allowed exclusion")
    return dict(**witness, task_ref=item["task_ref"],requirement=item["requirement"],message=item["message"],
                proof_basis="counterexample", actual_artifact=actual["artifact"],
                original_artifact=original["artifact"],chain=actual_rows[0]["chain"],line=actual_rows[0]["_line"])


def usable_evidence(record):
    """Validate the tool response, not its source file category or task meaning."""
    return not record.get('error_type') and record.get('status') in {'FACT','PASS','FAIL'}


def finalize(value, tools):
    validate_schema(value,FINAL_SCHEMA)
    value=deepcopy(value)
    tools.current()
    issues=list(value["unverified"])
    all_sources={key:tools.read(key).splitlines() for key in tools.artifacts
                 if key.startswith("input/") and key.endswith(".md")}
    def reference(ref):
        source=ref["source"]; lines=all_sources.get(source)
        if lines is None or not 1<=ref["line_start"]<=ref["line_end"]<=len(lines):
            raise ValueError("Invalid task source reference")
    statuses=[]; checked_counterexamples=[]; counterexample_errors=[]; interpreted_checks=[]
    for item_index,item in enumerate(value["items"]):
        reference(item["task_ref"])
        witnesses=[]
        if item["status"]=="FAIL" and item["kind"] in {"structural","report"}:
            for witness_index,witness in enumerate(item.get("counterexamples") or []):
                try:
                    if not item["message"].strip():raise ValueError("message: explain the violated requirement")
                    witnesses.append(check_counterexample(witness,item,tools))
                except ValueError as exc:
                    counterexample_errors.append(f"$.items[{item_index}].counterexamples[{witness_index}]: {exc}")
        evidence=[tools.records[key] for key in item["evidence_ids"] if key in tools.records]
        if len(evidence)!=len(item["evidence_ids"]) or not evidence:
            issues.append("Missing or stale evidence for: "+item["requirement"])
            item["validated_status"]="UNKNOWN"
            statuses.append("UNKNOWN"); continue
        status=item["status"]
        proof=[r for r in evidence if r.get("proof") and r.get("complete")]
        if item["judgment_basis"]=="model":
            for review in item.get("inapplicable_checks") or []:
                key=review["evidence_id"];record=tools.records.get(key)
                if key not in item["evidence_ids"] or not record or not record.get("proof") or record.get("status")!="FAIL" or not review["reason"].strip():
                    issues.append("Invalid inapplicable check reference: "+key);continue
                interpreted_checks.append(dict(evidence_id=key,reason=review["reason"],task_ref=item["task_ref"],judgment_basis="model"))
            if status in {"PASS","FAIL"} and (not any(usable_evidence(r) for r in evidence) or not item["message"].strip()):
                issues.append("Model interpretation lacks successful cited evidence or explanation: "+item["requirement"]);status="UNKNOWN"
            elif status in {"PASS","FAIL"}:item["proof_basis"]="model_interpretation"
        else:
            if status=="FAIL" and witnesses:
                item["proof_basis"]="counterexample"
                checked_counterexamples.extend(witnesses)
            elif status in {"PASS","FAIL"} and not any(r["status"]==status for r in proof):
                issues.append("No complete program proof or validated counterexample for: "+item["requirement"]);status="UNKNOWN"
            elif status in {"PASS","FAIL"}:
                item["proof_basis"]="complete_program_check"
        if status=="UNKNOWN": issues.append("Unverified requirement: "+item["requirement"])
        item["validated_status"]=status
        statuses.append(status)
    for entry in value["context"]:
        reference(entry["task_ref"])
    if not value["reviewed_task"] or not value["items"]: issues.append("Final full-task review is incomplete")
    failures=[key for key,r in tools.records.items() if r.get("proof") and r.get("complete") and r.get("status")=="FAIL"]
    # A failed predicate is not necessarily a violated task clause: a model may
    # have chosen the wrong stage/scope. A model may explicitly source-link an
    # applicability disagreement, but the original failed predicate is retained.
    # Unresolved disagreement stays UNKNOWN. Confirmed FAIL must
    # be tied to a source-linked item and cited evidence.
    confirmed=[key for item in value["items"] if item.get("validated_status")=="FAIL"
               for key in item["evidence_ids"] if key in failures]
    reviewed={r["evidence_id"] for r in interpreted_checks}
    unresolved=[key for key in failures if key not in confirmed and key not in reviewed]
    if unresolved:
        issues.append("Program checks failed but their applicability to the task is unresolved")
    status="FAIL" if "FAIL" in statuses else "UNKNOWN" if issues or not statuses else "PASS"
    if value["status"]=="UNKNOWN" and status in {"PASS","FAIL"}:
        status="UNKNOWN";issues.append("Verifier explicitly reported UNKNOWN")
    if value["status"]=="FAIL" and status=="PASS":
        status="UNKNOWN";issues.append("FAIL claim has no supported violation")
    issues.extend(counterexample_errors)
    return dict(interpreted_program_failures=interpreted_checks,checked_counterexamples=checked_counterexamples,counterexample_errors=counterexample_errors,
                status=status, summary=value["summary"], items=value["items"],
                unverified=list(dict.fromkeys(issues)),
                forced_program_failures=list(dict.fromkeys(confirmed)),
                unresolved_program_failures=unresolved,
                reviewed_task=value["reviewed_task"],
                scope="Program checks plus source-linked model interpretation; no complete functional equivalence proof")
