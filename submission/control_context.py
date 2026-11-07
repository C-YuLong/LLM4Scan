"""Bounded source evidence for control cones in structural gate-level Verilog.

Pin roles come from the supplied Liberty, not instance names. This is context
selection, not elaboration, simulation, or proof of clock controllability.
"""
from collections import defaultdict, deque
from bisect import bisect_right
import re


def groups(text, kind):
    """Read balanced Liberty groups, ignoring braces inside quoted strings."""
    pattern = re.compile(r'\b' + kind + r'\s*\(\s*([^)]*)\)\s*\{')
    offset = 0
    while match := pattern.search(text, offset):
        depth, quoted, escaped, end = 1, False, False, match.end()
        while depth and end < len(text):
            char = text[end]
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = not quoted
            elif not quoted:
                depth += (char == '{') - (char == '}')
            end += 1
        if depth:
            raise ValueError('Unclosed Liberty ' + kind + ' group')
        yield match[1].strip().strip('"'), text[match.end():end-1]
        offset = end


def library_roles(source):
    result = {}
    for path in sorted(source.rglob('*.lib')):
        for name, body in groups(path.read_text(encoding='utf-8'), 'cell'):
            pin_blocks = dict(groups(body, 'pin'))
            pins = {pin: re.search(r'\bdirection\s*:\s*"?(\w+)', block)[1]
                    for pin, block in pin_blocks.items()
                    if re.search(r'\bdirection\s*:\s*"?(\w+)', block)}
            sequential = list(groups(body, 'ff'))
            latches = list(groups(body, 'latch'))
            # A scan cell may contain a nested test_cell with a different
            # functional-only FF model. The outer cell's first state group is
            # its actual function; do not overwrite it with the nested model.
            control_text = ' '.join(block for _, block in sequential[:1] + latches[:1])
            controls = set()
            for expr in re.findall(r'\b(?:clocked_on|clocked_on_also|enable|clear|preset)\s*:\s*"([^"]+)"', control_text):
                controls.update(pin for pin in re.findall(r'[A-Za-z_$][\w$]*', expr) if pin in pins)
            result[name] = {'inputs': {p for p, d in pins.items() if d == 'input'},
                            'outputs': {p for p, d in pins.items() if d == 'output'},
                            'controls': controls, 'ff': bool(sequential), 'latch': bool(latches),
                            'scan': bool(re.search(r'\bsignal_type\s*:\s*"test_scan_enable"', body)),
                            'functions': {pin: m[1] for pin, block in pin_blocks.items()
                                          if (m := re.search(r'\bfunction\s*:\s*"([^"]+)"', block))},
                            'state': {key: value for key, value in re.findall(
                                r'\b(clocked_on|clocked_on_also|next_state|data_in|enable|clear|preset)\s*:\s*"([^"]+)"', control_text)}}
    return result


def statements(path):
    """Stream semicolon-terminated structural statements with original lines."""
    module, pending, first, comment = None, '', 0, False
    with path.open(encoding='utf-8') as stream:
        for number, raw in enumerate(stream, 1):
            # Keep strings/escaped identifiers intact; gate netlists do not use
            # string-valued expressions in named pin connections.
            line = raw
            if comment:
                end = line.find('*/')
                if end < 0:
                    continue
                line, comment = line[end+2:], False
            while '/*' in line:
                start = line.index('/*')
                end = line.find('*/', start+2)
                if end < 0:
                    line, comment = line[:start], True
                    break
                line = line[:start] + ' ' + line[end+2:]
            line = line.split('//', 1)[0].strip()
            if not line or line.startswith('`'):
                continue
            if re.match(r'^endmodule\b', line):
                module, pending = None, ''
                continue
            if not pending:
                first = number
            pending += line + ' '
            while ';' in pending:
                statement, pending = pending.split(';', 1)
                # Escaped Verilog identifiers end at whitespace, not at ';'.
                # Preserve a delimiter after a final escaped declaration name.
                statement, pending = statement.strip() + ' ;', pending.lstrip()
                match = re.match(r'module\s+(\S+?)\s*\(', statement)
                if match:
                    module = match[1]
                yield module, first, number, statement
                first = number


