"""Conservative Liberty facts. Names never determine a cell's hardware role."""
import re


def clean(text):
    # Preserve strings; remove comments before finding groups and attributes.
    return re.sub(r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*',
                  lambda m: m[0] if m[0].startswith('"') else ' ', text, flags=re.S)


def groups(text, keyword):
    text = clean(text)
    pattern = re.compile(r'\b' + re.escape(keyword) + r'\s*\(([^{}]*?)\)\s*\{')
    offset = 0
    while match := pattern.search(text, offset):
        start, depth, quoted, escaped = match.end(), 1, False, False
        i = start
        while i < len(text) and depth:
            char = text[i]
            if escaped: escaped = False
            elif quoted and char == '\\': escaped = True
            elif char == '"': quoted = not quoted
            elif not quoted and char == '{': depth += 1
            elif not quoted and char == '}': depth -= 1
            i += 1
        if depth: raise ValueError('Unclosed Liberty group: ' + keyword)
        yield match[1].strip().strip('"'), text[start:i-1]
        offset = i


def attribute(text, name):
    match = re.search(r'\b' + re.escape(name) + r'\s*:\s*("(?:\\.|[^"\\])*"|[^;]+)\s*;', text)
    return match[1].strip().strip('"') if match else ''


def library_metadata(text):
    result = {}
    for name, body in groups(text, 'cell'):
        pins, gate, scan = {}, {}, {}
        conflicts = []
        for pin, definition in groups(body, 'pin'):
            direction = attribute(definition, 'direction')
            previous = pins.get(pin, {})
            if direction and previous.get('direction') and previous['direction'] != direction:
                conflicts.append('Conflicting pin direction: ' + pin)
            signal = attribute(definition, 'signal_type')
            pins[pin] = dict(direction=direction or previous.get('direction', ''),
                             signal_type=signal or previous.get('signal_type', ''),
                             function=attribute(definition, 'function') or previous.get('function', ''))
            for role in ('clock', 'enable', 'out', 'test'):
                if attribute(definition, 'clock_gate_' + role + '_pin') == 'true':
                    if role in gate and gate[role] != pin: conflicts.append('Ambiguous ICG pin: ' + role)
                    gate[role] = pin
            if signal.startswith('test_scan_'):
                scan[pin] = signal
        ffs = list(groups(body, 'ff'))
        latches = list(groups(body, 'latch'))
        if ffs and latches: conflicts.append('Mixed sequential roles')
        role = ('icg' if gate and attribute(body, 'clock_gating_integrated_cell') else
                'scan_ff' if ffs and scan else 'ordinary_ff' if ffs else 'latch' if latches else 'combinational')
        if attribute(body,'clock_gating_integrated_cell') and role!='icg':
            conflicts.append('Missing ICG role pins')
        if list(groups(body,'ff_bank')) or list(groups(body,'latch_bank')):
            conflicts.append('Unsupported sequential bank role')
        if any(p['direction'] not in ('input','output','internal','inout') for p in pins.values()):
            conflicts.append('Missing pin direction')
        if role=='combinational' and (not pins or any(not p['function'] for p in pins.values() if p['direction']=='output')):
            conflicts.append('No complete combinational cell function')
        if role == 'icg' and not {'clock', 'enable', 'out', 'test'} <= set(gate):
            conflicts.append('Incomplete ICG role attributes')
        if ffs and scan and not {'test_scan_in', 'test_scan_enable'} <= set(scan.values()):
            conflicts.append('Incomplete scan role attributes')
        # test_cell describes the normal sequential function of scan cells.
        test = list(groups(body, 'test_cell'))
        functional = list(groups(test[0][1], 'ff')) if test else []
        sequential = functional[0][1] if functional else ffs[0][1] if ffs else ''
        signature = {key: attribute(sequential, key) for key in (
            'clocked_on', 'next_state', 'clear', 'preset', 'clear_preset_var1', 'clear_preset_var2')}
        state_names=(functional[0][0] if functional else ffs[0][0] if ffs else '').split(',')
        for pin, definition in pins.items():
            if definition['direction']!='output': continue
            function=definition['function']
            for index,state_name in enumerate(state_names):
                state_name=state_name.strip().strip('"')
                if state_name: function=re.sub(r'\b'+re.escape(state_name)+r'\b', '@STATE'+str(index), function)
            signature['output:'+pin]=function
            if ffs and not function: conflicts.append('Unknown FF output polarity: '+pin)
        if ffs and not all(signature[key] for key in ('clocked_on', 'next_state')):
            conflicts.append('Incomplete FF function')
        result[name] = dict(role='unknown' if conflicts else role, pins=pins, icg_pins=gate,
                            scan_pins=scan, functional_signature=signature,
                            signature_complete=bool(sequential) and (not scan or bool(functional)),
                            unknowns=conflicts)
    return result
