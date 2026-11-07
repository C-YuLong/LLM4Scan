"""Shared strict wire schemas and validation for direct verification."""
from copy import deepcopy
from .result_view import SECTIONS

S = {"type": "string"}
TEXT = {"type": "string", "minLength": 1}
N = {"type": "integer", "minimum": 0}
B = {"type": "boolean"}

def obj(**fields):
    return dict(type="object", properties=fields, required=list(fields), additionalProperties=False)

def enum(*values):
    return dict(type="string", enum=list(values))

def array(items, minimum=0):
    return dict(type="array", items=items, minItems=minimum)

def optional(schema):
    result=deepcopy(schema)
    result["type"]=[result["type"], "null"]
    if "enum" in result: result["enum"].append(None)
    return result

TABLE=enum("chains", "members", "elements", "signals", "partitions", "configuration", "drc", "execution", "segments", "wrappers", "ctl_members", "ctl_chains", "def_chains", "log_events", "log_members")
FILTER=obj(field=TEXT, op=enum("eq", "ne", "prefix", "under", "port_format", "le"), value=S)
STRINGS=array(TEXT)
REPORT=obj(table=TABLE, operation=optional(enum("select", "count", "group", "unique", "forall", "set_equal", "subset", "disjoint", "chain_consistency", "drc_policy")),
           filters=optional(array(FILTER)), field=optional(TEXT), expected=optional(N), conditions=optional(array(FILTER)), cursor=optional(N),
           right_artifact=optional(TEXT), right_table=optional(TABLE), right_field=optional(TEXT), right_filters=optional(array(FILTER)),
           conditions_fields=optional(array(enum("clock", "edge"))), allowed_rules=optional(STRINGS), ignored_rules=optional(STRINGS), execution_artifact=optional(TEXT))
DESIGN=obj(operation=optional(enum("select", "count", "group", "ports", "connections", "hierarchy", "pin_catalog", "port_mappings")),
           top=optional(TEXT), scope=optional(S), types=optional(STRINGS), roles=optional(array(enum("DFF", "SFF", "FF", "ICG", "latch", "combinational"))),
           cursor=optional(N), instance=optional(TEXT), pin=optional(TEXT))
COMPARE=obj(operation=enum("icg_integrity", "icg_rewire", "ff_total", "ff_region_plain", "ff_mapping", "ff_changes", "no_scan_structure", "unchanged"),
            top=optional(TEXT), include=optional(STRINGS), exclude=optional(STRINGS), target_port=optional(TEXT), original_value=optional(enum("0", "1")),
            mapping=optional(array(obj(before=TEXT, after=TEXT))), scope=optional(S), properties=optional(array(enum("cell_type", "pins"),1)))
MEMBERSHIP=obj(expected_id=TEXT, actual_id=TEXT, relation=enum("equal", "subset", "disjoint"),
               expected_include=optional(STRINGS), expected_exclude=optional(STRINGS), actual_include=optional(STRINGS), actual_exclude=optional(STRINGS), require_unique=optional(B))
