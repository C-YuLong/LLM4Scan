"""Deterministic FF observations, without task eligibility decisions."""
from collections import Counter
import time
from .structural.design_data import same_bits
from .structural.proofs import scope_cells


def collect_ff_changes(before, after, scope, deadline):
    designs=(before.index,after.index)
    scoped=[scope_cells(designs[0],scope,d['cells']) for d in designs]
    roles={'ordinary_ff','scan_ff'}
    unknown_types=sorted({c['type'] for d,cells in zip(designs,scoped) for c in cells.values()
                          if d.get('library',{}).get(c['type'],{}).get('role','unknown')=='unknown'})
    populations=[{p:c for p,c in cells.items() if d.get('library',{}).get(c['type'],{}).get('role') in roles}
                 for d,cells in zip(designs,scoped)]
    rows=[];changes=Counter();connections=Counter();pairs=Counter()
    for path in sorted(set(populations[0])|set(populations[1])):
        if time.monotonic()>=deadline:raise TimeoutError('FF fact collection deadline exhausted')
        old,new=(cells.get(path) for cells in populations)
        change='added' if old is None else 'missing' if new is None else 'unchanged' if old['type']==new['type'] else 'type_changed'
        row=dict(instance='/'.join(path),before_type=old and old['type'],after_type=new and new['type'],change=change)
        changes[change]+=1;pairs[(row['before_type'],row['after_type'])]+=1
        for label,cell,design in zip(('before_role','after_role'),(old,new),designs):
            row[label]=design.get('library',{}).get(cell['type'],{}).get('role','unknown') if cell else None
        state='not_comparable';pin_facts=[]
        if old is not None and new is not None:
            metas=[d.get('library',{}).get(c['type'],{}) for d,c in zip(designs,(old,new))]
            skip={pin for meta in metas for pin,role in meta.get('scan_pins',{}).items() if role in {'test_scan_in','test_scan_enable'}}
            pins=(set(metas[0].get('pins',{}))|set(metas[1].get('pins',{})))-skip
            states=[]
            for pin in sorted(pins):
                a=old['connections'].get(pin);b=new['connections'].get(pin)
                equal=same_bits(a,b) if a is not None and b is not None else None
                current='preserved' if equal is True else 'changed' if equal is False else 'unresolved'
                states.append(current)
                if current!='preserved':pin_facts.append(dict(pin=pin,state=current,reason='Connection correspondence not established' if current=='unresolved' else 'Normal connection differs'))
            state=('changed' if 'changed' in states else 'unresolved' if 'unresolved' in states or not pins else 'preserved')
        row['normal_connections']=state
        if pin_facts:row['pin_observations']=pin_facts
        connections[state]+=1;rows.append(row)
    population_complete=not unknown_types and all(d.get('coverage',{}).get('complete',True) for d in designs)
    return dict(status='FACT',proof=False,complete=population_complete and not connections['unresolved'],rows=rows,
                values=[r['instance'] for r in rows],
                observed=dict(before_ff=len(populations[0]),after_ff=len(populations[1]),population_complete=population_complete,
                              changes=dict(changes),normal_connections=dict(connections),
                              type_pairs=[dict(before_type=a,after_type=b,count=n) for (a,b),n in pairs.items()]),
                unknown_types=unknown_types,
                gaps=([dict(aspect='normal_connections',category='capability_unsupported',next_step='Unresolved correspondence is not preserved connectivity; retain a gap if that proof is required.')] if connections['unresolved'] else []),
                gap_category='needs_interpretation',
                next_step='Interpret type changes and unchanged FFs against the task. Unchanged is not automatically an allowed exception. Connection unresolved is not proven preserved.',
                limitations='Role/type/population/connection observations only; no task eligibility or functional equivalence verdict.')
