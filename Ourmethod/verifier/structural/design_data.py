"""Yosys JSON hierarchy index. No user code, flatten-name heuristics or net-ID comparisons."""
from hashlib import sha256
import json
import re
from pathlib import Path
import subprocess
import time


class DesignUnavailable(ValueError): pass


class QueryInvalid(ValueError):
    def __init__(self, code, message, available=None):
        self.code, self.available = code, available or {}
        super().__init__(message)


def yosys_index(netlist, libraries, top, work, deadline):
    paths=[Path(p) for p in [netlist,*libraries]]
    if re.search(r'`\s*include\b',paths[0].read_text(encoding='utf-8')):
        raise DesignUnavailable('External Verilog includes are not registered analysis sources')
    digest=sha256(json.dumps([top]+[sha256(p.read_bytes()).hexdigest() for p in paths]).encode()).hexdigest()
    destination=Path(work)/digest; destination.mkdir(parents=True,exist_ok=True)
    output=destination/'design.json'
    if not output.exists():
        # Copy to fixed safe names: Yosys never sees model strings as command syntax.
        for i,p in enumerate(paths): (destination/f'source_{i}').write_bytes(p.read_bytes())
        script='\n'.join([f'read_liberty -lib source_{i}' for i in range(1,len(paths))]+[
            'read_verilog source_0','hierarchy -check','write_json design.pending.json'])
        (destination/'read.ys').write_text(script)
        timeout=deadline-time.monotonic()
        if timeout<=0: raise TimeoutError('Design query deadline exhausted')
        try:
            result=subprocess.run(['yosys','-Q','-T','-s','read.ys'],cwd=destination,
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        except subprocess.TimeoutExpired as exc: raise TimeoutError('Yosys deadline exhausted') from exc
        except OSError as exc: raise DesignUnavailable('Yosys unavailable') from exc
        (destination/'yosys.log').write_bytes(result.stdout)
        pending=destination/'design.pending.json'
        if result.returncode or not pending.exists(): raise DesignUnavailable('Yosys failed; see analysis log')
        pending.replace(output)
    return build_index(json.loads(output.read_text())['modules'],top,deadline)


def build_index(modules, top, deadline=float('inf')):
    if top not in modules: raise DesignUnavailable('Unknown top')
    parents={}; aliases={}; cells={}; ports={}; hierarchy=[]; boundaries=[]; definitions={}
    def root(x):
        while x in parents and parents[x]!=x:
            parents[x]=parents[parents[x]]; x=parents[x]
        return x
    def node(path,bit):
        value=('constant',bit) if isinstance(bit,str) else (path,bit)
        parents.setdefault(value,value); return value
    def union(a,b):
        a,b=root(a),root(b)
        if a!=b: parents[b]=a
    active=[]
    def visit(module,path,bindings=None):
        if module in active: raise DesignUnavailable('Recursive hierarchy')
        active.append(module); data=modules[module]
        hierarchy.append(dict(path=list(path),module=module))
        for name,port in data.get('ports',{}).items():
            nodes=[node(path,b) for b in port['bits']]
            if path:
                boundaries.append(dict(path=path,module=module,port=name,direction=port['direction'],bits=nodes))
            if bindings and name in bindings and bindings[name]:
                if len(nodes)!=len(bindings[name]): raise DesignUnavailable('Port width mismatch: '+repr((path,name,len(nodes),len(bindings[name]))))
                for a,b in zip(nodes,bindings[name]): union(a,b)
            if not path:
                ports[name]=dict(direction=port['direction'],bits=nodes)
                for i,value in enumerate(nodes):
                    if port['direction']=='input': aliases.setdefault(value,set()).add(('port',name,i))
        for name,net in data.get('netnames',{}).items():
            for i,bit in enumerate(net['bits']): aliases.setdefault(node(path,bit),set()).add((path,name,i))
        for name,cell in data.get('cells',{}).items():
            if time.monotonic()>=deadline: raise TimeoutError('Hierarchy indexing deadline exhausted')
            child=cell['type']; child_path=path+(name,)
            connections={pin:[node(path,b) for b in bits] for pin,bits in cell.get('connections',{}).items()}
            definition=modules.get(child)
            if definition is None: raise DesignUnavailable('Unknown cell type')
            if definition.get('attributes',{}).get('blackbox') or definition.get('attributes',{}).get('whitebox'):
                cells[child_path]=dict(type=child,connections=connections)
                definitions[child]={pin:dict(direction=value.get('direction',''),width=len(value['bits']))
                                    for pin,value in definition.get('ports',{}).items()}
                for pin,bits in connections.items():
                    direction=definition.get('ports',{}).get(pin,{}).get('direction')
                    if direction=='output':
                        for i,value in enumerate(bits): aliases.setdefault(value,set()).add(('driver',child_path,pin,i))
            else: visit(child,child_path,connections)
        active.pop()
    visit(top,())
    nets={}
    for key,names in aliases.items(): nets.setdefault(root(key),set()).update(names)
    # Constants can be merged through module ports; retain their identity explicitly.
    for key in parents:
        if key[0]=='constant': nets.setdefault(root(key),set()).add(('constant',key[1]))
    for values in nets.values():
        if len({v for v in values if v[0]=='constant'})>1: raise DesignUnavailable('Conflicting constant connections')
    for cell in cells.values(): cell['connections']={p:[nets.get(root(b),set()) for b in bits] for p,bits in cell['connections'].items()}
    for port in ports.values(): port['bits']=[nets.get(root(b),set()) for b in port['bits']]
    # Discover wrapper aliases in the module's own symbolic nets BEFORE binding
    # its ports to an outside constant. Global constant folding would otherwise
    # falsely map TE to every unrelated tied-low pin inside the wrapper.
    terminals={}
    def module_terminals(module):
        if module in terminals: return terminals[module]
        data=modules[module]; rows={}
        for name,cell in data.get('cells',{}).items():
            if time.monotonic()>=deadline: raise TimeoutError('Wrapper mapping deadline exhausted')
            child=modules[cell['type']]
            leaf=child.get('attributes',{}).get('blackbox') or child.get('attributes',{}).get('whitebox')
            nested={} if leaf else module_terminals(cell['type'])
            for pin,bits in cell.get('connections',{}).items():
                child_bits=child.get('ports',{}).get(pin,{}).get('bits',[])
                for i,bit in enumerate(bits):
                    endpoints=[((name,),cell['type'],pin,i)] if leaf else [
                        ((name,)+p,t,q,j) for p,t,q,j in nested.get(child_bits[i],[])] if i<len(child_bits) else []
                    rows.setdefault(bit,[]).extend(endpoints)
        terminals[module]=rows
        return rows
    mappings=[]
    for boundary in boundaries:
        data=modules[boundary['module']]
        local=module_terminals(boundary['module'])
        for index,bit in enumerate(data['ports'][boundary['port']]['bits']):
            if isinstance(bit,str): continue
            endpoints=local.get(bit,[])
            for relative,typ,pin,pin_bit in endpoints:
                mappings.append(dict(wrapper_path=list(boundary['path']),module=boundary['module'],
                    port=boundary['port'],port_bit=index,width=len(boundary['bits']),
                    leaf_path=list(boundary['path']+relative),leaf_type=typ,pin=pin,pin_bit=pin_bit,
                    relation='direct_wire'))
            if not endpoints:
                mappings.append(dict(wrapper_path=list(boundary['path']),module=boundary['module'],
                    port=boundary['port'],port_bit=index,width=len(boundary['bits']),
                    leaf_path=[],leaf_type='',pin='',pin_bit=0,relation='unresolved'))
    return dict(cells=cells,ports=ports,hierarchy=hierarchy,pin_definitions=definitions,port_mappings=mappings,
                coverage=dict(complete=True,unknowns=[]))


def path_matches(path,prefix):
    return path[:len(prefix)]==tuple(prefix)


def select_cells(design,selector, *, validate=False, known_types=None):
    if validate:
        types=known_types if known_types is not None else {cell['type'] for cell in design['cells'].values()}
        if set(selector['types'])-types:
            raise QueryInvalid('unknown_cell_type','Unknown selected cell types',{'types':sorted(types)})
        paths={path[:n] for path in design['cells'] for n in range(1,len(path)+1)}
        for path in selector['include']+selector['exclude']:
            if tuple(path) not in paths:
                raise QueryInvalid('unknown_hierarchy','Unknown hierarchy scope',{'path':path})
    result={}
    for path,cell in design['cells'].items():
        if selector['types'] and cell['type'] not in selector['types']: continue
        if selector['include'] and not any(path_matches(path,p) for p in selector['include']): continue
        if any(path_matches(path,p) for p in selector['exclude']): continue
        pin=selector['constant_pin']
        if validate and pin and pin not in cell['connections']:
            raise QueryInvalid('unknown_pin',f'{cell["type"]} has no pin {pin}',
                               {'type':cell['type'],'pins':sorted(cell['connections'])})
        if pin and not all(('constant',selector['constant_value']) in bit for bit in cell['connections'].get(pin,[])): continue
        if pin and not cell['connections'].get(pin): continue
        result[path]=cell
    return result


def same_bits(a,b):
    if len(a)!=len(b): return False
    unresolved=False
    for x,y in zip(a,b):
        cx={v for v in x if v[0]=='constant'};cy={v for v in y if v[0]=='constant'}
        if cx or cy:
            if cx!=cy: return False
            continue
        # Drivers and top inputs establish connectivity across independent parses.
        # Local wire/port aliases alone can survive an upstream reconnection.
        ax={v for v in x if v[0] in ('port','driver')}
        ay={v for v in y if v[0] in ('port','driver')}
        if ax and ay:
            if ax!=ay: return False
        else: unresolved=True
    return None if unresolved else True
