"""Typed whole-report tables with conservative parse coverage."""
import re
from collections import Counter, defaultdict

HEADERS = {
    'chains': ('Chain', 'Length', 'Input', 'Output', 'ScanEnable', 'Clocks', 'Partition', 'ChainProperty'),
    'members': ('ChainName', 'CellNo', 'InstanceName', 'Clocks', 'ClockEdges', 'RefName'),
    'elements': ('Type', 'InstanceName', 'SiSoClocks', 'ClockEdge', 'ObjState'),
    'signals': ('SignalType', 'OffState'),
    'partitions': ('Partition', 'InstanceName', 'ByClock', 'ByClockEdge'),
}
ALIASES = {'Chain':'chain', 'ChainName':'chain', 'Length':'length', 'Input':'si', 'Output':'so',
           'ScanEnable':'enable', 'Clocks':'clock', 'Partition':'partition', 'CellNo':'index',
           'InstanceName':'instance', 'ClockEdges':'edge', 'RefName':'type', 'Type':'category',
           'PartitionName':'partition', 'SignalName':'signal', 'OffState':'off_state',
           'ConstantValue':'constant', 'OwnerPartition':'partition', 'SignalType':'role',
           'Scannability':'eligibility', 'ScanProperty':'eligibility', 'Property':'eligibility', 'ObjState':'eligibility', 'SiSoClocks':'clock', 'ClockEdge':'edge', 'Port':'signal', 'ByClock':'clock', 'ByClockEdge':'edge'}


def parse_report(text, table, source):
    lines=text.splitlines(); rows=[]; unknown=[]; recognized=False
    if table=='execution':
        recognized=True
        for n,line in enumerate(lines,1):
            match=re.search(r'\[\s*CMD-0034\]\s+@\d+:\s*set_scan_drc_rule_handling\s+(?:\{([^}]+)\}|(\S+))\s+(\w+)',line)
            if match:
                for rule in (match[1] or match[2]).split():
                    if not re.fullmatch(r'DFTR[A-Za-z0-9_-]+',rule): unknown.append(n)
                    rows.append(dict(rule=rule,handling=match[3],_source=source,_line=n))
        return dict(rows=rows,recognized=recognized,unknown=unknown)
    if table=='drc':
        for n,line in enumerate(lines,1):
            total=re.search(r'Total\s+violations\s*[:=]\s*(\d+)',line,re.I)
            if total: rows.append(dict(total=int(total[1]),_source=source,_line=n)); recognized=True
            rule=re.search(r"DRC rule '(DFTR[^']+)' fails(?:.*?\b(\d+)\b)?",line)
            if rule:
                count=re.search(r"There were (\d+) DRC rule",line)
                rows.append(dict(rule=rule[1],count=int(count[1]) if count else None,_source=source,_line=n)); recognized=True
        return dict(rows=rows, recognized=recognized, unknown=unknown)
    if table=='configuration':
        recognized=any(x in text for x in ('ScanConfigurationParameter','WrapperConfigurationParameter'))
        if recognized:
            partition=''
            for n,line in enumerate(lines,1):
                section=re.fullmatch(r'\s*Scan partition:\s*(\S+)\s*',line)
                if section: partition=section[1];continue
                if 'ConfigurationParameter' in line: continue
                match=re.fullmatch(r'\s*(\w+)\s+(?:[:=]\s*)?(\S+)\s*',line)
                if match: rows.append(dict(key=match[1],value=match[2],partition=partition,_source=source,_line=n))
        return dict(rows=rows,recognized=recognized,unknown=unknown)
    required=HEADERS.get(table)
    if required is None: return dict(rows=[],recognized=False,unknown=[])
    header=next(((n,line) for n,line in enumerate(lines) if all(re.search(r'\b'+re.escape(k)+r'\b',line) for k in required)),None)
    if header is None: return dict(rows=[],recognized=False,unknown=[])
    start,line=header; recognized=True
    columns=[(m.start(),m.group()) for m in re.finditer(r'\S+',line)]
    chain=None;partition=None
    for n,line in enumerate(lines[start+1:],start+2):
        if not line.strip() or re.fullmatch(r'[\s=-]+',line): continue
        if line.strip().startswith(('Design:', 'Total ', '[INFO]', '[WARN]')): continue
        raw={key:line[offset:(columns[i+1][0] if i+1<len(columns) else len(line))].strip()
             for i,(offset,key) in enumerate(columns)}
        row={ALIASES.get(k,k):v for k,v in raw.items()}
        if table=='members':
            chain=row.get('chain') or chain
            row['chain']=chain or ''
        if table=='partitions':
            partition=row.get('partition') or partition
            row['partition']=partition or ''
        if table=='signals' and 'role' in row:
            row['role_detail']=row['role'];row['role']=row['role'].split('(')[0]
        valid=True
        for key in ('length','index'):
            if key in row:
                try: row[key]=int(row[key])
                except ValueError: valid=False
        if table in ('members','elements','partitions') and not row.get('instance'): valid=False
        if table=='chains' and not row.get('chain'): valid=False
        if not valid: unknown.append(n); continue
        rows.append(dict(row,_source=source,_line=n))
    return dict(rows=rows,recognized=recognized,unknown=unknown)