def mapping_input_losses(source, log_text, *, candidate=None):
    """Detect explicitly mapped FFs that lose a functional Liberty input.

    This narrow check catches data-enable/reset/clock pins absent from the
    replacement. It is not a general proof of equivalent cell functions.
    Use executed commands and the actual pre-scan working copy, not model claims.
    """
    mappings = dict(re.findall(
        r'CMD-0034\]\s+@\d+:\s*set_scan_cell_mapping\s+(\w+)\s+(\w+)\s*$',
        log_text, re.M))
    if not mappings:
        return []
    roles = library_roles(source)
    suspects = {}
    for old, new in mappings.items():
        if old not in roles or new not in roles:
            continue  # The Scan command itself reports missing library cells.
        original, replacement = roles[old], roles[new]
        referenced = set(re.findall(r'[A-Za-z_$][\w$]*',
                         ' '.join(original['state'].values()))) & original['inputs']
        missing = referenced - replacement['inputs']
        if original['ff'] and missing:
            suspects[old] = {'original_cell': old, 'replacement_cell': new,
                             'lost_functional_inputs': sorted(missing),
                             'original_state': original['state'],
                             'replacement_state': replacement['state']}
    if not suspects:
        return []
    # Unused declarations cannot have removed a circuit input. In Task 2 a
    # proved working copy can legitimately eliminate an enabled-FF cell type.
    paths = [candidate] if candidate is not None else sorted((source / 'netlist').rglob('*.v'))
    found = {}
    for path in paths:
        for module, first, last, statement in statements(path):
            cell = re.match(r'(\w+)\s+(\S+)\s*\(', statement)
            if cell and cell[1] in suspects and cell[1] not in found:
                found[cell[1]] = {**suspects[cell[1]], 'example_instance': cell[2],
                                  'module': module, 'source_lines': f'L{first}-L{last}'}
        if found.keys() == suspects.keys():
            break
    return list(found.values())