DELIVERY=obj(files=array(TEXT,1), runner_outputs=optional(array(enum("decision_log.json"))))
REQUESTS=dict(query_report=REPORT, query_design=DESIGN, compare_design=COMPARE, check_membership=MEMBERSHIP, check_delivery=DELIVERY)
TOOL_SCHEMAS={
    "trace_connection":obj(artifact=TEXT,top=TEXT,instance_path=TEXT,pin=TEXT,expected_top_port=TEXT,cursor=N),
    "query_cell_connections":obj(artifact=TEXT,top=TEXT,types=dict(type="array",items=TEXT,minItems=1,maxItems=16),pin=optional(TEXT),pin_role=optional(enum("clock","functional_enable","out","scan_test_enable","test_scan_in","test_scan_enable","test_scan_out")),include=optional(STRINGS),exclude=optional(STRINGS),cursor=N),
    "opensta_query":obj(artifact=TEXT,top=TEXT,script=TEXT,cursor=N),
    "read_tcl_commands":obj(artifact=TEXT,command=optional(TEXT),cursor=N),
    "read_verilog_blocks":obj(artifact=TEXT,kind=enum("module","instance"),module=optional(TEXT),name=optional(TEXT),top=optional(TEXT),instance_path=optional(TEXT),cursor=N),
    "list_evidence":obj(cursor=N),
    "list_artifacts":obj(), "read_lines":obj(artifact=TEXT,start=dict(type="integer",minimum=1),end=dict(type="integer",minimum=1),char_offset=optional(N)),
    "search_text":obj(artifact=TEXT,query=TEXT,cursor=N), "inspect_check":obj(evidence_id=TEXT,cursor=N,section=optional(enum(*SECTIONS))),
    "query_report":obj(artifact=TEXT,request=REPORT), "query_design":obj(artifact=TEXT,request=DESIGN),
    "compare_design":obj(before=TEXT,after=TEXT,request=COMPARE),
    "check_membership":obj(request=MEMBERSHIP), "check_delivery":obj(request=DELIVERY),
}


REF = obj(source=S, line_start={"type":"integer","minimum":1}, line_end={"type":"integer","minimum":1})
COUNTEREXAMPLE = obj(instance=TEXT, original_evidence_id=TEXT, actual_evidence_id=TEXT,
                     include=array(TEXT,1), exclude=array(TEXT))
ITEM = obj(requirement=S, task_ref=REF, status={"type":"string","enum":["PASS","FAIL","UNKNOWN"]},
           kind={"type":"string","enum":["structural","report","semantic","delivery"]},
           evidence_ids={"type":"array","items":S}, message=S,
           judgment_basis=enum("program", "model"),
           inapplicable_checks=optional(array(obj(evidence_id=TEXT,reason=TEXT))),
           counterexamples=optional(array(COUNTEREXAMPLE)))
FINAL_SCHEMA = obj(status={"type":"string","enum":["PASS","FAIL","UNKNOWN"]}, summary=S,
                   reviewed_task={"type":"boolean"}, items={"type":"array","items":ITEM},
                   context={"type":"array","items":obj(task_ref=REF, reason=S)},
                   unverified={"type":"array","items":S})


CHECKLIST_ITEM = obj(
    id=dict(type="string", minLength=1, maxLength=12),
    requirement=optional(dict(type="string", minLength=1, maxLength=96)),
    task_ref=optional(REF), state=enum("pending", "checking", "evidenced", "blocked"),
    evidence_ids=dict(type="array", items=TEXT, maxItems=4),
    gap=optional(dict(type="string", maxLength=80)))
TOOL_SCHEMAS["update_checklist"] = obj(items=dict(type="array", items=CHECKLIST_ITEM, minItems=1, maxItems=16))



class ToolArgumentError(ValueError):
    """Invalid wire parameters; no evidence operation has executed."""


def validate_schema(value, schema, path="$"):
    path=path.rstrip()
    kinds=schema["type"] if isinstance(schema["type"],list) else [schema["type"]]
    if value is None:
        if "null" in kinds:return
        raise ValueError(f"{path}: null is not allowed")
    kind=next((k for k in kinds if k!="null"),None)
    valid={"object":isinstance(value,dict),"array":isinstance(value,list),"string":isinstance(value,str),"boolean":type(value) is bool,"integer":type(value) is int}
    if not valid.get(kind,False):raise ValueError(f"{path}: expected {kind}")
    if "enum" in schema and value not in schema["enum"]:raise ValueError(f"{path}: allowed values are {schema['enum']}")
    if kind=="object":
        missing=set(schema["required"])-set(value);extra=set(value)-set(schema["properties"])
        if missing or extra:raise ValueError(f"{path}: missing fields {sorted(missing)}; unknown fields {sorted(extra)}")
        for key,child in schema["properties"].items():validate_schema(value[key],child,path+"."+key)
    elif kind=="array":
        if len(value)<schema.get("minItems",0):raise ValueError(f"{path}: requires at least {schema['minItems']} items")
        if len(value)>schema.get("maxItems",len(value)):raise ValueError(f"{path}: maximum is {schema['maxItems']} items")
        for i,item in enumerate(value):validate_schema(item,schema["items"],f"{path}[{i}]")
    elif kind=="string":
        if len(value)<schema.get("minLength",0):raise ValueError(f"{path}: must not be empty")
        if len(value)>schema.get("maxLength",len(value)):raise ValueError(f"{path}: maximum length is {schema['maxLength']}")
    elif kind=="integer" and value<schema.get("minimum",value):raise ValueError(f"{path}: minimum is {schema['minimum']}")

