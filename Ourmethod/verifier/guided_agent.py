"""A separate controller-driven verifier; direct_agent remains a comparison backend."""
import json
from pathlib import Path
import time
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from queue import Queue, Empty
from .diagnostics import SessionDiagnostics, atomic_json
from .tools import EvidenceTools,encoded
from .context import ContextProjection,byte_size,signature
from .tool_schema import validate_schema,normalize,ToolArgumentError
from .guided_state import GuidedState,CONTROL_SCHEMAS,CONTROL_DESCRIPTIONS
from .gaps import annotate
from .model_tools import model_api, GUIDE as MODEL_GUIDE, DYNAMIC_GUIDE

INSTRUCTIONS='''You verify the original task against relevant cited observations. Configuration requirements may be supported by script commands; execution/result requirements need observations that establish those effects. Explain why evidence is sufficient; no file category is universally required. Do not run Scan, repair scripts, access GT or invent requirements.
The controller handles one requirement at a time: plan -> initial question -> freely query, update the question, or assess -> next requirement.
First identify short requirements from the full original task describing the original scope and exceptions in requirement text. Tool scope parameters are supplied only when calling evidence tools. Do not supply source references or line numbers in the plan. Then set a concrete question, its artifacts and the fact needed to answer it. Use stage_artifacts for before/after choices.
While a requirement is active, all evidence tools remain available until budget closing. Complete results, pagination, eight batches or repeated/no-progress queries do not force assessment or prohibit further evidence. Decide yourself when evidence suffices. Avoid redundant queries; saved identical queries are reused.
Gather at most four relevant evidence calls per response. Do not page all objects when counts/selected sets already answer the question. next_read is available content, not a requirement to keep reading.
You may call set_question alone at any time during active verification to update the concrete gap/artifact scope, preserving prior evidence. Or use assess_requirement alone to classify evidence and settle PASS/FAIL/UNKNOWN; optional CONTINUE with next_question records an interim assessment and keeps querying. CONTINUE is not required just to query again. UNKNOWN settles the requirement.
Tool FAIL applies only to its own predicate, stage and scope. Checks on excluded objects cannot prove task FAIL. An old broader FAIL must be explicitly inapplicable or superseded before using a corrected scoped PASS. Do not hide failures as context_only. Confirm conditions_match only when applicable evidence matches the task; the program checks explicit contradictions, not all task semantics.
Use exact flat schema fields; [] means unrestricted selection and null is only for genuinely optional choices. No finish_verification call exists here: the controller aggregates validated requirement decisions. During budget closing, no more queries or question changes are allowed; assess existing facts or UNKNOWN. Full original evidence remains on disk; classified sections are previews, not independent full proof.
'''
NAV={'list_artifacts','list_evidence'}


def definitions(tools,state):
    names={'plan':['plan_verification'],'question':['set_question']}.get(state.phase)
    if state.closing:names=['assess_requirement']
    if names:
        return [dict(type='function',name=name,strict=True,description=CONTROL_DESCRIPTIONS[name],parameters=CONTROL_SCHEMAS[name]) for name in names]
    return [d for d in model_api(tools).definitions if d['name']!='update_checklist'] + [dict(type='function',name=name,strict=True,description=CONTROL_DESCRIPTIONS[name],parameters=CONTROL_SCHEMAS[name]) for name in ('set_question','assess_requirement')]


def advance_gather(state, observations):
    """Track query activity for diagnostics; never gate evidence availability."""
    current=state.active
    current['gather_batches']=current.get('gather_batches',0)+1
    useful=[(name,r) for name,r in observations if not r.get('error_type') and not r.get('reused_evidence') and r.get('status') in {'FACT','PASS','FAIL'}]
    current['no_progress']=0 if useful else current['no_progress']+1


