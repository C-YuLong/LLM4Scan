"""Requirement-scoped state and evidence applicability, independent of direct loop."""
from copy import deepcopy
import json
from .tool_schema import obj, optional, array, enum, TEXT, S, B, STRINGS, ToolArgumentError, validate_schema
from .result import usable_evidence
from .diagnostics import atomic_json

PLAN=obj(requirements=array(obj(id=dict(type='string',minLength=1,maxLength=12),
    requirement=dict(type='string',minLength=1,maxLength=160)),1))
PLAN['properties']['requirements']['maxItems']=16
QUESTION=obj(question=TEXT,artifacts=STRINGS,expected_fact=TEXT)
USE=obj(evidence_id=TEXT,use=enum('applicable','context_only','inapplicable','superseded'),reason=TEXT,replaced_by=optional(STRINGS))
ASSESS=obj(status=enum('PASS','FAIL','UNKNOWN','CONTINUE'),judgment_basis=enum('program','model'),
           message=TEXT,evidence=array(USE),conditions_match=B,
           next_question=optional(QUESTION))
CONTROL_SCHEMAS=dict(plan_verification=PLAN,set_question=QUESTION,assess_requirement=ASSESS)
CONTROL_DESCRIPTIONS={
 'plan_verification':'Identify <=16 short requirement groups in order from the full original task. Group obligations established by the same comparison (such as preserved cell types, counts and connections), preserving every obligation and exception in the requirement text. Do not split headings or delivery filenames into separate workers. Do not supply source references, line numbers or tool scope parameters. No verdicts yet.',
 'set_question':'For the current requirement name one specific unknown, artifacts to query, and the fact needed to answer it. Include both netlists for comparisons, using stage_artifacts. May also update the active question/artifact scope while verifying, without discarding evidence. Submit alone; unavailable only during budget closing. This is a goal, not an answer.',
 'assess_requirement':'Interpret the original requirement against cited observations. Classify every bound evidence ID; inapplicable/superseded need reasons, superseded needs replacement IDs. Program PASS/FAIL needs complete matching proof, not FACT. conditions_match confirms the cited evidence covers the full original requirement, including its stage, scope and exceptions; schema validity or a subset PASS alone is insufficient. Resolve old FAIL applicability explicitly. CONTINUE optionally records an interim assessment and needs a new concrete question; further queries do not require CONTINUE. UNKNOWN settles this requirement. unused next_question/replaced_by=null.'}