def matches(row, filters):
    for condition in filters:
        value=row.get(condition['field']); expected=condition['value']; op=condition['op']
        if value is None: return False
        if op=='eq' and str(value)!=expected: return False
        if op=='ne' and str(value)==expected: return False
        if op=='prefix' and not str(value).startswith(expected): return False
        if op=='under' and str(value)!=expected and not str(value).startswith(expected.rstrip('/')+'/'): return False
        if op=='port_format' and ('%d' not in expected or re.fullmatch(re.escape(expected).replace('%d',r'\d+'),str(value)) is None): return False
        if op=='le':
            try:
                if int(value)>int(expected): return False
            except ValueError: return False
    return True


def run_assertion(query, tables):
    left=tables[query['table']]; selected=[r for r in left['rows'] if matches(r,query['filters'])]
    op=query['operation']; field=query['field']; problems=[]; unknown=len(left['unknown'])
    verdict='passed'; actual=None
    if any(condition['field'] not in row for row in left['rows'] for condition in query['filters']): unknown+=1
    if not left['recognized'] or unknown: verdict='unverified'
    if not selected and not query['allow_empty']: verdict='unverified'
    if op=='select': actual=len(selected)
    elif op=='count': actual=len(selected); problems=[] if str(actual)==query['expected'] else selected or [{'actual':actual}]
    elif op=='group':
        counts=Counter(str(r.get(field,'')) for r in selected); actual=dict(counts)
    elif op=='unique':
        counts=Counter(str(r.get(field,'')) for r in selected)
        problems=[r for r in selected if counts[str(r.get(field,''))]>1]
    elif op=='forall': problems=[r for r in selected if not matches(r,query['conditions'])]
    elif op in ('set_equal','subset','disjoint'):
        right=tables[query['right_table']]
        if not right['recognized'] or right['unknown']: verdict='unverified'; unknown+=len(right['unknown'])
        if any(condition['field'] not in row for row in right['rows'] for condition in query['right_filters']): verdict='unverified'; unknown+=1
        others=[r for r in right['rows'] if matches(r,query['right_filters'])]
        if not others and not query['allow_empty']: verdict='unverified'
        a={str(r.get(field,'')) for r in selected}; b={str(r.get(query['right_field'],'')) for r in others}
        delta=(a^b if op=='set_equal' else a-b if op=='subset' else a&b)
        problems=[{'value':v} for v in sorted(delta)]
        actual={'left':len(a),'right':len(b)}
    elif op=='chain_consistency':
        right=tables['members']
        if not right['recognized'] or right['unknown']: verdict='unverified'
        grouped=defaultdict(list)
        for member in right['rows']: grouped[member['chain']].append(member)
        for row in selected:
            group=grouped.get(row['chain'],[])
            if len(group)!=row['length'] or [r['index'] for r in group]!=list(range(row['length'])):
                problems.append(dict(chain=row['chain'],reason='length_or_indices'))
            for key,expect in (('clock',row.get('clock')),('edge',None)):
                values={r.get(key) for r in group}
                if key in query['conditions_fields'] and (len(values)!=1 or (expect and values!={expect})):
                    problems.append(dict(chain=row['chain'],reason=key))
        unexpected=set(grouped)-{r['chain'] for r in selected}
        if unexpected: problems.append(dict(unexpected_chains=sorted(unexpected)))
    elif op=='drc_policy':
        totals=[r['total'] for r in selected if 'total' in r]; rules={r['rule'] for r in selected if 'rule' in r}
        allowed=set(query['allowed_rules'])
        execution=tables.get('execution',dict(rows=[],recognized=False,unknown=[]))
        ignored={r['rule'] for r in execution['rows'] if r['handling']=='Ignore'}
        if set(query['ignored_rules']) != ignored:
            problems.append({'configured_ignored_rules':sorted(ignored),'expected':query['ignored_rules']})
        if not execution['recognized'] or execution['unknown']: verdict='unverified'
        counts=[r.get('count') for r in selected if 'rule' in r]
        if any(totals) and (len(totals)!=1 or not counts or any(c is None for c in counts) or sum(c for c in counts if c is not None)!=totals[0]): verdict='unverified'

        if not totals or (any(totals) and not rules): verdict='unverified'
        problems.extend({'rule':r} for r in sorted(rules-allowed))
        actual={'totals':totals,'residual_rules':sorted(rules)}
    if op=='forall' and any(c['field'] not in r for r in selected for c in query['conditions']): verdict='unverified'
    if op=='forall' and not query['conditions']: verdict='unverified'
    if op in ('set_equal','subset','disjoint') and any(query['right_field'] not in r for r in others): verdict='unverified'
    if op=='chain_consistency' and query['table']!='chains': verdict='unverified'
    if field and op in ('unique','group','set_equal','subset','disjoint') and any(field not in r for r in selected): verdict='unverified'
    if problems and verdict!='unverified': verdict='failed'
    if op in ('select','group'): verdict='unverified'  # facts, not acceptance assertions
    return dict(verdict=verdict,selected=len(selected),checked=len(selected),unknown=unknown,
                actual=actual,details=problems if op!='select' else selected)