def evidence_call(tools,state,name,args,*,offered=None):
    """Called only in gather; duplicate queries reuse saved results without executing."""
    api=model_api(tools)
    api.validate(name,args,offered)
    normalized=normalize(args)
    query_paths=api.paths(name,args)
    # Check scope before reuse as a question may change within a requirement.
    extra=set(query_paths)-set(state.question['artifacts'])
    if extra:raise ToolArgumentError('Query artifacts not in current question: '+str(sorted(extra)))
    sig=signature(name,normalized)
    previous=state.active['queries'].get(sig)
    if previous:
        tools.current()
        answer=deepcopy(previous['answer']);answer['reused_evidence']=True
        answer['instruction']='Identical query already answered for this requirement; reuse evidence, then assess.'
        return answer,False
    answer=api.call(name,args,offered=offered)
    key=answer.get('evidence_id')
    if key:state.bind(key)
    success=name not in NAV and answer.get('status') in {'FACT','PASS','FAIL'} and not answer.get('error_type')
    state.active['queries'][sig]=dict(answer=deepcopy(answer))
    return answer,success


INSTRUCTIONS += '\n' + MODEL_GUIDE

def _verify_session(source,run_dir,script,execution,output_dir,settings,client,*,deadline,diagnostics,requirement=None,planner=False):
    started=time.monotonic();deadline=min(deadline if deadline is not None else float('inf'),started+settings['timeout_seconds'])
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    tools=EvidenceTools(source,Path(run_dir),script,output_dir/'evidence',settings,deadline)
    try:
        return _session_body(source,run_dir,script,execution,output_dir,settings,client,
                             deadline,diagnostics,requirement,planner,tools,started)
    except BaseException:
        tools.close()
        raise


