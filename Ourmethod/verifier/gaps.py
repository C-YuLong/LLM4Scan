"""Short next-action guidance; never convert uncertainty into proof."""

def annotate(value):
    if value.get('status')!='UNKNOWN' or value.get('gap_category'):return value
    details=value.get('observed',{}).get('details',{})
    subchecks=details.get('subchecks',[]) if isinstance(details,dict) else []
    gaps=[]
    for check in subchecks:
        if check.get('status')!='unverified':continue
        kind=check.get('kind');reason=check.get('reason_kind')
        category=('needs_interpretation' if kind=='exceptions' else 'invalid_request' if reason=='model_protocol_error'
                  else 'capability_unsupported' if reason=='capability_unsupported' else 'evidence_missing')
        action=('Use query_ff_changes for actual unchanged objects and interpret permitted exceptions from the task.' if kind=='exceptions'
                else 'Supply missing explicit mapping pairs to check_ff_mapping, or use query_ff_changes and task-authorized default mapping.' if kind=='mapping'
                else 'This checker did not establish normal connectivity; counts cannot prove it. Record the gap or use genuinely supported evidence.')
        gaps.append(dict(aspect=kind,category=category,next_step=action))
    if gaps:
        value['gaps']=gaps;value['gap_category']='needs_interpretation' if any(x['category']=='needs_interpretation' for x in gaps) else gaps[0]['category']
        value['next_step']='Handle each gap separately. Known observations remain available; use query_ff_changes for task interpretation.'
        return value
    text=(value.get('reason','')+' '+value.get('message','')).lower()
    if value.get('error_type')=='ToolArgumentError':
        category='invalid_request';action='Correct the exact field/type/enum error using the schema; do not invent parameters.'
    elif value.get('error_type')=='CounterexampleError':
        category='evidence_missing';action='Correct the cited witness or query the missing original/actual object evidence.'
    elif any(s in text for s in ('unsupported','forward-only','not establish','not proven')):
        category='capability_unsupported';action='Stop this unsupported proof or choose an applicable supported check; do not infer it from unrelated counts.'
    elif any(s in text for s in ('empty','missing','incomplete')):
        category='evidence_missing';action='Query the missing population/artifact or establish applicability from the task; do not assume an empty set passes.'
    elif value.get('error_type')=='ToolArgumentError' or any(s in text for s in ('unknown hierarchy','unknown cell type','invalid report','unsupported report operation')):
        category='invalid_request';action='Correct parameters using schema/available fields; use query_design_structure(view="hierarchy") or query_pin_catalog when needed.'
    else:
        category='execution_error';action='Inspect the execution/import diagnostic; without factual output retain UNKNOWN.'
    value.update(gap_category=category,next_step=action)
    return value
