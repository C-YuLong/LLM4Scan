"""Explicit, overridable roles; unknown names never imply a hardware role."""
from copy import deepcopy
from .schemas import QueryResult


class CellCatalog:
    def __init__(self, entries=None):
        self.entries = deepcopy(entries or {})

    @classmethod
    def sky130(cls):
        entries = {}
        for size in (1, 2, 4):
            entries[f'sky130_fd_sc_hd__sdlclkp_{size}'] = {
                'role': 'ICG', 'pins': {'clock': 'CLK', 'enable': 'GATE',
                                      'test': 'SCE', 'output': 'GCLK'}}
            for plain, scan in [('dfrtp', 'sdfrtp'), ('dfxtp', 'sdfxtp'),
                                ('dfstp', 'sdfstp'), ('edfxtp', 'sedfxtp')]:
                a, b = (f'sky130_fd_sc_hd__{name}_{size}' for name in (plain, scan))
                entries[a] = {'role': 'DFF', 'scan_type': b,
                              'pins': {'clock': 'CLK', 'data': 'D', 'output': 'Q'}}
                entries[b] = {'role': 'SFF', 'plain_type': a,
                              'pins': {'clock': 'CLK', 'data': 'D', 'output': 'Q',
                                       'scan_in': 'SCD', 'scan_enable': 'SCE'}}
        return cls(entries)

    def lookup(self, cell_type):
        value = self.entries.get(cell_type)
        return QueryResult('ok', deepcopy(value)) if value else QueryResult(
            'unsupported', {'role': 'UNKNOWN'},
            errors=[{'code': 'unknown_cell_type', 'message': cell_type}])