def _session_body(source,run_dir,script,execution,output_dir,settings,client,deadline,diagnostics,requirement,planner,tools,started):
    diagnostics.start('plan' if planner else 'question')
    instructions=INSTRUCTIONS
    if model_api(tools).dynamic:
        instructions=instructions.replace('all evidence tools remain available','evidence creation tools remain available')+'\n'+DYNAMIC_GUIDE
        instructions=instructions.replace('read_evidence_section uses a returned section.','use the returned section_reads tool for a classified section.')
    state=GuidedState(tools)
    state.observer=diagnostics.event
    if requirement is not None:
        state.load_requirement(requirement)
        instructions += "\nYou are an isolated worker. Verify only the assigned requirement; do not plan or judge other requirements."
    token_limit=settings['max_total_tokens']
    task={key:tools.read(key) for key in tools.artifacts if key.startswith('input/') and key.endswith('.md')}
    initial=dict(task_sources=task,run_id=Path(run_dir).name,execution=execution,artifacts=tools.model_artifact_manifest(),
                 stage_artifacts=tools.stage_artifacts,candidate_script=dict(artifact='candidate.dofile',text=script if len(script)<=20000 else 'Use read_lines'))
    if requirement is not None:initial['assigned_requirement']=requirement
    messages=[dict(role='user',content=encoded(initial))];context=ContextProjection(messages[0],settings.get('context_mode','compact'))
    metrics=dict(model_calls=0,tool_calls=0,work_model_calls=0,assessment_model_calls=0,work_tool_calls=0,assessment_tool_calls=0,total_tokens=0,usage_complete=True,usage=[],argument_retry_rounds=0,reused_queries=0)
    transcript=[];context_calls=[];closing_used=False;closing_reason=None;failure=None;preflight_fail=None;in_flight=False;operation='controller'
    def persist():
        diagnostics.status['metrics']=deepcopy(metrics)
        if in_flight:diagnostics.status['metrics']['usage_complete']=False
        atomic_json(output_dir/'trace.json',transcript)
        atomic_json(output_dir/'context_metrics.json',dict(calls=context_calls,stats=tools.context_stats,note='Serialized bytes are not precise tokens; missing usage is unknown.'))
        state.save()
        diagnostics.publish()
    persist()
    try:
        if execution.get('exit_status')!='completed':
            preflight_fail='Scan execution did not complete';state.phase='done';state.termination='scan_incomplete'
        while state.phase!='done':
            remaining=deadline-time.monotonic()
            if remaining<=0:state.termination='time_budget';break
            if token_limit and metrics['total_tokens']>=token_limit:
                state.termination='token_budget';break
            reason=('closing_time_budget' if remaining<30 else
                    'model_call_budget' if metrics['work_model_calls']>=settings['max_model_turns'] else
                    'tool_call_budget' if metrics['work_tool_calls']>=settings['max_tool_calls'] else None)
            if reason or state.closing:
                closing_reason=closing_reason or reason or 'closing_time_budget'
                if not state.active:state.termination=closing_reason;break
                if not state.closing:
                    state.closing=True
                    state.event('closing',reason=closing_reason,remaining_seconds=round(remaining,3))
                if closing_used:state.termination='assessment_incomplete';break
                state.phase='assess';closing_used=True
            assessment_request=state.closing
            budget_phase='assessment' if assessment_request else 'work'
            phase=state.phase;working=phase in {'gather','assess'} and not state.closing;defs=definitions(tools,state);allowed={d['name'] for d in defs}
            projected,archive,components=context.build(messages,tools)
            controller=state.prompt()+'\n'+archive+f'\nRemaining {remaining:.1f}s; correction attempts remaining {max(0,2-state.retry_count)}.'
            if assessment_request:
                controller+='\nFinal assessment allowance (separate from work quota). Submit assess_requirement only, using existing evidence, or UNKNOWN. No queries, question updates or CONTINUE. Classify every bound evidence ID.'
            projected=[projected[0],dict(role='user',content='Current guided controller state:\n'+controller),*projected[1:]]
            request_bytes=byte_size(projected)+byte_size(defs)+len(instructions.encode())
            if token_limit and metrics['total_tokens']+request_bytes>=token_limit:
                state.termination='token_admission_budget';break
            count=min(settings['max_output_tokens_per_call'],token_limit-metrics['total_tokens']-request_bytes) if token_limit else settings['max_output_tokens_per_call']
            metrics['model_calls']+=1
            metrics[budget_phase+'_model_calls']+=1
            mark=dict(call=metrics['model_calls'],budget_phase=budget_phase,phase=phase,requirement_id=state.active and state.active['id'],
                      bytes=components|dict(initial=byte_size(initial),controller=len(controller.encode()),tools=byte_size(defs)),
                      input_tokens=None,output_tokens=None,cached_input_tokens=None,usage_complete=False)
            context_calls.append(mark)
            choice=dict(type='function',name=next(iter(allowed))) if len(allowed)==1 else 'auto'
            operation='request';in_flight=True
            persist();diagnostics.event('model_call_started',phase=phase,budget_phase=budget_phase,call=metrics['model_calls'])
            remaining=deadline-time.monotonic()
            if remaining<=0:
                in_flight=False;metrics['model_calls']-=1;metrics[budget_phase+'_model_calls']-=1;state.termination='time_budget';break
            response=client.respond(projected,defs,instructions,remaining,count,next(iter(CONTROL_SCHEMAS.values())) if working else defs[0]['parameters'],choice)
            in_flight=False
            operation='controller'
            diagnostics.event('model_call_finished',phase=phase,budget_phase=budget_phase,call=metrics['model_calls'])
            context.mark_seen(projected)
            usage=response.get('usage') or {};metrics['usage'].append(usage)
            used=usage.get('total_tokens')
            if type(used) is int:metrics['total_tokens']+=used
            else:metrics['usage_complete']=False;metrics['total_tokens']+=request_bytes+count
            mark.update(input_tokens=usage.get('input_tokens'),output_tokens=usage.get('output_tokens'),cached_input_tokens=(usage.get('input_tokens_details') or {}).get('cached_tokens'),usage_complete=type(used) is int)
            output=response.get('output',[]);calls=[x for x in output if x.get('type')=='function_call']
            context.start_turn(messages)
            messages.extend(output);transcript.append(dict(response={k:v for k,v in response.items() if k!='output'},output=output,phase=phase,requirement_id=mark['requirement_id']))
            persist()
            invalid=False;fresh=False;seen=set();observations=[];controlled=False
            for index,call in enumerate(calls):
                if deadline-time.monotonic()<=0:state.termination='time_budget';break
                call_id=call.get('call_id');name=call.get('name')
                if ((not assessment_request and metrics['work_tool_calls']>=settings['max_tool_calls'])
                        or (assessment_request and index>=1)):
                    # Pair every offered call with a result before the next model
                    # request, including calls refused after a work quota is hit.
                    answer=annotate(dict(status='UNKNOWN',error_type='BudgetClosing',
                        message='Work tool quota exhausted; only the separate final assessment is allowed.' if not assessment_request else 'Only one assessment call is allowed.'))
                    messages.append(dict(type='function_call_output',call_id=call_id,output=encoded(answer)))
                    transcript.append(dict(tool=name,arguments=call.get('arguments'),result=answer,phase=phase,requirement_id=mark['requirement_id']))
                    if not assessment_request:
                        closing_reason='tool_call_budget'
                        if not state.closing:
                            state.closing=True;state.event('closing',reason=closing_reason)
                    persist();diagnostics.event('tool_call_rejected',phase=phase,tool=name,call_id=call_id,error_type='BudgetClosing')
                    continue
                metrics['tool_calls']+=1
                metrics[budget_phase+'_tool_calls']+=1
                operation='tool'
                persist();diagnostics.event('tool_call_started',phase=phase,call=metrics['tool_calls'],model_call=metrics['model_calls'],tool=name,call_id=call_id)
                try:
                    if not call_id or call_id in seen:raise ToolArgumentError('Missing or duplicate call_id')
                    seen.add(call_id)
                    # Phase is enforced locally even when the provider ignores tool_choice.
                    if name not in allowed:raise ToolArgumentError(f'{name} forbidden in phase={phase}; allowed={sorted(allowed)}')
                    if index>=(4 if working else 1):raise ToolArgumentError('At most four evidence calls, or one control call, per response')
                    if name not in CONTROL_SCHEMAS and (state.closing or deadline-time.monotonic()<30):
                        state.closing=True;raise ToolArgumentError('Closing: no more evidence tools may execute')
                    args=json.loads(call['arguments'])
                    if name in CONTROL_SCHEMAS:
                        if len(calls)!=1:raise ToolArgumentError('Submit a question update or assessment alone, separately from evidence calls')
                        controlled=True
                        validate_schema(args,CONTROL_SCHEMAS[name]);args=normalize(args)
                        handler={'plan_verification':state.plan,'set_question':state.set_question,'assess_requirement':state.assess}[name]
                        answer=handler(args)
                    else:
                        answer,successful=evidence_call(tools,state,name,args,offered=defs);fresh|=successful
                        observations.append((name,answer))
                        if answer.get('reused_evidence'):metrics['reused_queries']+=1
                except (ToolArgumentError,ValueError,KeyError,TypeError) as exc:
                    invalid=True;answer=annotate(dict(status='UNKNOWN',error_type='ToolArgumentError',message=str(exc),retryable=state.retry_count<2,retries_remaining=max(0,2-state.retry_count)))
                messages.append(dict(type='function_call_output',call_id=call_id,output=encoded(answer)))
                transcript.append(dict(tool=name,arguments=call.get('arguments'),result=answer,phase=phase,requirement_id=mark['requirement_id']))
                persist();diagnostics.event('tool_call_finished',phase=state.phase,call=metrics['tool_calls'],tool=name,call_id=call_id,evidence_id=answer.get('evidence_id'),error_type=answer.get('error_type'))
                operation='controller'
            if not calls:invalid=True
            if working and state.active and state.phase in {'gather','assess'} and not controlled and (observations or not invalid):
                advance_gather(state,observations)
            if state.termination:break
            if planner and state.entries and not invalid:
                state.phase='done'
            elif state.closing and closing_used:
                if state.active:state.termination='closing_time_budget' if closing_reason=='closing_time_budget' else 'assessment_incomplete'
                state.phase='done'
            elif invalid and state.phase==phase:
                if state.retry_count>=2:
                    if state.active:
                        state.settle_unknown('Parameter or phase correction attempts exhausted');state.termination='argument_correction_exhausted'
                    else:state.phase='done';state.termination='invalid_plan'
                else:state.retry_count+=1;metrics['argument_retry_rounds']+=1
            else:state.retry_count=0
            state.event('response_processed',phase_before=phase,phase_after=state.phase,new_successful_evidence=fresh,invalid=invalid)
    except Exception as exc:
        failure=type(exc).__name__
        if in_flight:metrics['usage_complete']=False
        state.termination='time_budget' if isinstance(exc,TimeoutError) and time.monotonic()>=deadline else ('request_error' if operation=='request' else 'tool_error')
        diagnostics.event('execution_error',phase=state.phase,operation=operation,failure_type=failure)
    finally:
        try:tools.close()
        except Exception as exc:
            failure=failure or type(exc).__name__;state.termination=state.termination or 'tool_error'
            diagnostics.event('cleanup_error',failure_type=type(exc).__name__)
    if not planner:
        for row in state.entries:
            if row['state'] not in {'PASS','FAIL','UNKNOWN'} or (failure and row['state']=='PASS'):
                row.update(state='UNKNOWN',message=state.termination or 'Requirement not completed')
    result=state.summarize()
    if preflight_fail:result.update(status='FAIL',summary=preflight_fail,unverified=[])
    if failure:result['unverified'].append(failure);result['failure_type']=failure
    if state.termination:result['termination']=state.termination
    state.save()
    metrics.update(context_mode=settings.get('context_mode','compact'),context_stats=tools.context_stats,elapsed_seconds=round(time.monotonic()-started,3))
    result.update(run_id=Path(run_dir).name,identity=tools.identity,metrics=metrics,backend='guided_agent',checklist=state.snapshot())
    if planner and state.entries:diagnostics.event('planning_completed',requirements=len(state.entries))
    if planner:result['planned_requirements']=[{k:r[k] for k in ('id','requirement')} for r in state.entries]
    persist()
    atomic_json(output_dir/'result.json',result)
    diagnostics.finish(result)
    return result


