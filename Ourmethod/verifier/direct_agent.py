"""Direct task reading and on-demand evidence, without an upfront task contract."""
import json
from pathlib import Path
import time

from .tools import EvidenceTools, encoded
from .context import ContextProjection, byte_size
from .result import FINAL_SCHEMA, finalize, parse_final
from .tool_schema import ToolArgumentError, validate_schema
from .gaps import annotate
from .model_tools import ModelTools, GUIDE as MODEL_GUIDE, DYNAMIC_GUIDE


LEGACY_INSTRUCTIONS = """You are the independent verifier for a Scan task. Determine whether the current
candidate's actual artifacts satisfy the complete original task. Use read-only
evidence tools; do not generate or repair scripts, run Scan, or access GT.

The input contains the complete task, candidate script, execution summary, and
available artifacts. The original task and its clarifications define acceptance.
The candidate script and author statements describe intent, not proof of success.

Follow these steps:

1. Identify acceptance conditions.
Identify every requirement, including its object scope, exceptions, and expected
result. Focus on final results; also verify intermediate artifacts explicitly
required by the task. Do not introduce restrictions absent from the task.
Record them with update_checklist as short requirement groups (maximum 16).
Only record a short label, task source range, progress state, up to 4 evidence
IDs and a short missing-proof note. Do not copy the task or list design objects.
Update progress when starting or resolving a requirement, preferably alongside
evidence calls in the same response. evidenced means evidence collected, not PASS.
The controller supplies only the latest checklist snapshot each turn; it does
not replace the original task or the final evidence/coverage checks.

2. Gather evidence.
Choose suitable artifacts and tools for the acceptance conditions. Before calling
a tool, confirm that it supports the intended check and that its parameters
accurately express the required scope, exceptions, and expected result. Use the artifact manifest's purposes and query hints to choose evidence sources;
these hints do not replace tool descriptions. Follow the tool descriptions. Prefer complete checks that directly test a condition;
read additional details when needed to resolve uncertainty.

3. Evaluate the requirements.
Interpret cited observations against each requirement; script commands can establish configuration but do not alone establish execution effects. Claims about all
objects or the absence of violations require evidence covering the full scope.
A tool result applies only to the conditions it checked. Confirm that those
conditions match the task before treating the result as evidence of task success
or failure. If evidence is insufficient, a check is inapplicable, or the required
proof is unsupported, mark the requirement UNKNOWN. Do not disregard unresolved
check failures to obtain PASS.

4. Review and submit.
Review the identified requirements against the original task and submit their
supported verdicts or explicit evidence gaps. You do not need to classify every
source line, heading, table row, or background passage. The context array may be
empty. Call finish_verification using its schema, with valid original task
references, evidence IDs, and concise explanations. Set reviewed_task to true
only after reviewing the submitted requirements.

Decision rules:
- FAIL: evidence confirms that at least one original task requirement is violated.
  For prohibited chain membership, you may submit a concrete counterexample in
  the item's counterexamples: instance, original_evidence_id, actual_evidence_id,
  include (prohibited scope), exclude (allowed exceptions). Reference both IDs in
  evidence_ids. Use original netlist and actual members select queries; the
  controller checks the saved records. A full-set comparison is not required to
  prove one prohibited member. Other failure types still need supported proof.
- PASS: sufficient evidence supports every requirement, with no unresolved check
  conflicts or unverified requirements.
- UNKNOWN: no violation is confirmed, but some requirements remain unproved.

After confirming a violation, you may end evidence gathering and mark remaining
unchecked requirements UNKNOWN. When the budget is insufficient, submit the
available evidence and unverified requirements rather than guessing.
"""


