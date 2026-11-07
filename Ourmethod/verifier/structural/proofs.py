"""Copied structural proofs; independent of task contracts and worker loops."""
import re
import time
from .design_data import QueryInvalid, same_bits, path_matches


def scope_cells(design,scope,cells=None):
    paths={tuple(item['path']) for item in design.get('hierarchy',[])}
    paths.update(path[:n] for path in design['cells'] for n in range(1,len(path)+1))
    for path in scope['include']+scope['exclude']:
        if tuple(path) not in paths: raise QueryInvalid('unknown_hierarchy','Unknown hierarchy scope',{'path':path})
    return {p:c for p,c in (cells if cells is not None else design['cells']).items()
            if (not scope['include'] or any(path_matches(p,v) for v in scope['include']))
            and not any(path_matches(p,v) for v in scope['exclude'])}


def role_cells(design,roles):
    unknown={c['type'] for c in design['cells'].values()
             if design.get('library',{}).get(c['type'],{}).get('role','unknown')=='unknown'}
    if unknown: raise Incomplete('Unclassified library cell types: '+', '.join(sorted(unknown)))
    return {p:c for p,c in design['cells'].items() if design['library'][c['type']]['role'] in roles}


class Incomplete(ValueError): pass


def ff_signature_matches(old,new):
    if not old.get('signature_complete') or not new.get('signature_complete'): return None
    # Conservative syntactic signature: arbitrary Boolean equivalence is EQY's
    # future job. Different normal-function pin names stay unknown.
    normalize=lambda signature:{k:re.sub(r'\s+','',v) for k,v in signature.items()}
    a=normalize(old['functional_signature']);b=normalize(new['functional_signature'])
    # Extra scan-cell outputs are allowed only when unconnected (checked per
    # instance below); every old output must retain its exact polarity.
    return all(b.get(k)==v for k,v in a.items()) and all(k in a or k.startswith('output:') for k in b)


def scan_graph(design):
    ff=role_cells(design,{'ordinary_ff','scan_ff'})
    edges=set(); external=set(); unknown=[]
    for path,cell in ff.items():
        meta=design['library'][cell['type']]
        if meta['role']!='scan_ff': continue
        pins=[p for p,r in meta['scan_pins'].items() if r=='test_scan_in']
        if not pins: unknown.append(list(path));continue
        for pin in pins:
            if pin not in cell['connections']: unknown.append(list(path));continue
            for bit_index,bit in enumerate(cell['connections'][pin]):
                if any(v[0]=='constant' and v[1] in ('0','1') for v in bit): continue
                drivers={v for v in bit if v[0]=='driver'}
                ports={v for v in bit if v[0]=='port'}
                for alias in ports: external.add((alias[1],path,pin,bit_index))
                for driver in drivers:
                    if driver[1] in ff: edges.add((driver[1],driver[2],path,pin,bit_index))
                    else: unknown.append(list(path))
                if not drivers and not ports: unknown.append(list(path))
    return edges,external,unknown