def _unfinished(requirement, reason, run_id):
    """A failed execution is never converted to a semantic FAIL or PASS."""
    items=[];checklist=[]
    if requirement:
        items=[dict(requirement=requirement['requirement'],task_ref=None,
                    status='UNKNOWN',validated_status='UNKNOWN',kind='semantic',judgment_basis='model',
                    evidence_ids=[],message=reason,counterexamples=None,inapplicable_checks=None)]
        checklist=[dict(id=requirement['id'],requirement=requirement['requirement'],task_ref=None,
                        state='UNKNOWN',evidence_ids=[],gap=reason)]
    return dict(status='UNKNOWN',summary='Guided verification: UNKNOWN',items=items,
                unverified=[(requirement['id']+': ' if requirement else '')+reason],
                forced_program_failures=[],unresolved_program_failures=[],interpreted_program_failures=[],
                checked_counterexamples=[],reviewed_task=bool(items),checklist=checklist,
                scope='Model task interpretation with deterministic state and evidence consistency checks; not independent semantic proof',
                run_id=run_id,identity={},backend='guided_agent',termination=reason,
                metrics=dict(model_calls=0,tool_calls=0,work_model_calls=0,assessment_model_calls=0,work_tool_calls=0,assessment_tool_calls=0,total_tokens=0,usage_complete=True,usage=[],
                             argument_retry_rounds=0,reused_queries=0,elapsed_seconds=0,context_stats={}))