def request_args(name, **values):
    """Build full strict objects for Python callers, not a wire fallback."""
    schema=REQUESTS[name]
    result={k:None for k,v in schema["properties"].items() if isinstance(v["type"],list) and "null" in v["type"]}
    result.update(values)
    return result

def normalize(value):
    if isinstance(value,dict):return {k:normalize(v) for k,v in value.items() if v is not None}
    if isinstance(value,list):return [normalize(v) for v in value]
    return value

def validate_conditions(name, args):
    q=args.get("request",{})
    if name in REQUESTS:
        # Direct Python entry points accept partial requests; validate their
        # completed schema too, before any parser/native executor is invoked.
        validate_schema(request_args(name,**q), REQUESTS[name], '$.request')
    def only(allowed):
        extra=set(q)-set(allowed)
        if extra:
            raise ValueError(f"$.request: inapplicable parameters {sorted(extra)} for operation={q.get('operation', 'select')}; set them null or use the purpose-specific tool for that operation")
    def require(*fields):
        for field in fields:
            if not q.get(field):raise ValueError(f"$.request.{field}: required for operation {q.get('operation')}")
    if name=="read_lines" and args["start"]>args["end"]:raise ValueError("$.end: must be >= start")
    if name=="query_report":
        op=q.get("operation","select")
        common={'table','operation','filters','cursor'}
        extra={'select':set(),'count':{'expected'},'group':{'field'},'unique':{'field'},
               'forall':{'conditions'},'set_equal':{'field','right_artifact','right_table','right_field','right_filters'},
               'subset':{'field','right_artifact','right_table','right_field','right_filters'},
               'disjoint':{'field','right_artifact','right_table','right_field','right_filters'},
               'chain_consistency':{'right_artifact','conditions_fields'},
               'drc_policy':{'execution_artifact','allowed_rules','ignored_rules'}}
        only(common|extra.get(op,set()))
        if op in {"group","unique","set_equal","subset","disjoint"}:require("field")
        if op in {"set_equal","subset","disjoint","chain_consistency"}:require("right_artifact")
        if op=="forall":require("conditions")
        if op=="drc_policy":
            require("execution_artifact")
            if q["table"]!="drc":raise ValueError("$.request.table: drc_policy requires drc")
        if op=="chain_consistency" and q["table"]!="chains":raise ValueError("$.request.table: chain_consistency requires chains")
        if "expected" in q and op!="count":raise ValueError("$.request.expected: only supported for count")
    if name=="query_design":
        op=q.get('operation','select')
        only({'operation','top','cursor'}|({'scope','types','roles'} if op in {'select','count','group'} else {'instance','pin'} if op=='connections' else {'types'} if op=='pin_catalog' else set()))
        if op=='connections':require('instance','pin')
    if name=="compare_design":
        if q["operation"]=="ff_changes":
            extra=set(q)-{"operation","top","include","exclude"}
            if extra:raise ValueError(f"$.request: ff_changes uses only top/include/exclude; set other fields null: {sorted(extra)}")
        if q["operation"]=="icg_rewire":require("target_port")
        op=q['operation']
        fields={'unchanged':{'scope','properties'},'icg_rewire':{'include','exclude','target_port','original_value'},
                'ff_mapping':{'include','exclude','mapping'},'ff_region_plain':{'include','exclude'},'ff_changes':{'include','exclude'}}
        only({'operation','top'}|fields.get(op,set()))