def constant_output_check(source, script, outputs, deadline, *, candidate=None):
    """Check top-level constant outputs using Yosys's real alias semantics.

    Only declarations, continuous assignments and supplied constant/buffer/
    inverter functions are retained. No FFs or logic cones are elaborated.
    This detects the observed Scan constant/concatenation export corruption;
    it is not full connectivity or functional equivalence checking.
    """
    import json
    from pathlib import Path
    import subprocess
    import tempfile
    import time

    tops = re.findall(r'(?m)^\s*present_design\s+([\w$]+)\s*$', script)
    if len(tops) != 1 or not outputs:
        return {'checked': False, 'reason': 'No single explicit top and emitted netlist available'}
    top = tops[0]
    roles = library_roles(source)
    def skeleton(paths):
        lines, excerpts = [], []
        for path in paths:
            for module, first, last, statement in statements(path):
                if time.monotonic() >= deadline:
                    raise TimeoutError('Constant-output check exceeded the current run deadline')
                if module != top:
                    continue
                if re.match(r'^(?:module|input|output|inout|wire|tri|reg|logic|supply0|supply1|parameter|localparam|assign)\b', statement):
                    lines.append(statement)
                    if statement.startswith('assign ') and re.search(r"\d+'[sS]?[bBoOdDhH]", statement):
                        excerpts.append({'source': path.relative_to(source).as_posix() if path.is_relative_to(source) else path.name,
                                         'line_start': first, 'line_end': last, 'statement': statement})
                    continue
                cell = re.fullmatch(r'(\S+)\s+(\S+)\s*\((.*)\)\s*;', statement)
                role = roles.get(cell[1]) if cell else None
                if not role or role['ff'] or role['latch']:
                    continue
                pins = dict(re.findall(r'\.(\w+)\s*\(\s*([^()]*)\)', cell[3]))
                for pin in role['outputs']:
                    function = role['functions'].get(pin, '').strip('() ')
                    if pin not in pins or not pins[pin]:
                        continue
                    if function in ('0', '1'):
                        expression = "1'b" + function
                    elif function in role['inputs'] and pins.get(function):
                        expression = pins[function]
                    elif function.startswith('!') and function[1:] in role['inputs'] and pins.get(function[1:]):
                        expression = '~(' + pins[function[1:]] + ')'
                    else:
                        continue
                    lines.append(f'assign {pins[pin]} = {expression};')
        return '\n'.join(lines + ['endmodule']) + '\n', excerpts

    scope = 'Top-level constant output bits via continuous aliases and supplied constant/buffer/inverter functions; not complete functional equivalence'
    paths = [candidate] if candidate is not None else sorted((source / 'netlist').rglob('*.v'))
    original, excerpts = skeleton(paths)
    with tempfile.TemporaryDirectory(prefix='scan-output-aliases-') as folder:
        work = Path(folder)
        def read_ports(text, label):
            net = work / (label + '.v')
            result = work / (label + '.json')
            net.write_text(text, encoding='utf-8')
            commands = f'read_verilog "{net.as_posix()}"; hierarchy -top {top}; write_json "{result.as_posix()}"'
            process = subprocess.run(['yosys', '-Q', '-T', '-q', '-p', commands],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                timeout=max(0.001, deadline - time.monotonic()))
            if process.returncode:
                return None, process.stdout[-4000:]
            return json.loads(result.read_text())['modules'][top]['ports'], process.stdout
        original_ports, log = read_ports(original, 'original')
        if original_ports is None:
            return {'checked': False, 'reason': 'Original alias-only view is not supported', 'tool_log': log, 'scope': scope}
        expected = [(name, index, bit) for name, port in original_ports.items() if port['direction'] == 'output'
                    for index, bit in enumerate(port['bits']) if bit in ('0', '1')]
        if not expected:
            return {'checked': False, 'reason': 'No constant top-level output bits in the supported view', 'scope': scope}
        mismatches, logs = [], {}
        for number, path in enumerate(outputs):
            actual, _ = skeleton([path])
            ports, log = read_ports(actual, 'candidate' + str(number))
            if log:
                logs[path.name] = log[-4000:]
            if ports is None:
                mismatches.append({'artifact': path.name, 'reason': 'Emitted alias-only view could not be parsed'})
                continue
            for name, index, bit in expected:
                observed = ports.get(name, {}).get('bits', [])
                if index >= len(observed) or observed[index] != bit:
                    port = original_ports[name]
                    bit_index = port.get('offset', 0) + (len(port['bits']) - 1 - index if port.get('upto') else index)
                    mismatches.append({'artifact': path.name, 'port': name, 'bit': bit_index, 'expected_constant': bit,
                        'observed': observed[index] if index < len(observed) and isinstance(observed[index], str) else 'not established as the original constant'})
        # Exact source, not the normalized parser statement, is needed for the
        # existing uniquely-matched working-copy edits after a real mismatch.
        excerpts = excerpts[:32]
        if mismatches:
            for path in paths:
                name = path.relative_to(source).as_posix() if path.is_relative_to(source) else path.name
                entries = [item for item in excerpts if item['source'] == name]
                if not entries:
                    continue
                selected = {line for item in entries for line in range(item['line_start'], item['line_end'] + 1)}
                actual_lines, last_line = {}, max(selected)
                with path.open(encoding='utf-8') as stream:
                    for number, line in enumerate(stream, 1):
                        if time.monotonic() >= deadline:
                            raise TimeoutError('Constant-output evidence exceeded the current run deadline')
                        if number in selected:
                            actual_lines[number] = line
                        if number >= last_line:
                            break
                for item in entries:
                    item['statement'] = ''.join(actual_lines[n] for n in range(item['line_start'], item['line_end'] + 1))
        return {'checked': True, 'passed': not mismatches, 'constant_output_bits': len(expected),
                'netlists_checked': len(outputs), 'mismatch_count': len(mismatches), 'mismatches': mismatches[:16],
                'original_constant_assignments': excerpts, 'tool_logs': logs, 'scope': scope}