def _safe_session(source,run_dir,script,execution,directory,settings,client,deadline,diagnostics,requirement=None,planner=False):
    if not planner and deadline-time.monotonic()<30:
        reason='queue_time_budget' if time.monotonic()>=deadline else 'queue_closing_time_budget'
        result=_unfinished(requirement,reason,Path(run_dir).name)
        diagnostics.status['queue_seconds']=round(time.monotonic()-diagnostics.created,3)
        runtime_status='not_started'
    else:
        try:
            return _verify_session(source,run_dir,script,execution,directory,settings,client,
                                   deadline=deadline,diagnostics=diagnostics,requirement=requirement,planner=planner)
        except Exception as exc:
            # No exception text from transports/configuration: it can include credentials.
            result=_unfinished(requirement,'initialization_error',Path(run_dir).name)
            result['failure_type']=type(exc).__name__
            result['metrics'].update(diagnostics.status.get('metrics',{}))
            if result['metrics']['model_calls']:result['metrics']['usage_complete']=False
            diagnostics.event('execution_error',failure_type=type(exc).__name__)
            runtime_status='error'
    directory=Path(directory)
    for filename,value in [('trace.json',[]),('context_metrics.json',dict(calls=[],stats={})),
                           ('guided_state.json',dict(phase='done',requirements=[requirement] if requirement else [],events=[],termination=result['termination']))]:
        if not (directory/filename).exists():atomic_json(directory/filename,value)
    (directory/'evidence').mkdir(exist_ok=True)
    atomic_json(directory/'result.json',result)
    diagnostics.finish(result,runtime_status)
    return result


