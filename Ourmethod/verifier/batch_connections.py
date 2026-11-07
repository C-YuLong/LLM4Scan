"""Deterministic native discovery chain; facts only, never task verdicts."""
import base64
from collections import Counter
import json

DESCRIPTION='''Use query_cell_connections to discover a complete scoped population of exact cell types and their actual pins/local nets. Supply artifact, top, types, exactly one of pin or pin_role (other null), include/exclude (null unrestricted), cursor=0. Roles come only from explicit Liberty attributes: functional_enable is normal gating; scan_test_enable is scan/test override. Never guess pin names. Returns counts, resolved/unresolved rows and saved pages; complete means local discovery only. If top_source_unconfirmed>0 and you need a specific top input connection, use trace_connection with a returned instance_path and pin plus the task-named expected_top_port. Paging or repeating this local query cannot complete cross-hierarchy tracing. Copy next_read for more local rows; use inspect_check with section=null (JSON null) to restore these records. FACT is not task PASS.'''
GUIDE=DESCRIPTION+'''
Routing: population/local pins -> query_cell_connections; concrete pin to top input -> trace_connection; before/after predicates -> compare_design; additional native facts not covered by those tools -> opensta_query.
Example role selector: pin=null,pin_role="scan_test_enable". Follow returned instance_path and pin rather than a guessed module type or wrapper pin. Query/role errors do not prove absence or disconnection. A single trace is not proof for every population member; choose coverage from the original requirement.
'''

def native_script(types,top):
    wanted=' '.join('[_utf8 decode [binary decode base64 {'+base64.b64encode(t.encode()).decode()+'}]]' for t in types)
    root='[_utf8 decode [binary decode base64 {'+base64.b64encode(top.encode()).decode()+'}]]'
    return 'set wanted [list '+wanted+']\nset root_type '+root+'\n'+r'''
proc b64 {value} {binary encode base64 -maxlen 0 [_utf8 encode $value]}
foreach c [get_cells -hierarchical *] {
    set type [get_property $c ref_name]
    if {[lsearch -exact $wanted $type] < 0} {continue}
    set path [get_full_name $c]
    puts "BQC_CELL\t[b64 $path]\t[b64 $type]"
    set parent_type $root_type
    set slash [string last / $path]
    if {$slash >= 0} {
        set parent_path [string range $path 0 [expr {$slash-1}]]
        set parent_type {}
        foreach parent [get_cells -quiet $parent_path] {
            if {[get_full_name $parent] eq $parent_path} {set parent_type [get_property $parent ref_name]}
        }
    }
    if {$parent_type ne {}} {puts "BQC_PARENT\t[b64 $path]\t[b64 $parent_type]"}
    foreach p [get_pins -of_objects $c] {
        set fullname [get_full_name $p]
        set pin [string range $fullname [expr {[string length $path]+1}] end]
        set nets [get_nets -of_objects $p]
        puts "BQC_PIN\t[b64 $path]\t[b64 $pin]"
        foreach net $nets {
            if {[catch {get_full_name $net} netname]} {
                puts "BQC_GAP\t[b64 $path]\t[b64 $pin]\t[b64 $netname]"
                continue
            }
            puts "BQC_NET\t[b64 $path]\t[b64 $pin]\t[b64 $netname]"
            foreach port [get_ports -of_objects $net] {
                if {![catch {get_full_name $port} portname]} {
                    puts "BQC_PORT\t[b64 $path]\t[b64 $pin]\t[b64 $portname]"
                    if {![catch {get_property $port direction} direction] && $direction eq "input"} {
                        puts "BQC_INPUT\t[b64 $path]\t[b64 $pin]\t[b64 $portname]"
                    }
                }
            }
        }
    }
}
puts BQC_DONE
'''

def decode_population(output):
    cells={};done=False
    for line in output.splitlines():
        if line=='BQC_DONE':done=True
        if not line.startswith(('BQC_CELL\t','BQC_PARENT\t','BQC_PIN\t','BQC_NET\t','BQC_PORT\t','BQC_INPUT\t','BQC_GAP\t')):continue
        fields=line.split('\t');kind=fields.pop(0)
        values=[base64.b64decode(v,validate=True).decode('utf-8') for v in fields]
        if kind=='BQC_CELL':
            path,typ=values;cells[path]=dict(type=typ,pins={},containing_module_type=None)
        elif kind=='BQC_PARENT':
            path,parent_type=values;cells[path]['containing_module_type']=parent_type
        elif kind=='BQC_PIN':
            path,pin=values;cells[path]['pins'][pin]=dict(nets=[],top_ports=[],top_inputs=[],diagnostics=[])
        else:
            path,pin,value=values
            field={'BQC_NET':'nets','BQC_PORT':'top_ports','BQC_INPUT':'top_inputs','BQC_GAP':'diagnostics'}[kind]
            cells[path]['pins'][pin][field].append(value)
    if not done:raise ValueError('Native population output incomplete')
    return cells