def nets(expression):
    expression = re.sub(r"\d*'[sS]?[bBoOdDhH][0-9a-fA-FxXzZ_]+", '', expression)
    return re.findall(r'\\\S+|[A-Za-z_$][\w$]*(?:\s*\[\s*\d+\s*\])?', expression)


def original_instances(source, script, selected_types, library_types):
    """Resolve original leaf paths for mapping and task-membership checks."""
    modules = {}
    for path in sorted((source / 'netlist').rglob('*.v')):
        for module, _, _, statement in statements(path):
            if statement.startswith('module '):
                modules.setdefault(module, [])
                continue
            match = re.fullmatch(r'(\S+)\s+(\S+)\s*\((.*)\)\s*;', statement)
            if match and (match[1] in selected_types or match[1] not in library_types):
                modules.setdefault(module, []).append((match[1], match[2].lstrip('\\')))
    tops = re.findall(r'(?m)^\s*present_design\s+([\w$]+)\s*$', script)
    if len(tops) != 1 or tops[0] not in modules:
        return None
    expected = {}
    pending = [(tops[0], '', ())]
    while pending:
        module, prefix, ancestors = pending.pop()
        if module in ancestors:
            raise ValueError('Recursive hierarchy in mapping evidence')
        for kind, name in modules[module]:
            if kind in selected_types:
                expected[prefix + name] = kind
            elif kind in modules:
                pending.append((kind, prefix + name + '/', ancestors + (module,)))
    return expected


def required_mapping_check(source, script, reports, required):
    """Check task-explicit replacement types for reported original FF instances.

    Reuse the structural statement reader and hierarchy; this does not certify
    excluded/unreported cells or replace actual netlist/function verification.
    """
    if source is None:
        return False, {'error': 'Original source is unavailable for required cell mapping checks'}
    roles = library_roles(source)
    def resolve(label):
        choices = [name for name in roles if name == label or name.endswith('__' + label)]
        return choices[0] if len(choices) == 1 else None
    mappings = {}
    for original, replacement in required.items():
        old, new = resolve(original), resolve(replacement)
        if old is None or new is None:
            return False, {'error': 'Task cell name is absent or ambiguous in the actual library',
                           'original': original, 'replacement': replacement}
        mappings[old] = new
    instances = original_instances(source, script, mappings, roles)
    if instances is None:
        return False, {'error': 'Cannot resolve the actual top for required mapping checks'}
    expected = {name: mappings[kind] for name, kind in instances.items()}
    observed, mismatches, retained = {}, [], []
    for report, text in reports.items():
        if not all(header in text for header in ('CellNo', 'InstanceName', 'RefName')):
            continue
        chain, shifts = None, {}
        for number, line in enumerate(text.splitlines(), 1):
            match = re.match(r'^\s*(?:([IW]\s+\S+)\s+)?(\d+)\s+(\S+)\s+\S+\s+\([^)]+\)\s+(\S+)\s+\S+(?:\s+(\d+)/(\d+))?', line)
            if not match:
                continue
            chain = match[1] or chain
            index, name, actual = int(match[2]), match[3], match[4]
            retained_shift = False
            if 'ShiftRegID/CellNo' in text and match[5] is not None:
                group, position = match[5], int(match[6])
                preceding = shifts.get((chain, group))
                # Scan intentionally replaces only the head of an inferred
                # shift register. Its later FFs still shift through their D
                # inputs, so an original non-scan RefName is not a wrong map.
                retained_shift = (position > 0 and preceding is not None
                    and preceding['position'] == position - 1 and preceding['cell_index'] == index - 1
                    and preceding['valid'])
                valid_head = position == 0 and roles.get(actual, {}).get('scan', False)
                shifts[(chain, group)] = {'position': position, 'cell_index': index,
                                         'valid': bool(valid_head or retained_shift)}
            if name in expected:
                observed[name] = actual
                if actual == instances[name] and retained_shift:
                    retained.append({'source': report, 'line': number, 'instance': name,
                                     'shift_register': group, 'position': position})
                elif actual != expected[name] and len(mismatches) < 8:
                    mismatches.append({'source': report, 'line': number, 'instance': name,
                                       'expected': expected[name], 'actual': actual})
    return (not mismatches and (bool(observed) or not expected)), {
        'mappings': mappings, 'original_instances': len(expected), 'reported_instances_checked': len(observed),
        'mismatches': mismatches, 'retained_shift_cells': len(retained), 'retained_shift_examples': retained[:8],
        'scope': 'Replacement types of reported original instances, preserving reported non-head shift FFs; not unreported-cell coverage or functional equivalence'}


