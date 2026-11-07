"""Conservative provenance of declared Task 1 stages, never semantic acceptance."""
from hashlib import sha256
from pathlib import Path
import json
import re

CMD=re.compile(r'\[\s*CMD-0034\]\s+@(\d+):\s*(.*)$')
ERROR=re.compile(r'\[ERROR\]|^Error:')


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def resolve_stage_declarations(stages, resolve):
    if stages is None:return None
    return [dict(item,command=resolve(item['command']),
                 export_command=resolve(item['export_command']) if item.get('export_command') is not None else None) for item in stages]


def build_stage_artifacts(script, log, declarations, source, run_dir):
    source,run_dir=Path(source).resolve(),Path(run_dir).resolve()
    lines=script.splitlines();log_lines=log.splitlines()
    blocks=[]
    for n,line in enumerate(log_lines,1):
        m=CMD.search(line)
        if m:blocks.append(dict(command=m[2].strip(),log_line=n,script_line=int(m[1])))
    for i,b in enumerate(blocks):
        stop=blocks[i+1]['log_line']-1 if i+1<len(blocks) else len(log_lines)
        b['errors']=[dict(line=n,text=log_lines[n-1]) for n in range(b['log_line']+1,stop+1) if ERROR.search(log_lines[n-1])]
        b['export_success']=any('VRLG-O-0009' in log_lines[n-1] for n in range(b['log_line']+1,stop+1))
    active=[(n,line.strip()) for n,line in enumerate(lines,1) if line.strip() and not line.lstrip().startswith('#')]
    # Only straight-line commands are associated; no Tcl control-flow evaluator.
    unsafe=any(re.match(r'^(?:if|elseif|else|foreach|for|while|proc|source|eval|uplevel|switch|catch)\b',line) or ';' in line or line.endswith('\\') for _,line in active)

    def location(command):
        hits=[n for n,line in active if line==command.strip()] if command else []
        observed=[b for b in blocks if command and b['command']==command.strip()]
        return dict(script_lines=hits,log_lines=[b['log_line'] for b in observed],
                    observed=len(observed)==1,errors=[e for b in observed for e in b['errors']]),observed

    def path_of(command,verb):
        if not command or not command.startswith(verb+' '):return None
        tail=command[len(verb):].strip()
        if verb=='dump_netlist':
            m=re.fullmatch(r'-file\s+(.+)',tail)
            if not m:return None
            tail=m[1].strip()
        join=re.fullmatch(r'\[file join \$input_dir ([\w./ -]+)\]',tail)
        if join:tail=str(source.joinpath(*join[1].split()))
        else:
            tail=tail.strip('"{}')
            for variable,root in (('input_dir',source),('out_res_dir',run_dir/'deliverables'),('out_rpt_dir',run_dir/'reports')):
                tail=tail.replace('${'+variable+'}',str(root)).replace('$'+variable,str(root))
            if any(c in tail for c in '$[];\n'):return None
        path=Path(tail)
        if not path.is_absolute():return None
        path=path.resolve()
        if path.suffix.lower() not in {'.v','.sv'}:return None
        if path.is_relative_to(source):key='input/'+path.relative_to(source).as_posix()
        elif path.is_relative_to(run_dir/'deliverables'):key='run/'+path.relative_to(run_dir).as_posix()
        else:return None
        if not path.is_file() or not path.stat().st_size:return None
        return dict(artifact_id=key,sha256=digest(path))

    snapshots=[]
    exports={}
    for n,line in active:
        if line.startswith('dump_netlist '):
            snapshot=path_of(line,'dump_netlist')
            if snapshot:exports.setdefault(snapshot['artifact_id'],[]).append(n)
        elif line.startswith('load_netlist '):
            snapshot=path_of(line,'load_netlist')
            if snapshot and snapshot['artifact_id'].startswith('input/'):
                loc,seen=location(line)
                if len(loc['script_lines'])==1 and len(seen)==1 and not loc['errors']:
                    snapshots.append(dict(snapshot,line=n,log_line=seen[0]['log_line']))
    for n,line in active:
        if line.startswith('dump_netlist '):
            snapshot=path_of(line,'dump_netlist');loc,seen=location(line)
            if snapshot and len(exports[snapshot['artifact_id']])==1 and len(loc['script_lines'])==1 and len(seen)==1 and not loc['errors'] and seen[0]['export_success']:
                snapshots.append(dict(snapshot,line=n,log_line=seen[0]['log_line']))
    rows=[]
    ids=[d.get('id') for d in declarations]
    for d in declarations:
        command=d.get('command','');export=d.get('export_command');loc,seen=location(command);outloc,outseen=location(export)
        problems=[];before=None;after=None;intervening=[]
        if unsafe:problems.append('Unsupported Tcl control flow or compound commands; association uncertain')
        if ids.count(d.get('id'))!=1:problems.append('Duplicate stage ID')
        if len(loc['script_lines'])!=1 or len(seen)!=1:problems.append('Operation command is absent or not uniquely located')
        if loc['errors']:problems.append('Operation has logged errors')
        if len(loc['script_lines'])==1 and len(seen)==1:
            previous=[s for s in snapshots if s['line']<loc['script_lines'][0] and s['log_line']<seen[0]['log_line']]
            if previous:
                snap=max(previous,key=lambda x:x['line']);before={k:v for k,v in snap.items() if k in {'artifact_id','sha256'}}
                intervening=[n for n,line in active if snap['line']<n<loc['script_lines'][0] and not line.startswith('dump_netlist ')]
            else:problems.append('No verified preceding snapshot')
        if export is None:problems.append('No output snapshot declared')
        elif len(outloc['script_lines'])!=1 or len(outseen)!=1:problems.append('Export command is absent or not uniquely located')
        else:
            snapshot=path_of(export,'dump_netlist')
            if snapshot is None:problems.append('Output snapshot missing, empty, inaccessible, or unsupported path')
            elif len(exports.get(snapshot['artifact_id'],[]))!=1:problems.append('Snapshot path overwritten by multiple exports')
            elif outloc['errors'] or not outseen[0]['export_success']:problems.append('Export success not established')
            elif len(loc['script_lines'])==1 and len(seen)==1:
                between=[n for n,line in active if loc['script_lines'][0]<n<outloc['script_lines'][0] and not line.startswith('dump_netlist ')]
                if outloc['script_lines'][0]<=loc['script_lines'][0] or outseen[0]['log_line']<=seen[0]['log_line']:problems.append('Export is not after operation')
                elif between:problems.append('Other commands intervene before output export')
                else:after=snapshot
        if unsafe:before=None;after=None
        status='verified_source' if not problems else ('missing_snapshot' if any('snapshot' in p.lower() for p in problems) else 'uncertain')
        rows.append(dict(id=d.get('id'),label=d.get('label'),command=command,export_command=export,
                         before_snapshot=before,after_snapshot=after,operation=loc,export=outloc,
                         intervening_before_lines=intervening,status=status,issues=problems))
    return dict(schema_version=1,run_id=run_dir.name,script_sha256=sha256(script.encode()).hexdigest(),log_sha256=sha256(log.encode()).hexdigest(),scope='Observed provenance only; labels are generator intent, not semantic proof',stages=rows)


