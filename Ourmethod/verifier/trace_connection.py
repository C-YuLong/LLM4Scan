"""Fixed OpenSTA electrical connectivity trace; never follows cell timing arcs."""
import base64
from collections import deque
import json

DESCRIPTION='''Use trace_connection when the question is whether a concrete input pin directly connects across hierarchy to a specified top input, including after query_cell_connections reports top_source_unconfirmed. Supply artifact, top, returned instance_path, actual pin, task-named expected_top_port, cursor=0; no Tcl is needed. Returns resolution_status=resolved/unresolved/query_error, actual_sources, directly_connected and ordered path rows. Only resolved yields a boolean; unresolved/query_error yields null, never false. It traverses wire/port boundaries, not cell logic. Multiple drivers are unresolved. A single trace is not full population coverage or task PASS. Copy next_read for saved path pages; inspect_check section must be JSON null.'''
GUIDE=DESCRIPTION+'''
Example shape (replace placeholders with observed objects and the task target):
{"artifact":"<registered netlist>","top":"<exact top>","instance_path":"<row.instance_path>","pin":"<row.pin>","expected_top_port":"<task-named top input>","cursor":0}
Use the same snapshot as the local connection row. A leaf's actual pin can differ from its wrapper's port; do not substitute guessed TE/SCE names. Compare selected before/after snapshots to establish unchanged exceptions. directly_connected=false means no direct wire connection to that target, not absence of functional dependency through logic. Missing/floating/ambiguous objects, limits and query errors never establish disconnection.
'''

# Trusted read-only primitive: object methods are never forwarded from model Tcl.
# pin_iterator + term_iterator cross explicit module boundaries; connected_pin_iterator
# enumerates electrical endpoints, unlike timing get_fanin (which can cross logic).
NATIVE_BRIDGE=r'''
namespace eval sta {
proc trace_emit {kind args} {
    set row [list TC $kind]
    foreach value $args {lappend row [binary encode base64 -maxlen 0 [encoding convertto utf-8 $value]]}
    puts [join $row "\t"]
}
proc bridge_trace_connection {wanted pin_name expected} {
    set cells {}
    foreach c [get_cells -quiet $wanted] {
        if {[get_full_name $c] eq $wanted} {lappend cells $c}
    }
    if {[llength $cells] != 1} {trace_emit GAP instance_not_unique; return}
    set pins {}
    foreach p [get_pins -of_objects [lindex $cells 0]] {
        if {[$p port_name] eq $pin_name} {lappend pins $p}
    }
    if {[llength $pins] != 1} {trace_emit GAP pin_not_unique; return}
    set pin [lindex $pins 0]
    if {[get_property $pin direction] ne "input"} {trace_emit GAP pin_not_input; return}
    set ports {}
    foreach p [get_ports -quiet $expected] {
        if {[get_full_name $p] eq $expected} {lappend ports $p}
    }
    if {[llength $ports] != 1} {trace_emit GAP target_port_not_unique; return}
    set port [lindex $ports 0]
    if {[$port is_bus] || [get_property $port direction] ne "input"} {
        trace_emit GAP target_not_scalar_input; return
    }
    set start [$pin net]
    if {$start eq "NULL"} {trace_emit GAP pin_unconnected; return}
    trace_emit START [get_full_name $start]
    set sources 0
    set count 0
    set iter [$start connected_pin_iterator]
    try {
        while {[$iter has_next]} {
            set p [$iter next]
            incr count
            if {$count > 200000} {trace_emit GAP endpoint_limit; return}
            if {[$p is_hierarchical]} {continue}
            if {[$p is_driver]} {
                incr sources
                if {$sources > 64} {trace_emit GAP source_limit; return}
                set kind cell_output
                if {[$p is_top_level_port]} {set kind top_port}
                trace_emit SOURCE $kind [get_full_name $p] [get_property $p direction]
                set attached [$p net]
                if {$attached eq "NULL" && [$p term] ne "NULL"} {set attached [[$p term] net]}
                if {$attached ne "NULL"} {trace_emit ATTACH [get_full_name $p] [get_full_name $attached]}
            }
        }
    } finally {$iter finish}
    set queue [list $start]
    set seen [dict create $start 1]
    set pos 0
    while {$pos < [llength $queue]} {
        set net [lindex $queue $pos]
        incr pos
        set name [get_full_name $net]
        trace_emit NET $name
        if {[$net is_power]} {trace_emit CONST $name 1; trace_emit ATTACH 1 $name}
        if {[$net is_ground]} {trace_emit CONST $name 0; trace_emit ATTACH 0 $name}
        set links {}
        set iter [$net pin_iterator]
        try {
            while {[$iter has_next]} {
                set p [$iter next]
                set term [$p term]
                if {$term ne "NULL"} {lappend links [list [$term net] [get_full_name $p]]}
            }
        } finally {$iter finish}
        set iter [$net term_iterator]
        try {
            while {[$iter has_next]} {
                set term [$iter next]
                set p [$term pin]
                lappend links [list [$p net] [get_full_name $p]]
            }
        } finally {$iter finish}
        foreach link $links {
            lassign $link next boundary
            if {$next eq "NULL"} {continue}
            trace_emit EDGE $name [get_full_name $next] $boundary
            if {![dict exists $seen $next]} {
                dict set seen $next 1
                if {[dict size $seen] > 4096} {trace_emit GAP net_limit; return}
                lappend queue $next
            }
        }
    }
    trace_emit DONE
}
}
'''