def selector_matches(name, selector):
    pattern = re.escape(selector).replace(r'\*', '.*').replace(r'\?', '.')
    return bool(re.fullmatch(pattern, name)) or name.startswith(selector.rstrip('/') + '/')


def explicit_exclusion_check(reports, rules, *, required):
    """Reject actual user-excluded FFs outside the task-frozen scope.

    Latches are not DFFs. This does not prove all unreported cells were scanned
    or that natural DRC classifications are correct.
    """
    objects = [(name, text) for name, text in reports.items() if name.endswith('.rpt')
               and re.search(r'InstanceName.*Obj_?State', text[:2000])]
    if not objects:
        return not required, {'checked': False, 'reason': 'No register/object report; rpt_scan_element -type all is required when using set_scan_element'}
    excluded = [rule['target'] for rule in rules if rule['value'] == 'exclude']
    included = [rule['target'] for rule in rules if rule['value'] == 'include']
    count, forbidden, examples = 0, 0, []
    for name, text in objects:
        # The long InstanceName/clock columns made the failing greedy regex
        # backtrack on every ordinary scannable row in large object reports.
        # Read the same whitespace-delimited columns and exact final state.
        for line in text.splitlines():
            fields = line.split()
            if (len(fields) < 5 or fields[1] not in {'dff', 'sff'}
                    or not fields[2].isdecimal() or fields[-1] != 'user_defined_nonscannable'):
                continue
            instance = fields[3]
            count += 1
            if (not any(selector_matches(instance, target) for target in excluded)
                    or any(selector_matches(instance, target) for target in included)):
                forbidden += 1
                if len(examples) < 8:
                    examples.append({'source': name, 'instance': instance, 'state': 'user_defined_nonscannable'})
    return not forbidden, {'checked': True, 'user_excluded_ff_rows': count,
                          'unauthorized_ff_rows': forbidden, 'examples': examples,
                          'task_exclusion_scopes': excluded, 'task_inclusion_exceptions': included}


def required_membership_check(source, script, ctl_texts, rules, *, reports=None):
    """Enforce task-selected inclusion/exclusion against original leaf paths.

    A bare hierarchy selects its descendants; * and ? are name wildcards,
    while bus brackets remain literal. Inclusion overrides broad exclusion.
    Actual participation comes from CTL or scan-cell reports, not candidate Tcl.
    A task need not export CTL just to demonstrate a scan exclusion.
    """
    if source is None:
        return False, {'error': 'Original source is unavailable for scan membership checks'}
    roles = library_roles(source)
    original = original_instances(source, script,
        {name for name, role in roles.items() if role['ff'] or role['latch']}, roles)
    if original is None:
        return False, {'error': 'Cannot resolve the actual top for scan membership checks'}
    included, excluded, selections = set(), set(), []
    for rule in rules:
        selected = {name for name in original if selector_matches(name, rule['target'])}
        (included if rule['value'] == 'include' else excluded).update(selected)
        selections.append({**rule, 'original_matched': len(selected)})
    actual = {name for text in ctl_texts for body in re.findall(r'\bScanCells\s+(.*?);', text, re.S)
              for name in re.findall(r'"([^"]+)"', body)}
    cell_reports = [text for text in (reports or {}).values()
                    if all(header in text for header in ('CellNo', 'InstanceName', 'RefName'))]
    actual.update(name for text in cell_reports for name in re.findall(
        r'(?m)^\s*(?:[IW]\s+\S+\s+)?\d+\s+(\S+)\s+\S+\s+\([^)]+\)\s+\S+', text))
    missing, forbidden = included - actual, (excluded - included) & actual
    ok = bool(ctl_texts or cell_reports) and all(row['original_matched'] for row in selections) and not missing and not forbidden
    return bool(ok), {'selections': selections, 'missing_count': len(missing),
                      'forbidden_count': len(forbidden), 'missing_examples': sorted(missing)[:8],
                      'forbidden_examples': sorted(forbidden)[:8],
                      'scope': 'Task-selected original sequential instances versus CTL/scan-cell reports; not functional equivalence'}


