"""Required JSON fields for the model's existing script/evidence contracts."""


def record(fields):
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


STRING = {"type": "string"}
BOOL = {"type": "boolean"}
INTEGER = {"type": "integer"}


class ModelResponseError(ValueError):
    """A returned model plan failed its required shape; retain it for diagnosis."""

    def __init__(self, reason, plan, usage):
        super().__init__(reason)
        self.plan = plan
        self.usage = usage


def validate(value, schema, path='$'):
    """Enforce the same required JSON shape on the competition's chat API."""
    kind = schema['type']
    expected = {'object': dict, 'array': list, 'string': str, 'boolean': bool, 'integer': int}[kind]
    if type(value) is not expected:
        raise ValueError(f'{path}: expected {kind}, got {type(value).__name__}')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(f'{path}: invalid enum value')
    if kind == 'object':
        if not set(schema['required']) <= value.keys() or (schema.get('additionalProperties') is False and value.keys() - schema['properties'].keys()):
            missing = sorted(set(schema['required']) - value.keys())
            extra = sorted(value.keys() - schema['properties'].keys())
            raise ValueError(f'{path}: missing fields {missing}; extra fields {extra}')
        for key, item in value.items():
            validate(item, schema['properties'][key], f'{path}.{key}')
    elif kind == 'array':
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', len(value)):
            raise ValueError(f'{path}: incorrect array length')
        for index, item in enumerate(value):
            validate(item, schema['items'], f'{path}[{index}]')
TASK1 = record({
    "dofile": STRING,
    "requirements": {"type": "array", "minItems": 1, "items": record({"requirement": STRING, "command": STRING})},
    "repair": record({"located_object": STRING, "root_cause": STRING, "action": STRING}),
})
NETLIST_EDITS = {"type": "array", "items": record({"path": STRING, "before": STRING, "after": STRING})}
TASK2 = record({
    "dofile": {"type": "string", "description": "Tcl body only. The runner supplies input_dir, out_rpt_dir and out_res_dir; never redefine them or copy original script_dir/base_dir path setup."},
    "netlist_edits": NETLIST_EDITS,
    "issues": {"type": "array", "minItems": 0, "items": record({
        **{key: STRING for key in ("phenomenon", "located_object", "root_cause", "violated_requirement", "original_source", "original_command", "fix_action")},
        "defective_parameter": {"type": "string", "description": "One defective Tcl option such as -off_state, or arg1/arg2 for the first/second positional operand (a braced list is one operand). Separate independently wrong operands even on the same command. Empty only for a whole-command or structural defect."},
        "category": {"type": "string", "enum": ["command", "drc", "configuration"],
                     "description": "command for invalid syntax, command names or identifiers; drc for physical controllability defects; configuration for an otherwise legal setting that contradicts an explicit task requirement. Only configuration needs a task citation."}})},
})
REPAIR = record({"dofile": TASK2["properties"]["dofile"], "netlist_edits": NETLIST_EDITS,
                 "repair_action": STRING, "additional_issues": TASK2["properties"]["issues"],
                 "issue_updates": {"type": "array", "items": record({
                     "issue_number": INTEGER, "diagnosis": TASK2['properties']['issues']['items']})}})

# Extracted from the task alone, before proposing a script; frozen across repairs.
TASK_CONSTRAINTS = record({
    "constraints": {"type": "array", "items": record({
        "line_start": INTEGER, "line_end": INTEGER,
        "kind": {"type": "string", "enum": ["chain_count", "max_chain_count", "max_cells_per_chain", "chain_partition", "drc_zero", "config", "clock", "reset", "scan_enable", "scan_enable_usage", "constant", "wrapper_port", "output_file", "cell_mapping", "scan_membership",
                                                "wrapper_clock", "wrp_in_shift_en", "wrp_out_shift_en", "wrp_in_capture_en", "wrp_out_capture_en"]},
        "target": STRING, "value": STRING,
    })},
    "uncovered": {"type": "array", "items": record({"line_start": INTEGER, "line_end": INTEGER})},
})


def verification(issues):
    # Bind each verdict to one known finding in the response shape itself.
    # A free array allowed shifted identities and self-referential duplicates,
    # consuming the short task deadline on an otherwise completed Scan run.
    findings = {}
    for number, issue in enumerate(issues, 1):
        parameter = issue.get('defective_parameter')
        targets = [index for index, earlier in enumerate(issues[:number - 1], 1)
                   if not parameter or not earlier.get('defective_parameter')
                   or parameter == earlier['defective_parameter']]
        findings[f'I{number}'] = record({
            "issue_number": {"type": "integer", "enum": [number]},
            "resolved": BOOL, "original_defect_supported": BOOL, "support_basis": STRING,
            "duplicate_of": {"type": "integer", "enum": [0, *targets],
                             "description": "0 for an independent root; otherwise an allowed earlier supported independent finding about the SAME cause. Never this finding itself."},
            "evidence_id": {"type": "string", "description": "Copy one E-prefixed evidence ID displayed in actual_files for this candidate run. It binds the real source, file line and raw text together. Empty only for unresolved, unsupported or duplicate findings. Never use a Tcl @N number."},
        })
    return record({
        "all_requirements_met": BOOL, "summary": STRING,
        "issues": record(findings),
    })