def verify(source,run_dir,script,execution,output_dir,settings,client,*,deadline=None,design_cache=None):
    """Plan once, then verify every requirement in an isolated bounded worker."""
    started=time.monotonic()
    deadline=min(deadline if deadline is not None else float('inf'),started+settings['timeout_seconds'])
    concurrency=settings.get('worker_concurrency',3)
    if type(concurrency) is not int or concurrency<1:raise ValueError('worker_concurrency must be a positive integer')
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    updates=Queue();overview=[]
    planning=SessionDiagnostics(output_dir/'planner','planner')
    def overview_save():
        atomic_json(output_dir/'subtasks.json',dict(worker_concurrency=concurrency,planner=deepcopy(planning.status),subtasks=overview))
    overview_save()
    plan_result=_safe_session(source,run_dir,script,execution,output_dir/'planner',settings,client,deadline,planning,planner=True)
    overview_save()
    requirements=plan_result.pop('planned_requirements',[])
    if plan_result.get('termination') or execution.get('exit_status')!='completed':requirements=[]
    sessions=[]
    queued_at=time.monotonic()
    for index,requirement in enumerate(requirements,1):
        session=SessionDiagnostics(output_dir/'workers'/f'{index:03d}',f'{index:03d}',requirement,
                                   updates.put,queued_at)
        sessions.append(session)
        overview.append(deepcopy(session.status))
    def drain():
        changed=False
        while True:
            try:update=updates.get_nowait()
            except Empty:break
            overview[int(update['worker_id'])-1]=update;changed=True
        if changed:overview_save()
    drain()
    results=[None]*len(requirements)
    # Only the controller writes subtasks.json. Session events are sent as copied snapshots.
    with ThreadPoolExecutor(max_workers=concurrency,thread_name_prefix='verify') as pool:
        futures={pool.submit(_safe_session,source,run_dir,script,execution,session.directory,
                             deepcopy(settings),client,deadline,session,requirement):index
                 for index,(session,requirement) in enumerate(zip(sessions,requirements))}
        pending=set(futures)
        while pending:
            drain()
            for future in list(pending):
                if future.done():
                    index=futures[future]
                    results[index]=future.result()
                    pending.remove(future)
            if pending:
                try:
                    update=updates.get(timeout=0.05)
                    overview[int(update['worker_id'])-1]=update
                    overview_save()
                except Empty:pass
    drain()
    if not requirements:
        result=plan_result
    else:
        result=deepcopy(plan_result)
        result.pop('termination',None)
        result.pop('failure_type',None)
        for key in ('items','unverified','forced_program_failures','unresolved_program_failures',
                    'interpreted_program_failures','checked_counterexamples','checklist'):
            result[key]=[entry for worker in results for entry in worker[key]]
        result['status']='FAIL' if any(r['status']=='FAIL' for r in results) else 'PASS' if all(r['status']=='PASS' for r in results) else 'UNKNOWN'
        result.update(summary='Guided verification: '+result['status'],reviewed_task=True)
        if any(s['runtime_status'] in {'timed_out','not_started'} for s in overview):result['termination']='time_budget'
    metrics=result['metrics']=deepcopy(plan_result['metrics'])
    for worker in results:
        for key in ('model_calls','tool_calls','work_model_calls','assessment_model_calls','work_tool_calls','assessment_tool_calls','total_tokens','argument_retry_rounds','reused_queries'):
            metrics[key]+=worker['metrics'][key]
        metrics['usage'].extend(worker['metrics']['usage'])
        metrics['usage_complete'] &= worker['metrics']['usage_complete']
    metrics.update(elapsed_seconds=round(time.monotonic()-started,3),worker_concurrency=concurrency,
                   budget_scope='planner and each requirement independently; one separate final assessment per requirement; shared absolute deadline')
    traces=[];contexts=[];entries=[];events=[];evidence_index={};stats={}
    for worker_id,directory in [('planner',output_dir/'planner')]+[(s.status['worker_id'],s.directory) for s in sessions]:
        for row in json.loads((directory/'trace.json').read_text()):traces.append(dict(row,worker_id=worker_id))
        context=json.loads((directory/'context_metrics.json').read_text())
        for row in context['calls']:contexts.append(dict(row,worker_id=worker_id))
        for key,value in context['stats'].items():stats[key]=stats.get(key,0)+value
        state=json.loads((directory/'guided_state.json').read_text())
        if worker_id!='planner':entries.extend(dict(row,worker_id=worker_id) for row in state['requirements'])
        for event in state['events']:events.append(dict(event,worker_id=worker_id))
        for path in (directory/'evidence').glob('*.json'):
            record=json.loads(path.read_text())
            if 'evidence_id' in record:evidence_index[record['evidence_id']]=str(path.relative_to(output_dir))
    # Old readers retain their views; detailed chronology is worker-local, not a fabricated serial conversation.
    atomic_json(output_dir/'trace.json',traces)
    atomic_json(output_dir/'context_metrics.json',dict(calls=contexts,stats=stats,note='Grouped by worker; call numbers are worker-local. Missing usage is unknown.'))
    atomic_json(output_dir/'guided_state.json',dict(phase='done',closing=False,current_index=len(entries),
                requirements=entries,question=None,events=events,termination=result.get('termination'),worker_concurrency=concurrency))
    atomic_json(output_dir/'evidence_index.json',evidence_index)
    metrics['context_stats']=stats
    result['subtask_diagnostics']=dict(index='subtasks.json',report='subtasks.md',evidence_index='evidence_index.json',planner='planner/status.json')
    atomic_json(output_dir/'result.json',result)
    def cell(value):return str(value if value is not None else '—').replace('|','\\|').replace('\n',' ')
    lines=['# 子任务核验诊断','',f"整体：{result['status']}；并发：{concurrency}。规划详情：[planner](planner/status.json)。",'',
           '| 子任务 | 要求 | 运行状态 | 结论 | 阶段 | 排队秒 | 执行秒 | 模型/工具 | 常规/收尾模型 | 终止原因 | 详情 |',
           '|---|---|---|---|---|---:|---:|---|---|---|---|']
    for s in overview:
        m=s['metrics'];folder='workers/'+s['worker_id']
        values=[s['requirement_id'],s['requirement']['requirement'],s['runtime_status'],s['verdict'],s.get('last_phase',s['phase']),
                s['queue_seconds'],s['elapsed_seconds'],f"{m.get('model_calls',0)}/{m.get('tool_calls',0)}",f"{m.get('work_model_calls',0)}/{m.get('assessment_model_calls',0)}",s['termination']]
        lines.append('| '+' | '.join(cell(v) for v in values)+f' | [状态]({folder}/status.json) · [事件]({folder}/events.jsonl) · [交互]({folder}/trace.json) |')
    if not overview:lines.extend(['',f"未启动子任务：{cell(result.get('termination') or result['summary'])}。"])
    temporary=output_dir/'subtasks.md.tmp';temporary.write_text('\n'.join(lines)+'\n',encoding='utf-8');temporary.replace(output_dir/'subtasks.md')
    return result
