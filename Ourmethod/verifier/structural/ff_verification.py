"""Independent FF population, mapping, wiring and exception checks."""
import time

from .design_data import same_bits


def check_ff_mapping(assertion, before, after, read_report, deadline):
    from .proofs import scope_cells, role_cells, ff_signature_matches, Incomplete
    def tick():
        if time.monotonic() >= deadline:
            raise TimeoutError('FF verification deadline exhausted')
    selected = scope_cells(before, assertion['scope'], role_cells(before, {'ordinary_ff', 'scan_ff'}))
    final = scope_cells(before, assertion['scope'], role_cells(after, {'ordinary_ff', 'scan_ff'}))
    if not selected and not assertion['empty_basis']:
        raise Incomplete('Empty FF mapping population')
    checks = {}
    for kind in ('population', 'mapping', 'connections', 'exceptions'):
        checks[kind] = dict(id=assertion['id']+'.'+kind, kind=kind, executor='program',
            verification_method='netlist_comparison' if kind != 'exceptions' else 'optional_eligibility_report',
            status='passed', checked_count=0, differences=[], unknown_items=[], evidence=[],
            reason='', reason_kind='', objects=[])
    population, mapping_check, connections, exceptions = checks.values()
    mapping = {}
    duplicates = set()
    for pair in assertion['mapping']:
        if pair['before'] in mapping: duplicates.add(pair['before'])
        mapping[pair['before']] = pair['after']
    # Enumerate the entire baseline population before reading optional evidence.
    for path in sorted(set(selected) | set(final)):
        tick()
        old, new = selected.get(path), final.get(path)
        item = dict(instance=list(path), before_type=old['type'] if old else None,
                    after_type=new['type'] if new else None)
        population['checked_count'] += 1
        if old is None or new is None:
            item['classification'] = 'added' if old is None else 'missing'
            population['differences'].append(dict(item, reason='FF '+item['classification']))
            population['objects'].append(item)
            continue
        unchanged = old['type'] == new['type']
        item['classification'] = 'unchanged' if unchanged else 'replaced'
        population['objects'].append(item)
        old_meta, new_meta = before['library'][old['type']], after['library'][new['type']]
        if unchanged and old_meta['role']=='scan_ff':
            item['classification']='already_scan'
        elif unchanged:
            exceptions['objects'].append(item)
        else:
            mapping_check['checked_count'] += 1
            if new_meta['role'] != 'scan_ff':
                population['differences'].append(dict(item, reason='Unexpected FF type change'))
                mapping_check['differences'].append(dict(item, reason='Replacement is not a scan FF'))
            elif old['type'] in duplicates or old['type'] not in mapping:
                mapping_check['unknown_items'].append(dict(item, reason='Missing or ambiguous explicit mapping'))
            elif after['library'].get(mapping[old['type']],{}).get('role')!='scan_ff':
                mapping_check['unknown_items'].append(dict(item, reason='Mapping target is not a known scan FF type'))
            elif mapping[old['type']] != new['type']:
                mapping_check['differences'].append(dict(item, expected=mapping[old['type']], reason='Wrong FF mapping'))
            else:
                signature = ff_signature_matches(old_meta, new_meta)
                # Syntactic mismatch is not a functional inequivalence proof.
                if signature is not True:
                    mapping_check['unknown_items'].append(dict(item, reason='Functional mapping signature not established'))
        connections['checked_count'] += 1
        for pin, definition in new_meta['pins'].items():
            if pin not in old_meta['pins'] and definition['direction'] == 'output' and new['connections'].get(pin):
                connections['unknown_items'].append(dict(item, pin=pin, reason='Extra output connection is not proven unused'))
        for pin, definition in old_meta['pins'].items():
            if old_meta['scan_pins'].get(pin) in ('test_scan_in', 'test_scan_enable'): continue
            if definition['direction'] == 'output' and not old['connections'].get(pin) and not new['connections'].get(pin): continue
            if pin not in old['connections'] or pin not in new['connections']:
                connections['unknown_items'].append(dict(item, pin=pin, reason='Normal pin missing'))
                continue
            equal = same_bits(old['connections'][pin], new['connections'][pin])
            if equal is None:
                connections['unknown_items'].append(dict(item, pin=pin, reason='Normal connection correspondence unknown'))
            elif not equal:
                connections['differences'].append(dict(item, pin=pin, reason='FF normal connection changed'))
    eligibility, report_error = {}, ''
    if exceptions['objects'] and assertion.get('eligibility_report'):
        try:
            eligibility = read_report(assertion['eligibility_report'])
        except (ValueError, OSError, KeyError) as exc:
            report_error = str(exc)
    for item in exceptions['objects']:
        tick()
        exceptions['checked_count'] += 1
        flag = eligibility.get(tuple(item['instance']))
        if flag is True:
            exceptions['differences'].append(dict(item, reason='Eligible FF was not replaced'))
        elif flag is not False:
            exceptions['unknown_items'].append(dict(item, reason='Replacement eligibility or exception not established'))
    exceptions['reason'] = report_error or ('No verified exception evidence' if exceptions['unknown_items'] else '')
    for check in checks.values():
        check['status'] = ('failed' if check['differences'] else 'unverified' if check['unknown_items'] else
                           'not_applicable' if not check['checked_count'] and check['kind'] in ('mapping', 'exceptions') else 'passed')
        check['reason_kind'] = ('counterexample' if check['differences'] else
                               'evidence_missing' if check['kind'] == 'exceptions' and check['unknown_items'] else
                               'model_protocol_error' if check['kind']=='mapping' and any(
                                   u['reason'] in ('Missing or ambiguous explicit mapping','Mapping target is not a known scan FF type')
                                   for u in check['unknown_items']) else
                               'capability_unsupported' if check['unknown_items'] else '')
    differences = [d for c in checks.values() for d in c['differences']]
    unknowns = [d for c in checks.values() for d in c['unknown_items']]
    return selected, len(selected), differences, len(unknowns), dict(
        subchecks=list(checks.values()), unknown_items=unknowns)