def resolve_pin(metadata,typ,pin,role):
    if pin is not None:return pin,'explicit pin name',None
    record=metadata.get(typ)
    if not record:return None,None,'No unique Liberty metadata for requested role'
    if any('Ambiguous' in s or 'Conflicting' in s for s in record.get('unknowns',[])):
        return None,None,'Conflicting or ambiguous Liberty pin attributes'
    icg_roles={'clock':'clock','functional_enable':'enable','out':'out','scan_test_enable':'test'}
    if role in icg_roles:
        resolved=record.get('icg_pins',{}).get(icg_roles[role])
    else:
        candidates=[p for p,r in record.get('scan_pins',{}).items() if r==role]
        resolved=candidates[0] if len(candidates)==1 else None
    return (resolved,'explicit Liberty pin-role attributes',None) if resolved else (None,None,'Requested role is absent or ambiguous in Liberty')

def collect(cells,metadata,request,scope_filter):
    include=request.get('include') or [];exclude=request.get('exclude') or []
    included=scope_filter(list(cells),include,[]);selected=scope_filter(list(cells),include,exclude)
    rows=[];unresolved=[]
    for path in selected:
        cell=cells[path];pin,basis,gap=resolve_pin(metadata,cell['type'],request.get('pin'),request.get('pin_role'))
        pins=cell['pins'];connection=pins.get(pin)
        if gap or connection is None:
            row=dict(instance=path,type=cell['type'],pin=pin,resolved=False,reason=gap or 'Requested pin absent on actual instance',available_pins=sorted(pins),nets=[],top_ports=[])
            unresolved.append(row)
        else:
            row=dict(instance=path,type=cell['type'],pin=pin,resolved=bool(connection['nets']),pin_basis=basis,
                     nets=sorted(set(connection['nets'])),top_ports=sorted(set(connection['top_ports'])),
                     top_inputs=sorted(set(connection['top_inputs'])),top_input_connected=bool(connection['top_inputs']),
                     trace_complete=False)
            if not row['resolved']:
                row['reason']='Actual pin has no connected net';unresolved.append(row)
        row['containing_module_type']=cell.get('containing_module_type')
        rows.append(row)
    groups=Counter((r['type'],r.get('pin'),tuple(r['nets']),tuple(r['top_ports'])) for r in rows if r['resolved'])
    summary=Counter((r['type'],r.get('pin')) for r in rows if r['resolved'])
    return dict(rows=rows,unresolved=unresolved,groups=[dict(type=t,pin=p,nets=list(n),top_ports=list(s),count=c) for (t,p,n,s),c in groups.items()],
                pin_summary=[dict(type=t,pin=p,count=c) for (t,p),c in summary.items()],
                observed=dict(matched=len(cells),included=len(included),excluded=len(included)-len(selected),selected=len(selected),
                              resolved=len(rows)-len(unresolved),unresolved=len(unresolved),
                              top_source_unconfirmed=len(rows)))

def preview(value,cursor,limit,page_rows):
    # Page resolved and unresolved rows together; full groups/native output stay on disk.
    rows=value['rows']
    if cursor<0 or cursor>len(rows):raise ValueError('Connection cursor outside saved population')
    base={k:v for k,v in value.items() if k not in {'rows','unresolved','groups','native','parameters','next_read','partial','omitted'}}
    page=[]
    def answer():
        end=cursor+len(page);more=end<len(rows)
        return dict(base,rows=list(page),total_rows=len(rows),cursor=cursor,omitted=more or cursor>0,
                    groups_saved=len(value['groups']),unresolved_saved=len(value['unresolved']),
                    next_cursor=end if more else None,next_read=dict(tool='query_cell_connections',arguments={**value['parameters'],'cursor':end}) if more else None,
                    limitations='Local nets are not cross-hierarchy source proof. Use trace_connection for a concrete pin and task-named top input; local pagination does not trace sources.',
                    source_trace_hint=dict(tool='trace_connection',when='Need to resolve a top_source_unconfirmed pin to a specified top input',
                        arguments_from=dict(artifact='same artifact',top='same top',instance_path='row.instance_path',pin='row.pin',expected_top_port='exact top input named by the task',cursor=0),
                        coverage='Each call traces one instance; do not generalize a sample to the whole population') if any(r.get('resolved') and not r.get('trace_complete') for r in page) else None)
    for row in rows[cursor:cursor+page_rows]:
        page.append(row)
        if len(json.dumps(answer(),ensure_ascii=False))>limit:
            page.pop();break
    if not page and cursor<len(rows):raise ValueError('Connection row exceeds page budget; narrow scope or increase result limit')
    result=answer()
    if len(json.dumps(result,ensure_ascii=False))>limit:raise ValueError('Connection metadata exceeds page budget')
    return result
