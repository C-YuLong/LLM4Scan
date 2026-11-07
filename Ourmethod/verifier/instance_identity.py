"""Canonical model identities; internal legacy evidence remains untouched."""
import base64
from copy import deepcopy

GUIDE='''Instance identity: instance_path is a concrete top-relative design path; module_type is the referenced module/library-cell type, never a path segment guessed as a type. containing_module_type is the parent declaration's type; instance_name is local. Native ref_name corresponds to module_type. Definition locations are artifact/hash/start/end, or null when unavailable.
For source navigation prefer read_verilog_blocks(artifact=<same snapshot>,top=<exact top>,instance_path=<returned instance_path>,kind="instance" or "module",module=null,name=null,cursor=0). The tool resolves actual types from the design. kind=instance reads the declaration in the actual parent module; kind=module reads the referenced type definition. Library-only definitions are explicitly reported; no Verilog position is invented.
Traditional source mode still uses module=<containing module TYPE>,name=<local declaration name>,top=null,instance_path=null. Unbound syntax declarations have instance_path=null because one module may be instantiated many times. Connection requests use concrete paths; type filters use types. Never substitute an instance name for a module type.
'''

def location(artifact,checksum,block):
    return dict(artifact=artifact,sha256=checksum,start=block['start'],end=block['end'])

def instance_script(path,top):
    def word(s):return '[_utf8 decode [binary decode base64 {'+base64.b64encode(s.encode()).decode()+'}]]'
    return 'set wanted '+word(path)+'\nset root_type '+word(top)+'\n'+r'''
set matches {}
foreach c [get_cells -quiet $wanted] {
    if {[get_full_name $c] eq $wanted} {lappend matches $c}
}
puts "IID_MATCH [llength $matches]"
if {[llength $matches] == 1} {
    set c [lindex $matches 0]
    set full [get_full_name $c]
    set slash [string last / $full]
    if {$slash < 0} {
        set local $full
        set parent_type $root_type
    } else {
        set local [string range $full [expr {$slash+1}] end]
        set parent_path [string range $full 0 [expr {$slash-1}]]
        set parents {}
        foreach p [get_cells -quiet $parent_path] {
            if {[get_full_name $p] eq $parent_path} {lappend parents $p}
        }
        if {[llength $parents] != 1} {error "Cannot uniquely resolve containing instance"}
        set parent_type [get_property [lindex $parents 0] ref_name]
    }
    foreach {key value} [list instance_path $full instance_name $local module_type [get_property $c ref_name] containing_module_type $parent_type] {
        puts "IID_FIELD $key [binary encode base64 -maxlen 0 [_utf8 encode $value]]"
    }
}
'''

def decode_instance(output):
    result={};matches=[]
    for line in output.splitlines():
        if line.startswith('IID_MATCH '):matches.append(int(line.split()[1]))
        elif line.startswith('IID_FIELD '):
            _,key,value=line.split(' ',2);result[key]=base64.b64decode(value,validate=True).decode()
    if matches!=[1] or set(result)!={'instance_path','instance_name','module_type','containing_module_type'}:
        raise ValueError('Instance path did not resolve to exactly one complete design identity')
    result['definition_location']=None
    return result

def canonical_row(row,operation,artifact,checksum):
    r=deepcopy(row)
    if operation=='connections' and isinstance(r.get('data'),dict):
        r['data']=canonical_row(r['data'],'instance',artifact,checksum);return r
    if operation=='read_verilog_blocks':
        kind=r.get('kind')
        if kind=='instance':
            r.setdefault('instance_path',None)
            r.setdefault('instance_name',r.get('name'))
            r.setdefault('module_type',r.get('cell_type'))
            r.setdefault('containing_module_type',r.get('module'))
        else:
            r.setdefault('instance_path',None);r.setdefault('instance_name',None)
            r.setdefault('module_type',r.get('name'));r.setdefault('containing_module_type',None)
        r.setdefault('declaration_location',location(artifact,checksum,r))
        r.setdefault('definition_location',r['declaration_location'] if kind=='module' else None)
        for key in ('name','module','cell_type'):r.pop(key,None)
        return r
    path=r.pop('path',r.pop('instance',None))
    if isinstance(path,(list,tuple)):path='/'.join(path)
    if path is not None:r.setdefault('instance_path',path)
    if 'instance_path' in r:
        r.setdefault('instance_name',r['instance_path'].rsplit('/',1)[-1])
        r.setdefault('containing_module_type',None)
        r.setdefault('definition_location',None)
    if 'type' in r:r.setdefault('module_type',r.pop('type'))
    if operation=='hierarchy' and 'module' in r:r.setdefault('module_type',r.pop('module'))
    return r

def model_value(value):
    op=value.get('operation')
    design=value.get('population_origin') in {'original_design','candidate_design'}
    if op not in {'query_cell_connections','read_verilog_blocks'} and not (design and op in {'select','hierarchy','connections','pin_catalog'}):return value
    v=dict(value);v.pop('identity_evidence',None)
    artifact=v.get('artifact');checksum=v.get('sha256')
    for key in ('rows','groups','unresolved','pin_summary','blocks'):
        if key in v:v[key]=[canonical_row(r,op,artifact,checksum) for r in v[key]]
    return v