def model_stage_table(record, artifacts, run_id):
    if not record or record.get('run_id')!=run_id:
        return dict(available=False,reason='Stage information unavailable for this run',stages=[])
    for key,expected in ((f'run/deliverables/{run_id}.dofile',record.get('script_sha256')),
                         (f'run/{run_id}.log',record.get('log_sha256'))):
        if key not in artifacts or artifacts[key]['sha256']!=expected:
            return dict(available=False,reason='Stage script/log identity does not match this run',stages=[])
    rows=[]
    for stage in record.get('stages',[]):
        row={k:stage.get(k) for k in ('id','label','status')}
        issues=list(stage.get('issues',[]))
        for field in ('before_snapshot','after_snapshot'):
            snap=stage.get(field);item=artifacts.get(snap.get('artifact_id')) if snap else None
            valid=bool(item and item['sha256']==snap.get('sha256'))
            row[field]=snap['artifact_id'] if valid else None
            if snap and not valid:issues.append(f'{field}: unavailable or changed artifact')
        row['intervening_before']=bool(stage.get('intervening_before_lines'))
        row['script_line']=next(iter(stage.get('operation',{}).get('script_lines',[])),None)
        row['log_line']=next(iter(stage.get('operation',{}).get('log_lines',[])),None)
        if issues:row['issues']=issues;row['status']='uncertain'
        rows.append(row)
    return dict(available=True,source='run/stage_artifacts.json',scope='Provenance, not correctness; before_snapshot is the nearest saved state',stages=rows)
