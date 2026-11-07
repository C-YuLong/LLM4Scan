"""Required JSON fields for the model's existing script/evidence contracts."""


def record(fields):
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


STRING = {"type": "string"}
BOOL = {"type": "boolean"}
INTEGER = {"type": "integer"}
TASK1 = record({
    "dofile": STRING,
    "requirements": {"type": "array", "minItems": 1, "items": record({"requirement": STRING, "command": STRING})},
    "repair": record({"located_object": STRING, "root_cause": STRING, "action": STRING}),
    "stages": {"type":"array","items":record({
        "id":{"type":"string","minLength":1,"maxLength":12},
        "label":{"type":"string","minLength":1,"maxLength":100},
        "command":STRING,"export_command":{"type":["string","null"]}})},
})
TASK2 = record({
    "dofile": STRING,
    "issues": {"type": "array", "minItems": 1, "items": record({key: STRING for key in (
        "phenomenon", "located_object", "root_cause", "violated_requirement", "original_command", "fix_action")})},
})
REPAIR = record({"dofile": STRING, "repair_action": STRING})


def verification(issue_count):
    return record({
        "all_requirements_met": BOOL, "summary": STRING,
        "issues": {"type": "array", "minItems": issue_count, "maxItems": issue_count, "items": record({
            "resolved": BOOL, "original_defect_supported": BOOL, "support_basis": STRING,
            "source": STRING, "line_start": INTEGER, "line_end": INTEGER,
        })},
    })