def control_context(source, additional_cell_types=()):
    roles = library_roles(source)
    paths = sorted((source / 'netlist').rglob('*.v'))
    ports, headers = defaultdict(dict), defaultdict(list)
    # Module port directions are needed before interpreting hierarchy bindings.
    for file_no, path in enumerate(paths):
        for module, start, end, text in statements(path):
            if text.startswith('module '):
                headers[file_no].append((start, end))
            if text.startswith(('module ', 'input ', 'output ', 'inout ')):
                for direction, names in re.findall(r'\b(input|output|inout)\b(.*?)(?=\b(?:input|output|inout)\b|$)', text):
                    names = re.sub(r'\[[^]]+\]|\b(?:wire|reg|signed|logic)\b', '', names)
                    ports[module].update((name, direction) for name in nets(names))
    drivers, incoming, seeds = {}, defaultdict(list), {}
    additional_ranges = set()
    additional_kinds = {}
    type_coverage = {kind: {'source_statements': 0, 'fully_shown': 0} for kind in sorted(additional_cell_types)}
    unknown_types = set()
    for file_no, path in enumerate(paths):
        for module, start, end, text in statements(path):
            assignment = re.fullmatch(r'assign\s+(\S+)\s*=\s*(.*);', text)
            if assignment:
                drivers[(module, assignment[1])] = (file_no, start, end, tuple((module, n) for n in nets(assignment[2])), None)
                continue
            cell = re.fullmatch(r'(\S+)\s+(\S+)\s*\((.*)\)\s*;', text)
            if not cell or cell[1] == 'module':
                continue
            kind, instance, body = cell.groups()
            if kind in type_coverage:
                type_coverage[kind]['source_statements'] += 1
                if len(additional_ranges) < 192:
                    additional_ranges.add((file_no, start, end))
                    additional_kinds[(file_no, start, end)] = kind
            pins = dict(re.findall(r'\.(\w+)\s*\(\s*([^()]*)\)', body))
            if kind in ports:
                for pin, value in pins.items():
                    links = [(module, net) for net in nets(value)]
                    if ports[kind].get(pin) == 'input':
                        incoming[(kind, pin)].extend((file_no, start, end, (link,), pin) for link in links)
                    elif ports[kind].get(pin) == 'output':
                        for link in links:
                            drivers[link] = (file_no, start, end, ((kind, pin),), pin)
                continue
            role = roles.get(kind)
            if role is None:
                unknown_types.add(kind)
                continue
            # The walk is bounded, so set iteration order would change which
            # evidence is selected across processes for the same input files.
            for pin in sorted(role['controls']):
                for net in nets(pins.get(pin, '')):
                    key = (module, net)
                    if key not in seeds:
                        seeds[key] = [0, file_no, start, end, instance, pin]
                    seeds[key][0] += 1
            if role['ff']:
                # Do not index millions of FF data cones. A FF-generated clock
                # is reported as a state boundary below, not treated as a PI.
                continue
            inputs = tuple((module, net) for pin in sorted(role['inputs']) for net in nets(pins.get(pin, '')))
            for pin in sorted(role['outputs']):
                for net in nets(pins.get(pin, '')):
                    drivers[(module, net)] = (file_no, start, end, inputs, None)
    selected, boundaries = additional_ranges, set()
    hierarchy_pins = defaultdict(set)
    # Keep a representative path for each independent input boundary before
    # spending the excerpt budget on repeated paths to the same source. A
    # high-fanout-only walk hid small independent reset domains behind RAM clocks.
    seed_order = sorted(seeds, key=lambda key: (seeds[key][0], key), reverse=True)
    pending = deque((key, key, ()) for key in seed_order)
    visited, represented_seeds = set(seed_order), []
    omitted = False
    def select_path(seed, trail, boundary=None):
        nonlocal omitted
        _, file_no, start, end, _, _ = seeds[seed]
        ranges = {(file_no, start, end)} | {(f, a, b) for f, a, b, _, _ in trail}
        if len(selected | ranges) > 192:
            omitted = True
            return
        selected.update(ranges)
        represented_seeds.append(seed)
        if boundary is not None:
            boundaries.add(boundary)
        for f, a, b, _, pin in trail:
            if pin is not None:
                hierarchy_pins[(f, a, b)].add(pin)
    # This walks the parsed module-local control graph, not an elaborated
    # hierarchy or FF data cones. Breadth first retains short source evidence.
    while pending:
        key, seed, trail = pending.popleft()
        entries = incoming.get(key, []) if key not in drivers else [drivers[key]]
        if not entries:
            if ports[key[0]].get(key[1]) == 'input':
                select_path(seed, trail, key)
        for entry in entries:
            inputs = entry[3]
            if len(trail) < 192:
                for upstream in inputs:
                    if upstream not in visited:
                        visited.add(upstream)
                        pending.append((upstream, seed, trail + (entry,)))
            else:
                omitted = True
    sample_order = list(dict.fromkeys(represented_seeds + seed_order))[:24]
    # Distinct inputs alone do not describe reconvergent gates or state-driven
    # clocks. Use the remaining existing budget for the full sampled cones.
    pending = list(reversed(list(dict.fromkeys(sample_order + seed_order))))
    visited = set()
    while pending and len(selected) < 192:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        entries = incoming.get(key, []) if key not in drivers else [drivers[key]]
        if not entries:
            boundaries.add(key)
        for file_no, start, end, inputs, pin in entries:
            selected.add((file_no, start, end))
            if pin is not None:
                hierarchy_pins[(file_no, start, end)].add(pin)
            pending.extend(inputs)
    for key in sample_order:
        _, file_no, start, end, _, _ = seeds[key]
        selected.add((file_no, start, end))
    state_boundaries = []
    # Find state drivers only for unresolved control nets. No recursive data
    # expansion: the exact FF statement remains evidence of the state boundary.
    for file_no, path in enumerate(paths):
        for module, start, end, text in statements(path):
            cell = re.fullmatch(r'(\S+)\s+(\S+)\s*\((.*)\)\s*;', text)
            if not cell or not roles.get(cell[1], {}).get('ff'):
                continue
            pins = dict(re.findall(r'\.(\w+)\s*\(\s*([^()]*)\)', cell[3]))
            hits = {(module, net) for pin in roles[cell[1]]['outputs'] for net in nets(pins.get(pin, ''))} & boundaries
            if hits:
                selected.add((file_no, start, end))
                state_boundaries.extend('/'.join(hit) for hit in sorted(hits))
                boundaries -= hits
    # Preserve declaration scope around sparse excerpts: identical short net
    # names in different modules are not aliases of one another.
    for file_no, start, _ in list(selected):
        starts = [a for a, _ in headers[file_no]]
        index = bisect_right(starts, start) - 1
        if index >= 0:
            # The module name establishes scope. Long port-list headers used
            # to consume the excerpt budget without showing the control path.
            header_start = headers[file_no][index][0]
            selected.add((file_no, header_start, header_start))
    excerpts, remaining, used_types = {}, 48000, set()
    for file_no, path in enumerate(paths):
        ranges = sorted((start, end) for f, start, end in selected if f == file_no)
        if not ranges:
            continue
        chosen = {n for start, end in ranges for n in range(start,
            (start if (file_no, start, end) in hierarchy_pins else min(end, start+40))+1)}
        pin_ranges = [(start, end, re.compile(r'\.(?:' + '|'.join(re.escape(pin) for pin in sorted(pins)) + r')\s*\('))
                      for (f, start, end), pins in sorted(hierarchy_pins.items()) if f == file_no]
        pin_starts = [start for start, _, _ in pin_ranges]
        continuation = None
        lines, shown_lines = [], set()
        with path.open(encoding='utf-8') as stream:
            for number, line in enumerate(stream, 1):
                # Show the actual traversed binding, not merely the first 40
                # lines of a potentially hundreds-of-ports instance. Preserve
                # its original line numbers and multiline pin expression.
                index = bisect_right(pin_starts, number) - 1
                binding_line = False
                if index >= 0 and number <= pin_ranges[index][1]:
                    match = pin_ranges[index][2].search(line)
                    binding_line = bool(match) or continuation == index
                    if binding_line:
                        rest = line[match.end():] if match else line
                        continuation = index if ')' not in rest else None
                if number in chosen or binding_line:
                    entry = f'L{number}: {line.rstrip()}'
                    if len(entry) <= remaining:
                        lines.append(entry)
                        shown_lines.add(number)
                        remaining -= len(entry)
                        match = re.match(r'\s*(\S+)\s+\S+\s*\(', line)
                        if match and match[1] in roles:
                            used_types.add(match[1])
        excerpts[path.relative_to(source).as_posix()] = '\n'.join(lines)
        for (file_index, start, end), kind in additional_kinds.items():
            if file_index == file_no and all(number in shown_lines for number in range(start, end + 1)):
                type_coverage[kind]['fully_shown'] += 1
    # Functional-input loss can require a plain FF plus a data mux. Those cells
    # need not be upstream of any control pin, so expose a few real library
    # candidates rather than asking the model to invent cell/pin names.
    rewrite_candidates = []
    if additional_cell_types:
        seen_functions = set()
        for kind in sorted(roles, key=lambda name: (len(name), name)):
            role = roles[kind]
            plain_ff = (role['ff'] and not role['scan']
                        and set(role['state']) == {'clocked_on', 'next_state'}
                        and role['state']['next_state'] in role['inputs'])
            named_mux = (not role['ff'] and not role['latch'] and len(role['inputs']) == 3
                         and 'mux' in kind.lower())
            if not plain_ff and not named_mux:
                continue
            signature = (tuple(sorted(role['inputs'])), tuple(sorted(role['functions'].items())),
                         tuple(sorted(role['state'].items())))
            if signature not in seen_functions:
                seen_functions.add(signature)
                rewrite_candidates.append(kind)
                used_types.add(kind)
            if len(rewrite_candidates) == 8:
                break
    return {'scope': 'Source excerpts upstream of Liberty FF/latch control pins; module-local nets, not full elaboration or proof. Multiple instantiations retain all input bindings. FF data paths stop at state boundaries.',
            'sources': excerpts, 'state_boundaries': state_boundaries,
            'requested_type_coverage': {kind: {**counts,
                'complete': counts['source_statements'] == counts['fully_shown']}
                for kind, counts in type_coverage.items()},
            'control_samples': [{'module': module, 'net': net,
                'example_instance': seeds[(module, net)][4], 'example_pin': seeds[(module, net)][5],
                'source': paths[seeds[(module, net)][1]].relative_to(source).as_posix(),
                'line_start': seeds[(module, net)][2], 'line_end': seeds[(module, net)][3]}
                for module, net in sample_order],
            'library_cell_functions': {kind: {'outputs': roles[kind]['functions'], 'state': roles[kind]['state']}
                                       for kind in sorted(used_types)},
            'rewrite_cell_candidates': rewrite_candidates,
            'library_scan_cell_names': sorted(kind for kind, role in roles.items() if role['scan']),
            'unresolved_or_input_nets': ['/'.join(key) for key in sorted(boundaries)][:96],
            'unknown_cell_types': sorted(unknown_types)[:32],
            'truncated': omitted or bool(pending) or remaining < 2000,
            'control_net_count': len(seeds)}


def component_sources(source, additional_cell_types=()):
    sources = {str(p.relative_to(source)): p.read_text(encoding='utf-8') for p in sorted(source.rglob('*'))
               if p.is_file() and p.suffix in {'.v', '.ctl'} and p.stat().st_size <= 32768}
    # Metadata, not an input filename; source excerpts carry their actual paths.
    sources['__control_trace__'] = control_context(source, additional_cell_types)
    return sources