class GuidedState:
    def __init__(self,tools):
        self.tools=tools;self.entries=[];self.phase='plan';self.question=None
        self.events=[];self.retry_count=0;self.closing=False;self.termination=None
        self.sequence=0;self.current_index=0
        tools.guided_state=self;tools.checklist=self

    @property
    def active(self):
        return self.entries[self.current_index] if self.current_index<len(self.entries) else None

    def snapshot(self):
        return [dict(id=r['id'],requirement=r['requirement'],state=r['state'],
                     evidence_ids=list(r['bindings']),gap=r.get('message','')) for r in self.entries]

    def prompt(self):
        active={k:v for k,v in self.active.items() if k!='queries'} if self.active else None
        return json.dumps(dict(phase=self.phase,closing=self.closing,current_requirement=active,
                               question=self.question,requirements=self.snapshot()),ensure_ascii=False,separators=(',',':'))

    def save(self):
        atomic_json(self.tools.output_dir.parent/'guided_state.json',dict(phase=self.phase,closing=self.closing,
            current_index=self.current_index,requirements=self.entries,question=self.question,events=self.events,termination=self.termination))

    def event(self,kind,**data):
        self.sequence+=1;self.events.append(dict(sequence=self.sequence,event=kind,requirement=self.active and self.active['id'],**data));self.save()
        if getattr(self,'observer',None):self.observer(kind,phase=self.phase,**data)

    def plan(self,args):
        if self.phase!='plan' or self.entries:raise ToolArgumentError('plan already established')
        rows=args['requirements']
        validate_schema(args,PLAN)
        if len(rows)>16 or len({r['id'] for r in rows})!=len(rows):raise ToolArgumentError('plan requires <=16 unique IDs')
        self.entries=[dict(r,state='pending',
                           bindings={},queries={},no_progress=0,message='',judgment_basis=None) for r in deepcopy(rows)]
        self.phase='question';self.event('planned');return dict(status='FACT',message='Requirements recorded; set_question for the first requirement.')

    def load_requirement(self, requirement):
        """Initialize an isolated worker from a previously validated plan row."""
        self.plan(dict(requirements=[deepcopy(requirement)]))

    def set_question(self,args):
        if self.closing or self.phase not in {'question','gather','assess'} or self.active is None:raise ToolArgumentError('set_question requires an active requirement outside budget closing')
        self.validate_question(args)
        self.question=deepcopy(args);self.active['state']='checking';self.active['gather_batches']=0;self.active['no_progress']=0;self.phase='gather';self.event('question_set',question=args)
        return dict(status='FACT',message='Query freely within the remaining budget; complete results do not force assessment. Use set_question to update the gap/artifacts, or assess_requirement alone when ready.')

    def validate_question(self,args):
        if not args['artifacts']:raise ToolArgumentError('question requires explicit artifact IDs')
        bad=[a for a in args['artifacts'] if a not in self.tools.identity]
        if bad:raise ToolArgumentError(f'Unknown question artifacts: {bad}')
        if args['question'].strip().lower().strip('.!。！') in {'continue','continue checking','more evidence','继续检查','继续查证','继续验证'}:raise ToolArgumentError('Name the specific missing fact, not just continue checking')
        if len(args['question'].strip())<8 or len(args['expected_fact'].strip())<8:raise ToolArgumentError('State a concrete question and the fact needed, not just continue checking')

    def bind(self,key):
        if key in self.tools.records:self.active['bindings'].setdefault(key,dict(use='pending',reason='',replaced_by=[]))

    def disposition(self,key):
        bound=[dict(requirement=r['id'],**r['bindings'][key]) for r in self.entries if key in r['bindings']]
        return bound

    def assess(self,args):
        if self.phase not in {'gather','assess'} or not self.active:raise ToolArgumentError('assessment is not allowed in this phase')
        current=self.active;uses={e['evidence_id']:e for e in args['evidence']}
        if len(uses)!=len(args['evidence']):raise ToolArgumentError('Duplicate evidence disposition')
        if set(current['bindings'])-set(uses):raise ToolArgumentError('Classify all current evidence IDs: '+str(sorted(set(current['bindings'])-set(uses))))
        if set(uses)-set(self.tools.records):raise ToolArgumentError('Missing or stale evidence ID')
        for key,use in uses.items():
            replacements=use.get('replaced_by') or []
            if use['use']=='superseded' and (not replacements or key in replacements or any(k not in uses or uses[k]['use']!='applicable' for k in replacements)):
                raise ToolArgumentError('superseded needs distinct applicable replacement evidence IDs')
        applicable=[(k,self.tools.records[k]) for k,u in uses.items() if u['use']=='applicable']
        failures=[(k,r) for k,r in applicable if r.get('proof') and r.get('status')=='FAIL']
        if any(self.tools.records[k].get('proof') and self.tools.records[k].get('status')=='FAIL' and u['use']=='context_only' for k,u in uses.items()):
            raise ToolArgumentError('A failed check needs applicable/inapplicable/superseded disposition, not context_only')
        verdict=args['status']
        if verdict in {'PASS','FAIL'}:
            if not args['conditions_match']:raise ToolArgumentError('Confirm task stage/scope/predicate applicability, or retain UNKNOWN')
            for key,record in applicable:
                paths=([record['artifact']] if record.get('artifact') else [])+record.get('artifacts',[])
                if paths and not set(paths)<=set(self.question['artifacts']):raise ToolArgumentError(f'{key}: evidence artifacts differ from this question; set an appropriate question before deciding')
            if not any(usable_evidence(r) for _,r in applicable):raise ToolArgumentError('Decision requires successful applicable cited evidence')
            if not args['message'].strip():raise ToolArgumentError('Explain how cited evidence supports the original requirement')
            proofs=[(k,r) for k,r in applicable if r.get('proof') and r.get('complete') and r.get('status')==verdict]
            if args['judgment_basis']=='program' and not proofs:raise ToolArgumentError('FACT is not complete program proof; use model interpretation with explanation or UNKNOWN')
            if verdict=='PASS' and failures:raise ToolArgumentError('Applicable failed checks conflict with PASS; resolve applicability explicitly')
            if verdict=='FAIL':
                def predicate(record):
                    params={k:v for k,v in record.get('parameters',{}).items() if k not in {'include','exclude','scope','top'}}
                    return (record.get('operation'),tuple(record.get('artifacts',[])),json.dumps(params,sort_keys=True))
                if failures and any(p.get('proof') and p.get('status')=='PASS' and predicate(p)==predicate(f) for _,p in applicable for _,f in failures):
                    raise ToolArgumentError('Conflicting applicable PASS/FAIL checks; explain applicability before FAIL')
        if verdict=='CONTINUE':
            if self.closing:raise ToolArgumentError('Closing state cannot continue gathering')
            if not args.get('next_question'):raise ToolArgumentError('CONTINUE requires a concrete next_question')
            self.validate_question(args['next_question'])
            if args['next_question']['question'].strip().lower()==self.question['question'].strip().lower():raise ToolArgumentError('CONTINUE must identify the remaining specific gap, not repeat the same question')
        elif args.get('next_question') is not None:raise ToolArgumentError('next_question must be null for a settled decision')
        current['bindings']={k:dict(use=u['use'],reason=u['reason'],replaced_by=u.get('replaced_by') or []) for k,u in uses.items()}
        current.update(message=args['message'],judgment_basis=args['judgment_basis'])
        if verdict=='CONTINUE':
            self.question=deepcopy(args['next_question']);current['gather_batches']=0;current['no_progress']=0;self.phase='gather';self.event('continue',question=self.question)
        else:
            current['state']=verdict;self.event('settled',status=verdict)
            self.current_index+=1;self.question=None;self.phase='done' if verdict=='FAIL' or self.current_index>=len(self.entries) or self.closing else 'question'
        return dict(status='FACT',message='Assessment recorded; controller chose the next phase.',phase=self.phase)

    def settle_unknown(self,message):
        if self.active:self.active.update(state='UNKNOWN',message=message)
        self.event('settled_unknown',reason=message)
        self.current_index+=1;self.question=None;self.phase='done' if self.closing or self.current_index>=len(self.entries) else 'question'
        self.retry_count=0

    def summarize(self):
        items=[];unverified=[];confirmed=[];interpreted=[];unresolved=[]
        for r in self.entries:
            status=r['state'] if r['state'] in {'PASS','FAIL','UNKNOWN'} else 'UNKNOWN'
            applicable=[key for key,u in r['bindings'].items() if u['use']=='applicable']
            item=dict(requirement=r['requirement'],task_ref=None,status=status,validated_status=status,
                      kind='semantic',judgment_basis=r.get('judgment_basis') or 'model',evidence_ids=applicable,
                      message=r['message'] or 'Requirement not completed',counterexamples=None,inapplicable_checks=None)
            items.append(item)
            if status=='UNKNOWN':unverified.append(r['id']+': '+item['message'])
            if status=='FAIL':confirmed.extend(k for k in applicable if self.tools.records[k].get('proof') and self.tools.records[k].get('status')=='FAIL')
            unresolved.extend(k for k,u in r['bindings'].items() if u['use']=='pending' and self.tools.records[k].get('proof') and self.tools.records[k].get('status')=='FAIL')
            interpreted.extend(dict(evidence_id=k,requirement_id=r['id'],task_ref=None,use=u['use'],reason=u['reason'],replaced_by=u['replaced_by']) for k,u in r['bindings'].items() if u['use'] in {'inapplicable','superseded'})
        status='FAIL' if any(i['status']=='FAIL' for i in items) else 'PASS' if items and all(i['status']=='PASS' for i in items) else 'UNKNOWN'
        if not items:unverified.append('No validated requirement plan')
        return dict(status=status,summary='Guided verification: '+status,items=items,unverified=unverified,
                    forced_program_failures=confirmed,unresolved_program_failures=list(dict.fromkeys(unresolved)),interpreted_program_failures=interpreted,
                    checked_counterexamples=[],reviewed_task=bool(items),scope='Model task interpretation with deterministic state and evidence consistency checks; not independent semantic proof')