INSTRUCTIONS = """You independently verify a Scan candidate against the complete original task.
Use read-only tools; do not generate/repair scripts, run Scan or access GT.
The task and clarifications define acceptance. Script commands can support configuration requirements; execution/result requirements need observations that establish those effects. Explain sufficiency against the original requirement without a universal file-category rule. Do not add requirements or invent exceptions.

1. Identify the task rules, scopes, exceptions and expected results. Track short
requirement groups with update_checklist. evidenced is progress, not a verdict.
2. Query concrete facts or test explicit predicates using the tool schemas.
Use stage_artifacts to select before/after snapshots; respect uncertain associations and intervening operations. Stage labels describe intent, not correctness.
Read the relevant classified result section and use its next_read to recover omitted objects; group previews do not establish full proof.
For long text follow next_read; prefer log_events/log_members for supported
replacement lists instead of reading all instance names as text.
For FF type/replacement/unchanged populations, prefer query_ff_changes.
It observes changes and connections; it does not decide whether exceptions are
allowed. check_ff_mapping is a stricter explicit-mapping predicate, not task semantics.
3. Interpret facts against the original task. Follow gap_category/next_step:
correct invalid_request; query evidence_missing; decide needs_interpretation
from task and facts; record capability_unsupported or execution_error rather
than repeatedly querying unrelated counts. Never invent unavailable facts or
claim functional equivalence from counts or unresolved connections.
4. Call finish_verification with source refs, relevant cited evidence and reasons.
judgment_basis=program uses complete predicate proof or a supported concrete
counterexample. judgment_basis=model explains the task rule, actual observation
and why it satisfies/violates that rule; the controller validates citations,
not a second complete semantic proof. For inapplicable program FAILs list each
ID and source-linked reason in inapplicable_checks; never silently ignore them.
The failed predicate stays recorded. Use null for unused counterexamples and
inapplicable_checks. context may be empty; no source-line classification is needed.

FAIL requires a supported violation; PASS requires a supported decision on each
submitted requirement and no unexplained conflicts; otherwise UNKNOWN. Preserve
uncertainty rather than guess. Set reviewed_task after reviewing submitted items.
You may finish after a confirmed violation, leaving others UNKNOWN.

The latest checklist/evidence table replace old history; only the last three
complete response/tool rounds remain verbatim. Summaries are observations, not
new judgments. Recover omitted scope/details with list_evidence/read_evidence.
"""


INSTRUCTIONS += '\n' + MODEL_GUIDE

