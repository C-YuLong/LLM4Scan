"""Bounded batch proofs. Unknown semantics and empty populations never pass silently."""
from pathlib import Path
from .design_data import same_bits
from .schemas import CheckResult


class Operations:
    def __init__(self, example_limit=20):
        if type(example_limit) is not int or example_limit < 0:
            raise ValueError('example_limit must be a nonnegative integer')
        self.example_limit = example_limit

    def result(self, kind, expected, checked=0, violations=None, unknown=0,
               evidence=None, requirement_id='', reason='', observed=None):
        violations = sorted(violations or [], key=lambda row: __import__('json').dumps(row, sort_keys=True))
        status = 'FAIL' if violations else 'UNKNOWN' if unknown or checked == 0 else 'PASS'
        return CheckResult(requirement_id, status, kind, expected,
            {'checked': checked, 'violations': len(violations), 'unknown': unknown,
             **(observed or {})}, violations[:self.example_limit], evidence or [], reason,
            'counterexample' if violations else 'evidence_missing' if status == 'UNKNOWN' else '')

    def select(self, db, scope='', selector=None):
        selector = selector or {}
        if set(selector) - {'types', 'roles'}:
            raise ValueError('Unsupported selector fields')
        if any(not isinstance(v, list) or any(not isinstance(x, str) for x in v)
               for v in selector.values()):
            raise ValueError('Selector values must be lists of strings')
        query = db.get_instances(scope)
        if query.status != 'ok':
            raise ValueError('Unknown scope: ' + scope)
        selected, unknown = {}, 0
        for row in query.data['instances']:
            if not row['leaf']:
                continue
            if selector.get('types') and row['type'] not in selector['types']:
                continue
            if selector.get('roles'):
                role = db.catalog.lookup(row['type'])
                if role.status != 'ok':
                    unknown += 1
                    continue
                actual = role.data['role']
                if actual not in selector['roles'] and not (actual in ('DFF', 'SFF') and 'FF' in selector['roles']):
                    continue
            selected[row['path']] = row
        return selected, unknown

    def check_cell_types(self, db, scope='', selector=None, allowed_types=None,
                         expected_count=None, requirement_id=''):
        if not allowed_types:
            raise ValueError('allowed_types must be nonempty')
        rows, unknown = self.select(db, scope, selector)
        bad = [{'instance': p, 'actual': r['type']} for p, r in rows.items() if r['type'] not in allowed_types]
        if expected_count is not None and len(rows) != expected_count and not unknown:
            bad.append({'expected_count': expected_count, 'actual_count': len(rows)})
        return self.result('cell_type_constraint', {'scope': scope, 'allowed': allowed_types},
            len(rows), bad, unknown, self.evidence(db, rows), requirement_id)

    def check_cell_roles(self, db, scope, cell_class='FF', allowed_roles=None, requirement_id=''):
        if cell_class != 'FF' or allowed_roles != ['DFF']:
            raise ValueError('Only the ordinary-FF regional constraint is supported')
        rows, unknown = self.select(db, scope, {'roles': ['FF']})
        bad = [{'instance': path, 'actual_type': row['type'],
                'actual_role': db.catalog.lookup(row['type']).data['role']}
               for path, row in rows.items()
               if db.catalog.lookup(row['type']).data['role'] not in allowed_roles]
        return self.result('cell_role_constraint', {'scope': scope, 'cell_class': cell_class,
            'allowed_roles': allowed_roles}, len(rows), bad, unknown,
            self.evidence(db, rows), requirement_id)

    def check_cell_count(self, before, after, scope='', selector=None,
                         relation='equal', requirement_id=''):
        if relation not in ('equal', 'at_least', 'at_most'):
            raise ValueError('Unsupported relation')
        a, u = self.select(before, scope, selector)
        b, v = self.select(after, scope, selector)
        valid = {'equal': len(a) == len(b), 'at_least': len(b) >= len(a), 'at_most': len(b) <= len(a)}[relation]
        bad = [] if valid or u or v else [{'before': len(a), 'after': len(b)}]
        return self.result('cell_count_invariant', {'scope': scope, 'relation': relation},
            len(a) + len(b), bad, u + v, before.evidence + after.evidence, requirement_id,
            observed={'before': len(a), 'after': len(b)})

    def check_pin_driver(self, db, scope='', selector=None, pin='', expected_driver=None,
                         exceptions=None, requirement_id=''):
        driver = expected_driver or {}
        if set(driver) != {'kind', 'name'} or driver['kind'] not in ('top_port', 'constant'):
            raise ValueError('Expected typed top_port or constant driver')
        if driver['kind'] == 'top_port':
            port = db.index['ports'].get(driver['name'])
            if not port or port['direction'] != 'input':
                raise ValueError('Expected driver is not a top-level input port')
            target = port['bits']
        else:
            if driver['name'] not in ('0', '1'):
                raise ValueError('Only known binary constants are supported')
            target = [{('constant', driver['name'])}]
        rows, unknown = self.select(db, scope, selector)
        exceptions = exceptions or []
        if set(exceptions) - set(rows):
            raise ValueError('Exception must identify a selected instance')
        bad, checked = [], 0
        for path in rows:
            if path in exceptions:
                continue
            checked += 1
            bits = db.index['cells'][tuple(path.split('/'))]['connections'].get(pin)
            if not bits:
                unknown += 1
                continue
            if len(bits) != len(target):
                bad.append({'instance': path, 'pin': pin, 'reason': 'width_mismatch'})
                continue
            # A directly tied constant proves a mismatch; absence of an endpoint
            # through intervening logic is not proof of the logical driver.
            mismatch, unresolved = False, False
            for bit, wanted in zip(bits, target):
                endpoints = {x for x in bit if x[0] in ('port', 'constant', 'driver')}
                expected = {x for x in wanted if x[0] in ('port', 'constant')}
                if expected <= endpoints:
                    if any(x[0] == 'driver' for x in endpoints):
                        unresolved = True
                elif any(x[0] == 'constant' for x in endpoints):
                    mismatch = True
                elif any(x[0] == 'port' for x in endpoints) and not any(x[0] == 'driver' for x in endpoints):
                    mismatch = True
                else:
                    unresolved = True
            if mismatch:
                bad.append({'instance': path, 'pin': pin, 'reason': 'wrong_direct_driver'})
            elif unresolved:
                unknown += 1
        return self.result('pin_driver', {'scope': scope, 'pin': pin, 'driver': driver},
            checked, bad, unknown, self.evidence(db, rows, pin), requirement_id)

    def check_unchanged(self, before, after, scope='', properties=None, requirement_id='', selector=None):
        properties = ['cell_type', 'pins'] if properties is None else properties
        if not properties or set(properties) - {'cell_type', 'pins'}:
            raise ValueError('Unsupported unchanged properties')
        a, u = self.select(before, scope, selector)
        b, v = self.select(after, scope, selector)
        bad, unknown = [], u + v
        for path in sorted(set(a) | set(b)):
            if path not in a or path not in b:
                bad.append({'instance': path, 'reason': 'added' if path in b else 'removed'})
                continue
            if 'cell_type' in properties and a[path]['type'] != b[path]['type']:
                bad.append({'instance': path, 'reason': 'type_changed'})
            if 'pins' in properties:
                old = before.index['cells'][tuple(path.split('/'))]['connections']
                new = after.index['cells'][tuple(path.split('/'))]['connections']
                for pin in sorted(set(old) | set(new)):
                    equal = same_bits(old[pin], new[pin]) if pin in old and pin in new else False
                    if equal is None:
                        unknown += 1
                    elif not equal:
                        bad.append({'instance': path, 'pin': pin, 'reason': 'connection_changed'})
        return self.result('unchanged', {'scope': scope, 'properties': properties},
            len(set(a) | set(b)), bad, unknown, before.evidence + after.evidence, requirement_id,
            observed={'before': len(a), 'after': len(b)})

    def compare_netlists(self, before, after, scope='', selector=None, requirement_id=''):
        return self.check_unchanged(before, after, scope, selector=selector, requirement_id=requirement_id)

    def check_no_scan_chains(self, db, report_ids=None, requirement_id=''):
        return self.result('no_scan_chains', {}, unknown=1, evidence=db.evidence,
            requirement_id=requirement_id,
            reason='Port names or direct connections alone do not prove absence of scan chains; complete chain evidence required.')

    def check_artifacts(self, output_dir, required_files, requirement_id=''):
        root = Path(output_dir).resolve()
        bad, evidence = [], []
        for name in sorted(set(required_files)):
            path = (root / name).resolve()
            if not path.is_relative_to(root):
                raise ValueError('Artifact escapes output directory')
            if not path.is_file() or path.stat().st_size == 0:
                bad.append({'artifact': name, 'reason': 'missing_or_empty'})
            else:
                evidence.append({'source': str(path), 'locator': 'file'})
        return self.result('artifacts', {'files': required_files}, len(set(required_files)),
                           bad, evidence=evidence, requirement_id=requirement_id)

    def check_run_status(self, log_path, requirement_id=''):
        return self.result('run_status', {}, unknown=1, requirement_id=requirement_id,
            reason='A registered log adapter and runner execution metadata are required')

    def run_lec(self, original_path, modified_path, config_path, requirement_id=''):
        return self.result('lec', {}, unknown=1, requirement_id=requirement_id,
                           reason='EQY execution requires an authorized task-specific adapter')

    def evidence(self, db, rows, pin=None):
        prefix = [{'source': s['source'], 'sha256': s['sha256'],
                   'locator': 'instance:' + p + (('/pin:' + pin) if pin else '')}
                  for p in list(rows)[:self.example_limit] for s in db.evidence[:1]]
        return db.evidence + prefix

    def structural(self, kind, before, after, *, scope=None, target_port='', original_value='0',
                   mapping=None, eligibility_report='', read_eligibility=None, requirement_id='', deadline=float('inf')):
        """Reuse existing complete ICG/FF proofs with actual Liberty semantics."""
        from .proofs import execute_structural, Incomplete
        from .design_data import QueryInvalid
        assertion = {'id': requirement_id, 'kind': kind,
            'scope': scope or {'include': [], 'exclude': []}, 'target_port': target_port,
            'original_value': original_value, 'mapping': mapping or [], 'empty_basis': [],
            'eligibility_report': eligibility_report}
        try:
            if kind == 'icg_rewire':
                port = before.index['ports'].get(target_port)
                if not port or port['direction'] != 'input' or len(port['bits']) != 1:
                    raise QueryInvalid('invalid_top_port', 'ICG target must be a scalar top-level input port')
            def missing_eligibility(_):
                raise Incomplete('No stage-bound replacement eligibility table registered')
            selected, checked, differences, unknown, extra = execute_structural(
                assertion, before.index, after.index, read_eligibility or missing_eligibility, deadline)
            return self.result(kind, {'scope': assertion['scope']}, checked, differences,
                unknown, before.evidence + after.evidence, requirement_id,
                observed={'selected': len(selected), 'details': extra})
        except (Incomplete, QueryInvalid, TimeoutError, KeyError) as exc:
            return CheckResult(requirement_id, 'UNKNOWN', kind, evidence=before.evidence + after.evidence,
                reason=str(exc), reason_kind='query_invalid' if isinstance(exc, QueryInvalid) else 'capability_unsupported')
