"""Hierarchical queries over Yosys JSON with module-local connection coordinates."""
from copy import deepcopy
from hashlib import sha256
import json
import time
from .design_data import build_index, DesignUnavailable
from .cell_catalog import CellCatalog
from .schemas import QueryResult, failure


class NetlistDB:
    def __init__(self, document, top, *, catalog=None, evidence=None, deadline=float('inf')):
        self.deadline = deadline
        self.document = deepcopy(document)
        self.modules = self.document['modules']
        self.top = top
        self.catalog = catalog or CellCatalog.sky130()
        self.evidence = evidence or []
        self.index = build_index(self.modules, top, deadline)
        self.instances = {}
        self.scopes = {''}
        self._visit(top, ())
        self.design_id = sha256(json.dumps(self.document, sort_keys=True).encode() + top.encode()).hexdigest()

    def _visit(self, module, scope):
        for name, cell in sorted(self.modules[module].get('cells', {}).items()):
            if time.monotonic() >= self.deadline:
                raise TimeoutError('Hierarchy indexing deadline exhausted')
            if '/' in name:
                raise DesignUnavailable('Instance names containing / need escaped-path support')
            path = scope + (name,)
            definition = self.modules[cell['type']]
            leaf = bool(definition.get('attributes', {}).get('blackbox') or
                        definition.get('attributes', {}).get('whitebox'))
            key = '/'.join(path)
            self.instances[key] = {'path': key, 'type': cell['type'], 'leaf': leaf,
                'parent_scope': '/'.join(scope), 'parent_module': module,
                'connections': deepcopy(cell.get('connections', {})),
                'port_directions': {p: d.get('direction', '') for p, d in definition.get('ports', {}).items()}}
            self.scopes.add(key)
            if not leaf:
                self._visit(cell['type'], path)

    @classmethod
    def from_json(cls, document, top, **kwargs):
        try:
            return QueryResult('ok', cls(document, top, **kwargs))
        except TimeoutError as exc:
            return failure('index_timeout', exc)
        except (ValueError, KeyError, TypeError, RecursionError) as exc:
            return failure('invalid_netlist_json', exc)

    @classmethod
    def load_netlist(cls, runner, path, liberty_paths, top=None, *, catalog=None):
        deadline = time.monotonic() + runner.timeout
        parsed = runner.load_netlist(path, liberty_paths, top)
        if parsed.status != 'ok':
            return parsed
        result = cls.from_json(parsed.data['document'], parsed.data['identity']['top'],
                               catalog=catalog, evidence=parsed.evidence, deadline=deadline)
        if result.status == 'ok':
            from .library_facts import library_metadata
            metadata = {}
            for item in parsed.evidence[1:]:
                from pathlib import Path
                for name, record in library_metadata(Path(item['source']).read_text()).items():
                    if name in metadata and metadata[name] != record:
                        record = {**record, 'role': 'unknown', 'unknowns': ['Conflicting library definitions']}
                    metadata[name] = record
            result.data.index['library'] = metadata
            if catalog is None:
                roles = {'ordinary_ff': 'DFF', 'scan_ff': 'SFF', 'icg': 'ICG',
                         'latch': 'latch', 'combinational': 'combinational'}
                result.data.catalog = CellCatalog({name: {'role': roles[r['role']]} for name, r in metadata.items()
                                                  if r['role'] in roles})
        return result

    def _ok(self, data):
        return QueryResult('ok', data, deepcopy(self.evidence))

    def get_instances(self, scope='', recursive=True, type_filter=None):
        if scope not in self.scopes:
            return failure('unknown_scope', scope)
        prefix = scope + '/' if scope else ''
        rows = [deepcopy(row) for path, row in sorted(self.instances.items())
                if path.startswith(prefix) and path != scope and
                (recursive or row['parent_scope'] == scope) and
                (type_filter is None or row['type'] in type_filter)]
        return self._ok({'instances': rows, 'count': len(rows), 'complete': True})

    def get_cell_type(self, instance_path):
        row = self.instances.get(instance_path)
        if row is None:
            return failure('unknown_instance', instance_path)
        role = self.catalog.lookup(row['type'])
        return self._ok({'type': row['type'], 'classification': role.to_dict()})

    def count_cells(self, scope='', type_filter=None):
        rows = self.get_instances(scope, type_filter=type_filter)
        if rows.status != 'ok':
            return rows
        return self._ok({'count': sum(row['leaf'] for row in rows.data['instances']),
                         'population': 'leaf_cells', 'complete': True})

    def get_pin_connection(self, instance_path, pin):
        row = self.instances.get(instance_path)
        if row is None:
            return failure('unknown_instance', instance_path)
        if pin not in row['connections']:
            return failure('unknown_pin', pin)
        local = row['connections'][pin]
        # Existing index unifies only direct wires across module port bindings.
        cell = self.index['cells'].get(tuple(instance_path.split('/')))
        traced = cell['connections'].get(pin) if cell else None
        bits = []
        for i, bit in enumerate(local):
            aliases = traced[i] if traced is not None else set()
            bits.append({'local_bit': bit, 'module_scope': row['parent_scope'],
                'constants': sorted(v[1] for v in aliases if v[0] == 'constant'),
                'top_inputs': sorted([v[1], v[2]] for v in aliases if v[0] == 'port'),
                'leaf_outputs': sorted([list(v[1]), v[2], v[3]] for v in aliases if v[0] == 'driver')})
        return self._ok({'instance': instance_path, 'pin': pin, 'width': len(local),
                         'direction': row['port_directions'].get(pin, ''), 'bits': bits,
                         'trace_method': 'direct_wire_only',
                         'trace_complete': traced is not None})

    def get_nets(self, scope=''):
        """Local nets are scoped coordinates, never global numeric net IDs."""
        if scope:
            row = self.instances.get(scope)
            if row is None or row['leaf']:
                return failure('unknown_module_scope', scope)
            module = row['type']
        else:
            module = self.top
        return self._ok({'scope': scope, 'module': module, 'nets': [
            {'name': name, 'bits': list(net['bits']), 'width': len(net['bits'])}
            for name, net in sorted(self.modules[module].get('netnames', {}).items())]})