def native_script(instance_path, pin, expected_top_port):
    def word(value):
        return '[_utf8 decode [binary decode base64 {'+base64.b64encode(value.encode()).decode()+'}]]'
    return '_trace_connection '+' '.join(word(v) for v in (instance_path,pin,expected_top_port))


def resolve(native, expected_top_port):
    base=dict(resolution_status='query_error',complete=False,directly_connected=None,
              actual_sources=[],source_is_unique=False,rows=[],unresolved_reason=None)
    if native.get('status')!='FACT' or native.get('complete') is not True:
        return dict(base,unresolved_reason='Native OpenSTA query failed',error_type=native.get('error_type') or 'OpenSTAQueryError')
    try:
        starts=[];sources=[];edges=[];nets=set();constants=set();gaps=[];done=0
        for line in native['output'].splitlines():
            if not line.startswith('TC\t'):continue
            _,kind,*fields=line.split('\t')
            values=[base64.b64decode(v,validate=True).decode('utf-8') for v in fields]
            if kind=='START':
                start,=values;starts.append(start)
            elif kind=='SOURCE':
                typ,name,direction=values;sources.append(dict(kind=typ,name=name,direction=direction))
            elif kind=='NET':
                name,=values;nets.add(name)
            elif kind=='CONST':
                name,value=values;constants.add((name,value))
            elif kind=='EDGE':
                a,b,pin=values;edges.append((a,b,pin))
            elif kind=='ATTACH':
                name,net=values
            elif kind=='GAP':
                reason,=values;gaps.append(reason)
            elif kind=='DONE':
                if values:raise ValueError('Unexpected completion fields')
                done+=1
            else:raise ValueError('Unknown trace record')
        if gaps:return dict(base,resolution_status='unresolved',unresolved_reason='; '.join(gaps))
        if len(starts)!=1 or done!=1 or starts[0] not in nets:raise ValueError('Incomplete native trace protocol')
        # Deduplicate constants on aliases of the same connected component.
        values={v for _,v in constants}
        for value in sorted(values):sources.append(dict(kind='constant',name=value,direction=None))
        unique={(s['kind'],s['name']):s for s in sources};sources=[unique[k] for k in sorted(unique)]
        base.update(actual_sources=sources,source_is_unique=len(sources)==1)
        graph={n:[] for n in nets}
        for a,b,pin in edges:
            if a not in nets or b not in nets:raise ValueError('Trace edge outside enumerated component')
            graph[a].append((b,pin));graph[b].append((a,pin))
        previous={starts[0]:None};queue=deque([starts[0]])
        while queue:
            a=queue.popleft()
            for b,pin in sorted(graph[a]):
                if b not in previous:previous[b]=(a,pin);queue.append(b)
        if set(previous)!=nets:raise ValueError('Incomplete trace graph')
        if len(sources)!=1:
            return dict(base,resolution_status='unresolved',unresolved_reason='floating_connection' if not sources else 'multiple_sources')
        source=sources[0]
        if source['direction'] not in (None,'input' if source['kind']=='top_port' else 'output'):
            return dict(base,resolution_status='unresolved',unresolved_reason='bidirectional_source')
        return _finish(base,previous,source,expected_top_port,native)
    except (ValueError,KeyError,TypeError,UnicodeError) as exc:
        return dict(base,resolution_status='query_error',unresolved_reason=str(exc),error_type='TraceProtocolError')


def _finish(base,previous,source,expected,native):
    # The connected endpoint itself is enough for direct membership. Explicit
    # boundary rows are filled using source attachment records from the primitive.
    attachment=None
    for line in native['output'].splitlines():
        if line.startswith('TC\tATTACH\t'):
            name,net=[base64.b64decode(v,validate=True).decode() for v in line.split('\t')[2:]]
            if name==source['name']:attachment=net
    if attachment is None or attachment not in previous:
        raise ValueError('Source attachment absent from traced component')
    rows=[];net=attachment
    while previous[net] is not None:
        parent,pin=previous[net];rows.append(dict(from_net=parent,via_pin=pin,to_net=net));net=parent
    rows.reverse()
    return dict(base,resolution_status='resolved',complete=True,directly_connected=source['kind']=='top_port' and source['name']==expected,
                rows=rows,source_net=attachment,local_net=net,unresolved_reason=None)


def preview(value,cursor,limit,page_rows):
    base={k:v for k,v in value.items() if k not in {'native','rows','parameters','next_read','partial','omitted'}}
    rows=value['rows']
    if cursor<0 or cursor>len(rows):raise ValueError('Trace cursor outside saved path')
    page=rows[cursor:cursor+page_rows]
    def answer():
        end=cursor+len(page)
        return dict(base,rows=page,total_rows=len(rows),cursor=cursor,omitted=end<len(rows) or cursor>0,
                    next_cursor=end if end<len(rows) else None,
                    next_read=dict(tool='trace_connection',arguments={**value['parameters'],'cursor':end}) if end<len(rows) else None)
    while page and len(json.dumps(answer(),ensure_ascii=False))>limit:page.pop()
    if len(json.dumps(answer(),ensure_ascii=False))>limit or (not page and cursor<len(rows)):
        raise ValueError('Trace metadata/path exceeds page budget')
    return answer()