def execute_structural(assertion,before,after,read_report,deadline=float('inf')):
    def tick():
        if time.monotonic()>=deadline: raise TimeoutError('Structural assertion deadline exhausted')
    tick()
    kind=assertion['kind']; differences=[]; unknown=0; selected={}; checked=0;unknown_items=[]
    if not before.get('coverage',{}).get('complete',True) or not after.get('coverage',{}).get('complete',True):
        raise Incomplete('Incomplete structural parse coverage')
    if kind.startswith('icg_'):
        selected=role_cells(before,{'icg'}); final=role_cells(after,{'icg'})
        if not selected: raise Incomplete('No baseline ICG population; establish applicability from task sources')
        if kind=='icg_integrity':
            for path in set(selected)^set(final): differences.append(dict(instance=list(path),reason='ICG set changed'))
            for path,old in selected.items():
                tick()
                new=after['cells'].get(path); checked+=1
                if not new: continue
                if old['type']!=new['type']: differences.append(dict(instance=list(path),reason='ICG type changed'));continue
                meta=before['library'][old['type']]
                for role in ('clock','enable'):
                    pin=meta['icg_pins'].get(role)
                    if not pin or pin not in old['connections'] or pin not in new['connections']:
                        raise Incomplete('Missing ICG role pin')
                    equal=same_bits(old['connections'][pin],new['connections'][pin])
                    if equal is None: unknown+=1
                    elif not equal: differences.append(dict(instance=list(path),pin=pin,reason='Connection changed'))
        else:
            scoped=scope_cells(before,assertion['scope'],selected); target=after['ports'].get(assertion['target_port'])
            if assertion['target_port'] not in before['ports']: raise QueryInvalid('unknown_port','Target port must exist in the baseline',{'ports':sorted(before['ports'])})
            if not target: return selected,0,[dict(port=assertion['target_port'],reason='Baseline target port removed')],0,{}
            eligible={}
            for path,cell in scoped.items():
                tick()
                pin=before['library'][cell['type']]['icg_pins'].get('test')
                if not pin or pin not in cell['connections']: raise Incomplete('Missing ICG test pin')
                if all(('constant',assertion['original_value']) in bit for bit in cell['connections'][pin]): eligible[path]=cell
            if not eligible and not assertion['empty_basis']:
                raise Incomplete('Empty target set needs explicit applicability evidence')
            for path,old in selected.items():
                tick()
                checked+=1;new=after['cells'].get(path)
                if not new: differences.append(dict(instance=list(path),reason='Missing ICG'));continue
                pin=before['library'][old['type']]['icg_pins']['test']
                if new['type']!=old['type']: differences.append(dict(instance=list(path),reason='ICG type changed'));continue
                if pin not in new['connections']: differences.append(dict(instance=list(path),reason='Missing test pin'));continue
                equal=(new['connections'][pin]==target['bits'] if path in eligible else
                       same_bits(old['connections'][pin],new['connections'][pin]))
                if equal is None: unknown+=1
                elif not equal: differences.append(dict(instance=list(path),pin=pin,
                    reason='Wrong target port' if path in eligible else 'Non-target connection changed'))
            return selected,checked,differences,unknown,dict(target_count=len(eligible),complement_count=len(selected)-len(eligible),
                                                            partition_complete=True)
    elif kind.startswith('ff_'):
        selected=role_cells(before,{'ordinary_ff','scan_ff'});final=role_cells(after,{'ordinary_ff','scan_ff'})
        if kind=='ff_total':
            checked=len(selected)
            if len(selected)!=len(final): differences.append(dict(before=len(selected),after=len(final),reason='FF total changed'))
        elif kind=='ff_region_plain':
            selected=scope_cells(before,assertion['scope'],selected)
            region=scope_cells(before,assertion['scope'],{p:c for p,c in final.items() if p in before['cells']})
            # Include additions under a real baseline scope as counterexamples.
            region.update({p:c for p,c in final.items() if p not in before['cells'] and
                           (not assertion['scope']['include'] or any(path_matches(p,v) for v in assertion['scope']['include'])) and
                           not any(path_matches(p,v) for v in assertion['scope']['exclude'])})
            if not selected and not assertion['empty_basis']: raise Incomplete('Empty baseline FF region')
            checked=len(selected)
            for path in set(selected)^set(region): differences.append(dict(instance=list(path),reason='Region FF set changed'))
            for path,cell in region.items():
                if after['library'][cell['type']]['role']!='ordinary_ff': differences.append(dict(instance=list(path),reason='Region contains scan FF'))
        else:
            from .ff_verification import check_ff_mapping
            return check_ff_mapping(assertion,before,after,read_report,deadline)
    elif kind=='no_scan_structure':
        selected=role_cells(before,{'ordinary_ff','scan_ff'});role_cells(after,{'ordinary_ff','scan_ff'})
        old_edges,old_ports,old_unknown=scan_graph(before);new_edges,new_ports,new_unknown=scan_graph(after)
        checked=len(after['ports'])+len(after['cells'])
        for edge in new_edges-old_edges: differences.append(dict(reason='New scan data cascade',edge=repr(edge)))
        added=set(after['ports'])-set(before['ports'])
        for port in set(before['ports'])-set(after['ports']): differences.append(dict(reason='Port removed',port=port))
        for port,path,pin,bit in new_ports-old_ports:
            differences.append(dict(reason='New scan data input port',port=port,instance=list(path),pin=pin))
        for port in added:
            definition=after['ports'][port]
            found=False
            for cell in after['cells'].values():
                meta=after['library'].get(cell['type'],{})
                for pin,role in meta.get('scan_pins',{}).items():
                    if (definition['direction']=='input' and role not in ('test_scan_in','test_scan_enable') or
                        definition['direction']=='output' and role!='test_scan_out'): continue
                    if any(bit and bit==target for bit in cell['connections'].get(pin,[]) for target in definition['bits']):
                        differences.append(dict(reason='New scan control/data port',port=port,pin=pin,role=role));found=True;break
                if found: break
        for port in set(before['ports'])&set(after['ports']):
            old=before['ports'][port];new=after['ports'][port]
            if (old['direction'],len(old['bits']))!=(new['direction'],len(new['bits'])):
                differences.append(dict(reason='Port signature changed',port=port))
        # New unexplained outputs or indirect scan-data routing cannot establish
        # absence. Existing scan FFs alone do not imply a newly constructed chain.
        unknown=len(new_unknown)+len(old_unknown)+len(added-{p[0] for p in new_ports})
    else: raise QueryInvalid('unknown_assertion','Unregistered structural assertion')
    tick()
    if not selected and kind not in ('no_scan_structure','ff_mapping','ff_region_plain'): raise Incomplete('Empty baseline population')
    return selected,checked,differences,unknown,dict(unknown_items=unknown_items)
