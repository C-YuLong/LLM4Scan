"""Typed model views over immutable full evidence; no acceptance decisions."""
from copy import deepcopy
import json

SECTIONS=('changed','unchanged','unchanged.ordinary_ff','unchanged.scan_ff','unchanged.other',
          'added','missing','connections.changed','connections.unresolved','violations','unresolved',
          'records','findings','set_differences','duplicates','matched_files','missing_files','empty_files')


def grouped_data(record):
    """Return section -> actual stored objects, or None for ordinary query tools."""
    operation=record.get('operation');rows=record.get('rows',[])
    if operation=='ff_changes':
        groups={key:[] for key in ('changed','unchanged','unchanged.ordinary_ff','unchanged.scan_ff',
                                    'unchanged.other','added','missing','connections.changed','connections.unresolved')}
        for row in rows:
            key={'type_changed':'changed','unchanged':'unchanged','added':'added','missing':'missing'}.get(row.get('change'))
            if key:groups[key].append(row)
            if key=='unchanged':
                role=row.get('before_role')
                groups['unchanged.'+(role if role in {'ordinary_ff','scan_ff'} else 'other')].append(row)
            connection=row.get('normal_connections')
            if connection in {'changed','unresolved'}:groups['connections.'+connection].append(row)
        return groups
    if record.get('table') is not None:
        return {'records' if record.get('status')=='FACT' else 'findings':rows}
    if operation=='membership':
        bad=record.get('violations',[])
        return dict(set_differences=[r for r in bad if r.get('reason')!='duplicate'],duplicates=[r for r in bad if r.get('reason')=='duplicate'])
    if operation=='delivery':
        bad=record.get('violations',[])
        return dict(matched_files=rows,missing_files=[r for r in bad if r.get('reason')!='Empty candidate artifact'],
                    empty_files=[r for r in bad if r.get('reason')=='Empty candidate artifact'])
    structural=operation in {'icg_integrity','icg_rewire','ff_total','ff_region_plain','ff_mapping','no_scan_structure','unchanged'}
    if structural:
        details=record.get('observed',{}).get('details',{})
        unknown=details.get('unknown_items',[]) if isinstance(details,dict) else []
        # Some forward-only checks store reverse-stage observations as rows, not violations.
        return dict(violations=record.get('violations',[]),unresolved=unknown or (rows if record.get('status')=='UNKNOWN' else []))
    return None


def _length(value):
    return len(json.dumps(value,ensure_ascii=False,default=str))


def grouped_preview(record,cursor,page_rows,limit,section=None):
    groups=grouped_data(record)
    if groups is None:
        if section is not None:raise ValueError('$.section: this evidence has no classified sections; use section=null')
        return None
    if section is not None and section not in groups:
        raise ValueError(f'$.section: {section!r} unavailable for this evidence; allowed: {sorted(groups)}')
    base=deepcopy({k:v for k,v in record.items() if k not in {'rows','values','violations','omitted','partial','next_read'}})
    observed=base.get('observed',{})
    details=observed.pop('details',None)
    if isinstance(details,dict):
        observed['subchecks']=[dict(kind=s.get('kind'),status=s.get('status'),checked=s.get('checked_count'),
                                    violations=len(s.get('differences',[])),unknown=len(s.get('unknown_items',[]))) for s in details.get('subchecks',[])]
    if record.get('operation')=='ff_changes':
        base['interpretation']='Changes and connections are overlapping dimensions; do not add their counts. Unchanged is not automatically a violation. FACT is not task acceptance.'
    base['section']=section
    base['view_only']=True
    base['reading_hint']='Continue an individual section with its next_read arguments to inspect_check. Counts refer to full saved evidence; displayed items are a preview, not an independent proof.'
    selected=[section] if section is not None else [name for name in groups if name!='unchanged' or record.get('operation')!='ff_changes']
    leaves={}
    for name in selected:
        rows=groups[name]
        start=cursor if section is not None else 0
        if start>len(rows):raise ValueError(f'$.cursor: exceeds section {name} count {len(rows)}')
        amount=page_rows if section is not None or record.get("operation")!="ff_changes" else (0 if name=='changed' else 3)
        leaves[name]=dict(count=len(rows),items=list(rows[start:start+amount]),cursor=start)
        if name=='unresolved':
            count=record.get('observed',{}).get('unknown',0)
            if count>len(rows):leaves[name].update(reported_unknown=count,details_complete=False,details_unavailable=True)
            elif not rows and record.get('status')=='UNKNOWN':leaves[name].update(details_unavailable=True,details_complete=False)

    def render():
        result=deepcopy(base)
        for name,leaf in leaves.items():
            page=dict(leaf);more=page['cursor']+len(page['items'])<page['count']
            page['omitted']=more or page['cursor']>0
            page['next_cursor']=page['cursor']+len(page['items']) if more else None
            page['next_read']=dict(evidence_id=record.get('evidence_id'),section=name,cursor=page['next_cursor']) if more else None
            node=result
            parts=name.split('.')
            for part in parts[:-1]:node=node.setdefault(part,{})
            node[parts[-1]]=page
        if record.get('operation')=='ff_changes' and section is None:
            result['unchanged']['count']=len(groups['unchanged'])
        result['omitted']=any(leaf['cursor']>0 or leaf['cursor']+len(leaf['items'])<leaf['count'] or leaf.get('details_unavailable') for leaf in leaves.values())
        return result
    result=render()
    while _length(result)>limit and any(leaf['items'] for leaf in leaves.values()):
        biggest=max((leaf for leaf in leaves.values() if leaf['items']),key=lambda leaf:_length(leaf['items']))
        biggest['items'].pop();result=render()
    if _length(result)>limit:
        raise ValueError('Result limit too small for classified result metadata; increase max_tool_result_chars')
    if section is not None and cursor<len(groups[section]) and not leaves[section]['items']:
        raise ValueError('Section item exceeds result limit; cannot return an advancing page at this max_tool_result_chars')
    return result