def verify(source, run_dir, script, execution, output_dir, settings, client, *, deadline=None, design_cache=None):
    started=time.monotonic()
    deadline=min(deadline if deadline is not None else float("inf"),started+settings["timeout_seconds"])
    output_dir=Path(output_dir)
    output_dir.mkdir(parents=True,exist_ok=True)
    tools=EvidenceTools(source,Path(run_dir),script,output_dir/"evidence",settings,deadline,design_cache=design_cache)
    api=ModelTools(tools)
    mode=settings.get("context_mode","compact")
    token_limit=settings['max_total_tokens']
    instructions=INSTRUCTIONS+('\n'+DYNAMIC_GUIDE if api.dynamic else '')
    if api.dynamic:instructions=instructions.replace('read_evidence_section uses a returned section.','use the returned section_reads tool for a classified section.')
    final_definition=dict(type="function",name="finish_verification",strict=True,
                                       description="Submit source-linked decisions: program basis requires matching program proof; model basis explains task rules against cited observations with valid citations. Explicitly list any inapplicable program FAIL IDs and reasons; no silent override. Unsupported facts remain UNKNOWN.",parameters=FINAL_SCHEMA)
    task={key:"\n".join(f"{n}: {line}" for n,line in enumerate(tools.read(key).splitlines(),1))
          for key in tools.artifacts if key.startswith("input/") and key.endswith(".md")}
    initial=dict(task_sources=task,run_id=Path(run_dir).name,execution=execution,
                 artifacts=tools.model_artifact_manifest(),stage_artifacts=tools.stage_artifacts,
                 candidate_script=dict(artifact="candidate.dofile",text=script if len(script)<=20000 else "Use read_lines to inspect this large script"))
    messages=[dict(role="user",content=encoded(initial))]
    context=ContextProjection(messages[0],mode)
    context_calls=[]
    metrics=dict(model_calls=0,tool_calls=0,total_tokens=0,usage_complete=True,usage=[])
    invalid_rounds=0  # At most two correction responses after an invalid call.
    metrics["argument_retry_rounds"]=0
    result=None; transcript=[]; token_ratio=1.0; seen_tool_outputs=set(); termination=None
    try:
        # Fatal execution failures need no expensive model diagnosis.
        if execution.get("exit_status")!="completed":
            result=dict(status="FAIL",summary="Scan execution did not complete",items=[],unverified=[],execution=execution)
        while result is None:
            definitions=api.definitions+[final_definition]
            remaining=deadline-time.monotonic()
            if remaining<=0 or metrics["model_calls"]>=settings["max_model_turns"]:
                termination="time_budget" if remaining<=0 else "model_call_budget"
                raise TimeoutError("Verify time/model-call budget exhausted")
            request_messages,archive_prompt,component_sizes=context.build(messages,tools)
            checklist_prompt=tools.checklist.prompt()
            system_prompt=instructions+checklist_prompt+archive_prompt if mode=="legacy" else instructions
            request_bytes=byte_size(request_messages)+len((instructions+checklist_prompt+archive_prompt).encode())+byte_size(definitions)+byte_size(FINAL_SCHEMA)
            estimate=int(request_bytes*token_ratio)
            available=token_limit-metrics["total_tokens"]-estimate if token_limit else None
            if token_limit and available<=0:
                termination="token_admission_budget"
                raise TimeoutError("Verify token admission budget exhausted")
            max_output=min(settings["max_output_tokens_per_call"],available) if token_limit else settings['max_output_tokens_per_call']
            final_only=(metrics["model_calls"]+1>=settings["max_model_turns"]
                        or remaining<25 or metrics["tool_calls"]>=settings["max_tool_calls"]
                        or (token_limit and metrics["model_calls"]>0 and available<estimate+2*settings["max_output_tokens_per_call"])
                        or any(r.get("proof") and r.get("complete") and r.get("status")=="FAIL" for r in tools.records.values()))
            budget=f'\nRemaining budget: {remaining:.1f}s, {settings["max_model_turns"]-metrics["model_calls"]} model calls, {settings["max_tool_calls"]-metrics["tool_calls"]} tool calls. '
            budget+=(f'{token_limit-metrics["total_tokens"]} total tokens remaining. ' if token_limit else 'Total token budget disabled; usage is still recorded. ')
            if invalid_rounds:
                budget += "Correct the invalid tool arguments using the field error and allowed values in the tool output. "
                budget += f"At most {3-invalid_rounds} correction response(s) remain; do not invent operations. "
            if final_only:budget+='This is the final response. Call finish_verification now with supported FAIL or PASS, otherwise UNKNOWN and unverified requirements. No more evidence queries.'
            if mode=="compact":
                state=dict(role="user",content="Current controller state:\n"+checklist_prompt+archive_prompt+budget)
                request_messages=[request_messages[0],state,*request_messages[1:]]
                call_instructions=instructions
            else:
                call_instructions=system_prompt+budget
            metrics["model_calls"]+=1
            context_call=dict(call=metrics["model_calls"],mode=mode,
                              bytes=component_sizes | dict(instructions=len(instructions.encode()),
                                task_sources=byte_size(initial["task_sources"]),candidate_script=byte_size(initial["candidate_script"]),
                                execution=byte_size(initial["execution"]),artifacts=byte_size(initial["artifacts"]),
                                checklist=len(checklist_prompt.encode()),tools=byte_size(definitions)),
                              input_tokens=None,output_tokens=None,cached_input_tokens=None,usage_complete=False)
            context_calls.append(context_call)
            try:
                choice=dict(type="function",name="finish_verification") if final_only else "auto"
                response=client.respond(request_messages,definitions,call_instructions,remaining,max_output,FINAL_SCHEMA,choice)
            except Exception:
                metrics["usage_complete"]=False
                raise
            context.mark_seen(request_messages)
            usage=response.get("usage") or {}
            context_call.update(input_tokens=usage.get("input_tokens"),output_tokens=usage.get("output_tokens"),
                                cached_input_tokens=(usage.get("input_tokens_details") or {}).get("cached_tokens"),
                                usage_complete=type(usage.get("total_tokens")) is int)
            metrics["usage"].append(usage)
            count=usage.get("total_tokens")
            if type(count) is not int:
                count=estimate+max_output;metrics["usage_complete"]=False
            if type(usage.get("input_tokens")) is int:
                token_ratio=min(1.0,max(0.3,usage["input_tokens"]/max(1,request_bytes)*1.2))
            metrics["total_tokens"]+=count
            output=response.get("output",[])
            transcript.append(dict(response={k:v for k,v in response.items() if k!="output"},
                                   output=[item for item in output if item.get("type")!="reasoning"]))
            if token_limit and metrics["total_tokens"]>token_limit:
                termination="token_usage_budget"
                raise TimeoutError("Verify token budget exceeded")
            # Preserve response reasoning/tool items for stateless native continuation;
            # private reasoning is not written to local transcripts.
            context.start_turn(messages)
            messages.extend(output)
            calls=[item for item in output if item.get("type")=="function_call"]
            finishes=[call for call in calls if call.get("name")=="finish_verification"]
            if finishes:
                if len(calls)!=1:raise ValueError("Final decision cannot be mixed with unexecuted evidence queries")
                try:
                    value=json.loads(finishes[0]["arguments"])
                    validate_schema(value,FINAL_SCHEMA)
                except (ValueError,TypeError) as exc:
                    answer=dict(status="UNKNOWN",error_type="ToolArgumentError",message=str(exc),
                                retryable=invalid_rounds<2,retries_remaining=max(0,2-invalid_rounds),
                                instruction="Correct the final arguments to match finish_verification's schema.")
                    answer=annotate(answer)
                    messages.append(dict(type="function_call_output",call_id=finishes[0]["call_id"],output=encoded(answer)))
                    transcript.append(dict(tool="finish_verification",arguments=finishes[0]["arguments"],result=answer))
                    if invalid_rounds>=2:
                        termination="invalid_tool_arguments"
                        raise ValueError("Tool argument retries exhausted")
                    invalid_rounds+=1;metrics["argument_retry_rounds"]+=1
                    continue
                checked=finalize(value,tools)
                if checked.get("counterexample_errors") and checked["status"]!="FAIL":
                    answer=dict(status="UNKNOWN",error_type="CounterexampleError",message="; ".join(checked["counterexample_errors"]),
                                retryable=invalid_rounds<2,retries_remaining=max(0,2-invalid_rounds),
                                instruction="Correct the counterexample or query missing evidence, then resubmit. Do not claim FAIL without a supported witness.")
                    answer=annotate(answer)
                    messages.append(dict(type="function_call_output",call_id=finishes[0]["call_id"],output=encoded(answer)))
                    transcript.append(dict(tool="finish_verification",arguments=finishes[0]["arguments"],result=answer))
                    if invalid_rounds>=2:
                        result=checked;result["termination"]="invalid_counterexample";continue
                    invalid_rounds+=1;metrics["argument_retry_rounds"]+=1
                    continue
                result=checked
                continue
            if calls:
                seen=set(); invalid_batch=False
                for call in calls:
                    if metrics["tool_calls"]>=settings["max_tool_calls"]:
                        termination="tool_call_budget"
                        raise TimeoutError("Verify tool-call budget exhausted")
                    call_id=call.get("call_id")
                    if not call_id or call_id in seen: raise ValueError("Invalid native tool call identity")
                    seen.add(call_id);metrics["tool_calls"]+=1
                    try:
                        answer=api.call(call["name"],json.loads(call["arguments"]),offered=definitions)
                    except (ToolArgumentError,json.JSONDecodeError) as exc:
                        invalid_batch=True
                        answer=dict(status="UNKNOWN",error_type="ToolArgumentError",message=str(exc),
                                    retryable=invalid_rounds<2,retries_remaining=max(0,2-invalid_rounds),
                                    instruction="Correct the indicated arguments using the tool schema and allowed values; submit a corrected call.")
                    except (ValueError,KeyError,TypeError) as exc:
                        answer=dict(status="UNKNOWN",error_type=type(exc).__name__,message=str(exc))
                    answer=annotate(answer)
                    messages.append(dict(type="function_call_output",call_id=call_id,output=encoded(answer)))
                    transcript.append(dict(tool=call["name"],arguments=call["arguments"],result=answer))
                if invalid_batch:
                    if invalid_rounds>=2:
                        termination="invalid_tool_arguments"
                        raise ValueError("Tool argument retries exhausted")
                    invalid_rounds+=1;metrics["argument_retry_rounds"]+=1
                else:
                    invalid_rounds=0
                continue
            text="".join(c.get("text","") for item in output for c in item.get("content",[]) if c.get("type")=="output_text")
            if not text: raise ValueError("No final answer or native tool call; endpoint must support native tools")
            result=finalize(parse_final(text),tools)
    except (Exception,) as exc:
        # Errors are not task violations. Avoid echoing HTTP bodies/URLs/credentials.
        failures=[key for key,r in tools.records.items() if r.get("proof") and r.get("complete") and r.get("status")=="FAIL"]
        result=dict(status="UNKNOWN",summary="Verification incomplete",
                    items=[],unverified=[type(exc).__name__],forced_program_failures=[],unresolved_program_failures=failures,
                    termination=termination or ("transport_error" if not metrics["usage_complete"] else "evidence_or_response_error"))
    finally:
        tools.close()
    tools.checklist.save()
    metrics["context_mode"]=mode
    metrics["context_stats"]=tools.context_stats
    (output_dir/"context_metrics.json").write_text(encoded(dict(mode=mode,calls=context_calls,stats=tools.context_stats,
        note="Component sizes are serialized bytes, not tokens. Usage is provider-reported; missing values are unknown."))+"\n",encoding="utf-8")
    result.update(checklist=tools.checklist.snapshot(),run_id=Path(run_dir).name,identity=tools.identity,
                  metrics=metrics | dict(elapsed_seconds=round(time.monotonic()-started,3)),backend="direct_agent")
    (output_dir/"result.json").write_text(encoded(result)+"\n",encoding="utf-8")
    (output_dir/"trace.json").write_text(encoded(transcript)+"\n",encoding="utf-8")
    return result
