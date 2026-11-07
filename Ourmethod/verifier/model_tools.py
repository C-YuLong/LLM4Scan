"""Purpose-specific model API over the backwards-compatible evidence executor."""
from copy import deepcopy
from hashlib import sha256
import json
import re
import uuid

from .tool_schema import (obj, enum, array, optional, S, TEXT, N, TABLE, FILTER,
                          STRINGS, TOOL_SCHEMAS, request_args, validate_schema,
                          normalize, ToolArgumentError)
from .result_view import SECTIONS, grouped_data

TOP = optional(TEXT)
ROLES = array(enum('DFF', 'SFF', 'FF', 'ICG', 'latch', 'combinational'))
SCHEMAS = {}
DESCRIPTIONS = {}
ROUTES = {}


def register(name, description, schema, route):
    SCHEMAS[name] = schema
    DESCRIPTIONS[name] = description
    ROUTES[name] = route


def report(operation, **fixed):
    def route(args):
        args = dict(args); artifact = args.pop('artifact')
        return 'query_report', dict(artifact=artifact, request=request_args('query_report', operation=operation, **args, **fixed))
    return route


def design(operation=None):
    def route(args):
        args = dict(args); artifact = args.pop('artifact')
        op = operation or args.pop('operation', args.pop('view', None))
        if 'instance_path' in args: args['instance'] = args.pop('instance_path')
        return 'query_design', dict(artifact=artifact, request=request_args('query_design', operation=op, **args))
    return route


def compare(operation=None):
    def route(args):
        args = dict(args); before = args.pop('before'); after = args.pop('after')
        op = operation or args.pop('check')
        return 'compare_design', dict(before=before, after=after, request=request_args('compare_design', operation=op, **args))
    return route


register('read_lines', 'Read inclusive 1-based source lines. Long text returns read_chunk continuation; copy next_read exactly. Partial text cannot prove absence.',
         obj(artifact=TEXT, start=dict(type='integer', minimum=1), end=dict(type='integer', minimum=1)),
         lambda a: ('read_lines', dict(a, char_offset=None)))
register('read_chunk', 'Continue saved source text using artifact and the opaque cursor from next_read. No file parsing or query is repeated.', obj(artifact=TEXT, cursor=TEXT), None)
register('read_evidence', 'Read saved evidence from the beginning with cursor="", or copy next_read. Classified evidence returns section summaries. FACT is not program proof.', obj(evidence_id=TEXT, cursor=S), None)
register('read_evidence_section', 'Read a named saved evidence section; cursor="" starts it. Use only a section returned for this evidence. Copy next_read for continuation.', obj(evidence_id=TEXT, section=enum(*SECTIONS), cursor=S), None)
register('list_evidence', 'Index of saved evidence, 20 per page. Start cursor=""; copy next_read. Read details with read_evidence/read_evidence_section.', obj(cursor=S), None)
register('list_artifacts', 'Exact registered artifact IDs and stage provenance; no GT access.', obj(), lambda a: ('list_artifacts', a))
register('search_text', 'Literal source search returning bounded excerpts and match counts. Use next_read for saved match pages or excerpt tails. Search is not acceptance proof.', obj(artifact=TEXT, query=TEXT), lambda a: ('search_text', dict(a, cursor=0)))
register('read_tcl_commands', 'Read exact Tcl commands and enclosing contexts without evaluation. command=null browses all commands. Comments and literal strings are excluded. Continue saved evidence with next_read.', obj(artifact=TEXT, command=optional(TEXT)), lambda a: ('read_tcl_commands', dict(a, cursor=0)))
register('read_instance_source', 'Resolve a concrete top-relative instance, then read its declaration or referenced definition. Use returned instance_path, not module_type. Library-only definitions are explicit; no equivalence proof.',
         obj(artifact=TEXT, top=TEXT, instance_path=TEXT, view=enum('declaration', 'definition')),
         lambda a: ('read_verilog_blocks', dict(artifact=a['artifact'], top=a['top'], instance_path=a['instance_path'], kind='instance' if a['view']=='declaration' else 'module', module=None, name=None, cursor=0)))
register('read_verilog_modules', 'Read module definitions by exact module_type; null browses definitions. Syntax only; no hierarchy elaboration.', obj(artifact=TEXT, module_type=optional(TEXT)),
         lambda a: ('read_verilog_blocks', dict(artifact=a['artifact'], kind='module', module=None, name=a['module_type'], top=None, instance_path=None, cursor=0)))
register('read_verilog_instances', 'Read local instance declarations in a containing module TYPE; null instance_name browses that module. Local names are not hierarchy paths; use read_instance_source for a concrete instance.',
         obj(artifact=TEXT, containing_module_type=TEXT, instance_name=optional(TEXT)),
         lambda a: ('read_verilog_blocks', dict(artifact=a['artifact'], kind='instance', module=a['containing_module_type'], name=a['instance_name'], top=None, instance_path=None, cursor=0)))

BASE = dict(artifact=TEXT, table=TABLE, filters=array(FILTER))
register('query_report_rows', 'Select complete saved report records with AND filters; returns a bounded preview. log_events/log_members require a .log artifact. Use available_fields. FACT only.', obj(**BASE), report('select'))
register('count_report_rows', 'Observe the number of filtered report rows. FACT only; use check_report_count to assert a number.', obj(**BASE), report('count'))
register('group_report_rows', 'Count filtered report rows by a real field from available_fields. FACT only.', obj(**BASE, field=TEXT), report('group'))
register('check_report_count', 'Assert a nonnegative expected report count. Incomplete or inapplicable empty populations cannot prove PASS.', obj(**BASE, expected=N), report('count'))
register('check_report_rows', 'Check every filtered report row against nonempty AND conditions. Filters select the population; conditions express the predicate.', obj(**BASE, conditions=array(FILTER, 1)), report('forall'))
register('check_report_unique', 'Check uniqueness of a field over filtered report rows.', obj(**BASE, field=TEXT), report('unique'))
register('check_report_sets', 'Compare report field sets: set_equal, subset (left <= right), disjoint. Both tables/fields are explicit; incomplete/empty data cannot prove PASS.',
         obj(**BASE, field=TEXT, relation=enum('set_equal', 'subset', 'disjoint'), right_artifact=TEXT, right_table=TABLE, right_field=TEXT, right_filters=array(FILTER)),
         lambda a: report(a['relation'])({k:v for k,v in a.items() if k!='relation'}))
register('check_chain_consistency', 'Check chain lengths/member indices and selected clock/edge consistency against the complete members report.',
         obj(artifact=TEXT, filters=array(FILTER), right_artifact=TEXT, conditions_fields=array(enum('clock', 'edge'))), report('chain_consistency', table='chains'))
register('check_drc_policy', 'Check DRC residual rules and execution ignored-rule configuration using both actual report artifacts.',
         obj(artifact=TEXT, execution_artifact=TEXT, allowed_rules=STRINGS, ignored_rules=STRINGS), report('drc_policy', table='drc'))

register('query_design_cells', 'Select/count/group leaf instances using exact library types and/or roles within scope ("" = root). group always groups by module_type; no field parameter. FACT only. Saved sets are complete despite preview pagination.',
         obj(artifact=TEXT, top=TOP, operation=enum('select', 'count', 'group'), scope=S, types=STRINGS, roles=ROLES), design())
register('query_design_structure', 'Read top ports, complete hierarchy, or port mappings. These are whole-design views without instance/type/scope filtering. FACT only.', obj(artifact=TEXT, top=TOP, view=enum('ports', 'hierarchy', 'port_mappings')), design())
register('query_pin_catalog', 'Read Liberty pin metadata for exact types associated with a netlist. Empty types means all instantiated leaf types. FACT only.', obj(artifact=TEXT, top=TOP, types=STRINGS), design('pin_catalog'))
register('query_pin_connection', 'Read one actual instance pin connection; use a concrete instance_path and discovered pin, never a guessed type/path.', obj(artifact=TEXT, top=TOP, instance_path=TEXT, pin=TEXT), design('connections'))

PAIR = dict(before=TEXT, after=TEXT, top=TOP)
REGION = dict(**PAIR, include=STRINGS, exclude=STRINGS)
register('query_ff_changes', 'Observe FF types/roles/populations and ordinary pin changes in before/after snapshots. FACT only; does not decide eligibility or functional equivalence. Classified counts overlap; use section next_read for details.', obj(**REGION), compare('ff_changes'))
register('check_icg_rewire', 'Check scalar target top INPUT: baseline ICGs in include/exclude with test pin tied to original_value must reconnect to target_port; ALL other baseline ICGs must retain their test connection. Scope selects rewire targets, not the population checked. Preserve stage provenance.', obj(**REGION, target_port=TEXT, original_value=enum('0', '1')), compare('icg_rewire'))
register('check_unchanged', 'Check selected cell_type/pins invariance in scope ("" = root). No functional equivalence proof.', obj(**PAIR, scope=S, properties=array(enum('cell_type', 'pins'), 1)), compare('unchanged'))
register('check_ff_mapping', 'Forward DFF-to-SFF explicit mapping predicate within include/exclude. Reverse replacement or missing eligibility can remain UNKNOWN; query_ff_changes supplies facts for interpretation.', obj(**REGION, mapping=array(obj(before=TEXT, after=TEXT))), compare('ff_mapping'))
register('check_ff_region_plain', 'Require ALL selected final FFs to be ordinary (non-scan) FFs, preserving the selected population. include selects the region; exclude removes regions. A scan FF is a failure of this predicate. Use only where the task requires ordinary FFs, never to verify a region required to contain scan FFs. Not reverse functional equivalence.', obj(**REGION), compare('ff_region_plain'))
register('check_design_global', 'Whole-design predicate: icg_integrity preserves ICG types/clock/enable; ff_total preserves FF total; no_scan_structure checks new scan structure. No regional filtering.', obj(**PAIR, check=enum('icg_integrity', 'ff_total', 'no_scan_structure')), compare())

CONNECTION = dict(artifact=TEXT, top=TEXT, types=dict(type='array', items=TEXT, minItems=1, maxItems=16), include=STRINGS, exclude=STRINGS)
for selector, schema in [('pin', TEXT), ('pin_role', TOOL_SCHEMAS['query_cell_connections']['properties']['pin_role'])]:
    name = 'query_connections_by_pin' if selector=='pin' else 'query_connections_by_role'
    nonnull = deepcopy(schema); nonnull['type']='string'
    if 'enum' in nonnull: nonnull['enum']=[v for v in nonnull['enum'] if v is not None]
    register(name, 'Discover scoped exact-type instances and local pin/net facts using '+selector+'. Roles come from explicit Liberty attributes. Local completeness does not prove top-input sources; use trace_connection for a concrete pin. Empty arrays mean unrestricted scope.',
             obj(**CONNECTION, **{selector:nonnull}),
             lambda a: ('query_cell_connections', dict({'pin':None, 'pin_role':None, **a}, cursor=0)))
for name in ('trace_connection', 'opensta_query'):
    fields={k:v for k,v in TOOL_SCHEMAS[name]['properties'].items() if k!='cursor'}
    description = ('Trace one concrete input pin across hierarchy to expected_top_port (scalar top INPUT). resolved yields directly_connected true/false and actual sources; unresolved/query_error yield null, never false. Traverses wires/ports, not logic. One trace cannot prove a population.' if name=='trace_connection' else
                   'Additional read-only native OpenSTA facts not covered by fixed tools. Program loads netlist/libraries; script uses safe Tcl and allowed get/report/help commands. Discover pins/types before querying. Errors are UNKNOWN. No file/process/network/design modification. Fresh Tcl variables per call.')
    register(name, description, obj(**fields), lambda a, name=name: (name, dict(a, cursor=0)))
for name in ('check_membership', 'check_delivery'):
    register(name, 'Compare complete saved member sets (subset means expected <= actual); empty/incomplete sets cannot prove PASS. Filters match paths/subtrees/globs; log sets prove reported operations only.' if name=='check_membership' else 'Check nonempty candidate files by relative names/globs. final.dofile means candidate script; runner_outputs are pending publication, not circuit proof.',
             obj(**{k:(dict(v, type=v['type'][0], **({'enum':[x for x in v['enum'] if x is not None]} if 'enum' in v else {})) if isinstance(v['type'],list) else v)
                    for k,v in TOOL_SCHEMAS[name]['properties']['request']['properties'].items()}),
             lambda a, name=name: (name, dict(request=request_args(name, **a))))
register('update_checklist', 'Upsert <=16 requirement progress groups. evidenced is progress, not PASS. New entries need requirement/task_ref; null retains fields on update.', TOOL_SCHEMAS['update_checklist'], lambda a: ('update_checklist', a))

GUIDE = '''Use only the offered purpose-specific tools. Arguments are flat; arrays [] mean unrestricted selection. Null is only for genuinely optional choices. Query tools return FACT; check tools prove only their own predicate, not original task semantics.
Verification runs BEFORE publication. final.dofile is the current candidate script, and decision_log.json is finalized by the runner after acceptance. Their absence from the candidate manifest is not a task violation. For delivery requirements use check_delivery with the actual required candidate filenames, files=["final.dofile", ...], and runner_outputs=["decision_log.json"] when required. This proves candidate readiness only; after publication the runner checks the final file set, bytes and decision's chosen run. Do not require the pending final_results tree to exist during candidate verification.
Use query_report_rows/count_report_rows/group_report_rows for report observations; check_report_* for explicit predicates. log_events/log_members require execution .log files. Filters AND using eq/ne/prefix/under/port_format/le; use available_fields.
Use query_design_cells for instances, query_design_structure for hierarchy/ports, query_pin_catalog for pins. query_connections_by_role/pin discovers local networks; trace_connection resolves one concrete input pin to a task-named top input. Use check_icg_rewire for its full before/after predicate, query_ff_changes for FF facts, and other check_* tools for explicit supported predicates. Choose snapshots using stage_artifacts.
For final-state requirements compare the original input with the final candidate netlist. Inspect intermediate snapshots only for an explicitly stage-specific obligation or to investigate a contradiction; do not repeat a complete final-state check across every intermediate pair. A missing proof is UNKNOWN, not FAIL.
Match each FF predicate to the original clause's population and required final role before querying or judging. If region A must be ordinary and the rest must be scan FFs, check_ff_region_plain uses include=[A], exclude=[]; the outside forward mapping uses include=[], exclude=[A]. A failed ordinary-FF check outside A contradicts no requirement that outside A be scan FFs; explain that check as inapplicable and collect the correct evidence.
Use read_tcl_commands for configuration; read_instance_source for concrete instance declarations/definitions. module_type is a referenced type, containing_module_type the parent type, instance_path a top-relative concrete path; never interchange them. read_verilog_modules/read_verilog_instances browse syntax and do not elaborate hierarchy. Variables/substitutions remain unevaluated.
All next_read values are {tool,arguments}; copy them exactly. Opaque cursor strings are not row/character offsets. read_evidence(evidence_id,cursor="") restores saved evidence; read_evidence_section uses a returned section. read_chunk continues saved source text. No continuation repeats the original query. Read omitted details only when needed; previews/samples/empty/partial results do not prove full coverage or absence. FACT does not establish functional equivalence. Errors/unsupported checks remain UNKNOWN.
'''

DYNAMIC_GUIDE='''Dynamic saved-evidence tools are enabled. Saved readers are loaded from current evidence and question scope each response; before evidence exists, no saved reader is offered. Classified readers are specific to reports, FF changes, structural checks, membership or delivery. Each evidence_id enum contains only compatible evidence; section lists only that family's actual groups. Connection evidence offers read_connection_details/coverage/unresolved, never records. Reports additionally offer read_report_columns/aggregates for saved metadata. No generic read_evidence_section is exposed; copy section_reads or next_read on the next response when that tool is offered. Evidence creation tools remain available; availability is not proof. Archive recovery uses the currently offered readers.'''


def public_hint(text):
    """Update executor diagnostics without rewriting operation names or facts."""
    names={'query_report':'query_report_rows', 'query_design':'query_design_cells',
           'query_cell_connections':'query_connections_by_role/pin',
           'compare_design':'the appropriate before/after check tool',
           'read_verilog_blocks':'read_instance_source/read_verilog_modules/read_verilog_instances',
           'inspect_check':'read_evidence/read_evidence_section', 'ff_region_plain':'check_ff_region_plain',
           'ff_changes':'query_ff_changes', 'ff_mapping':'check_ff_mapping'}
    text=text.replace('$.request.', '$.').replace('$.request:', '$:')
    text=text.replace('use section=null', 'use read_evidence with cursor=""')
    text=text.replace('requires section=null', 'requires read_evidence with cursor=""')
    text=text.replace('require section=null', 'require read_evidence with cursor=""')
    return re.sub(r'\b('+'|'.join(names)+r')\b',lambda m:names[m[0]],text)


class ModelTools:
    def __init__(self, tools):
        self.tools = tools
        self.cursors = {}
        self.cursor_keys = {}
        self.identity = sha256(json.dumps(tools.identity, sort_keys=True).encode()).hexdigest()
        tools.model_api = self

    @property
    def dynamic(self):
        return self.tools.settings.get('dynamic_evidence_tools',False)

    @property
    def definitions(self):
        schemas=self.available_schemas()
        result=[]
        for name,schema in schemas.items():
            description=DESCRIPTIONS.get(name,self.reader_description(name))
            if self.dynamic and name=='list_evidence':
                description='Index of saved evidence within the current question scope, up to 20 per page. Start cursor=""; copy next_read. Use returned recover/section_reads.'
            result.append(dict(type='function',name=name,strict=True,description=description,parameters=schema))
        return result

    @staticmethod
    def section_reader(record):
        groups=grouped_data(record)
        if groups is None:return None,()
        if record.get('table') is not None:
            name='read_report_records' if 'records' in groups else 'read_report_findings'
        else:
            name={'ff_changes':'read_ff_evidence','membership':'read_membership_evidence',
                  'delivery':'read_delivery_evidence'}.get(record.get('operation'),'read_structural_evidence')
        return name,tuple(groups)

    @staticmethod
    def reader_description(name):
        if name.startswith('read_connection_'):
            if name=='read_connection_coverage':return 'Read saved connection scope and coverage counts. No cursor or native query; local completeness is not cross-hierarchy proof.'
            return {'read_connection_details':'Read saved local connection rows.',
                    'read_connection_unresolved':'Read only saved unresolved connection rows, including empty results.'}.get(name,'')+' Start cursor=""; copy next_read. No native query is executed.'
        if name=='read_report_columns':return 'Read the saved report available_fields. No parsing or query is repeated; missing/empty columns do not prove absence.'
        if name=='read_report_aggregates':return 'Read saved report counts/aggregates from the original query. Does not compute a new grouping or upgrade FACT to PASS.'
        return 'Read saved '+name.removeprefix('read_')+' items. Start cursor=""; copy next_read. evidence_id and any section enum list only compatible saved evidence and groups; no query is repeated.'

    def reader_keys(self):
        state=getattr(self.tools,'guided_state',None)
        scope=set(state.question['artifacts']) if state is not None and state.question else None
        return [key for key,record in self.tools.records.items()
                if record.get('operation') not in {'list_artifacts','list_evidence','update_checklist'}
                and self.tools.record_queries.get(key,{}).get('tool') not in {'list_artifacts','list_evidence','update_checklist'}
                and (scope is None or self.paths('read_evidence',dict(evidence_id=key))<=scope)]

    def available_schemas(self):
        """Build wire schemas from saved capabilities, never a union section enum."""
        if not self.dynamic:return deepcopy(SCHEMAS)
        schemas={name:deepcopy(schema) for name,schema in SCHEMAS.items()
                 if name not in {'read_evidence','read_evidence_section','read_chunk','list_evidence'}}
        keys=self.reader_keys()
        if keys:
            schemas['list_evidence']=deepcopy(SCHEMAS['list_evidence'])
            schemas['read_evidence']=obj(evidence_id=enum(*keys),cursor=S)
        candidates={};sections_by_tool={}
        for key in keys:
            record=self.tools.records[key]
            name,sections=self.section_reader(record)
            if name:
                candidates.setdefault(name,[]).append(key)
                sections_by_tool[name]=sections
            if record.get('operation')=='query_cell_connections':
                for view in ('details','coverage','unresolved'):
                    candidates.setdefault('read_connection_'+view,[]).append(key)
            if record.get('table') is not None:
                for view in ('columns','aggregates'):
                    candidates.setdefault('read_report_'+view,[]).append(key)
        for name,ids in candidates.items():
            fields={} if name in {'read_connection_coverage','read_report_columns','read_report_aggregates'} else {'cursor':S}
            if len(sections_by_tool.get(name,()))>1:fields['section']=enum(*sections_by_tool[name])
            schemas[name]=obj(evidence_id=enum(*ids),**fields)
        artifacts=sorted({data['artifact'] for data in self.cursors.values()
                          if data['kind']=='text' and data['evidence_id'] in keys})
        if artifacts:schemas['read_chunk']=obj(artifact=enum(*artifacts),cursor=TEXT)
        return schemas

    def validate(self, name, args, offered=None):
        schemas=self.available_schemas()
        if name not in schemas:
            raise ToolArgumentError(f'$.tool: {name!r} is not offered in the current evidence state; use read_evidence if available or create relevant evidence first. Offered: {sorted(schemas)}')
        try:
            validate_schema(args, schemas[name])
            if offered is not None:
                sent={d['name']:d['parameters'] for d in offered}
                if name not in sent:raise ValueError(f'$.tool: {name!r} was not offered in this response; wait for the next tool definitions')
                validate_schema(args,sent[name])
        except ValueError as exc: raise ToolArgumentError(str(exc)) from exc

    def token(self, key, position, section=None, kind='evidence', **extra):
        data=dict(evidence_id=key, position=position, section=section, kind=kind, identity=self.identity, **extra)
        signature=json.dumps(data, sort_keys=True)
        if signature not in self.cursor_keys:
            cursor='page_'+uuid.uuid4().hex
            self.cursor_keys[signature]=cursor; self.cursors[cursor]=data
        return self.cursor_keys[signature]

    def decode(self, cursor, *, key=None, section=None, kind='evidence', artifact=None):
        data=self.cursors.get(cursor)
        if (not data or data['identity']!=self.identity or data['kind']!=kind or data['section']!=section
            or (key is not None and data['evidence_id']!=key) or (artifact is not None and data.get('artifact')!=artifact)):
            raise ToolArgumentError('$.cursor: invalid or mismatched saved page; copy next_read.tool/arguments exactly, or restart read_evidence with cursor=""')
        return data

    def paths(self, name, args):
        """Resolve scope even for saved evidence and indirect member-set inputs."""
        paths={v for k,v in args.items() if k in {'artifact','before','after','right_artifact','execution_artifact'} and isinstance(v,str)}
        keys=[args[k] for k in ('evidence_id','expected_id','actual_id') if k in args]
        if name=='read_chunk' and args.get('cursor') in self.cursors: keys.append(self.cursors[args['cursor']]['evidence_id'])
        visited=set()
        while keys:
            key=keys.pop()
            if key in visited: continue
            visited.add(key); record=self.tools.records.get(key,{})
            if record.get('artifact'): paths.add(record['artifact'])
            paths.update(record.get('artifacts',[])); keys.extend(record.get('source_evidence',[]))
            query=record.get('model_arguments',self.tools.record_queries.get(key,{}).get('arguments',{}))
            for field in ('artifact','before','after','right_artifact','execution_artifact'):
                if isinstance(query.get(field),str): paths.add(query[field])
            keys.extend(query[k] for k in ('expected_id','actual_id') if k in query)
        return paths

    def recover(self, key, section=None):
        if not self.dynamic:
            return dict(tool='read_evidence_section' if section is not None else 'read_evidence',
                        arguments=dict(evidence_id=key,cursor='',**({'section':section} if section is not None else {})))
        name,sections=self.section_reader(self.tools.records[key]) if section is not None else ('read_evidence',())
        args=dict(evidence_id=key, cursor='')
        if len(sections)>1:args['section']=section
        return dict(tool=name, arguments=args)

    def continuation(self,key,position,section=None):
        result=self.recover(key,section)
        result['arguments']['cursor']=self.token(key,position,section)
        return result

    def view(self, answer, key, section=None, *, position=0, record_override=None, _attempt=0, _preview_budget=None):
        """Translate executor-specific pagination without changing saved evidence."""
        record=record_override or self.tools.records[key]
        operation=record.get('operation')
        def convert(value, active_section=None):
            if isinstance(value,list): return [convert(v,active_section) for v in value]
            if not isinstance(value,dict): return value
            out={k:convert(v, k if k in SECTIONS else active_section) for k,v in value.items()
                 if k not in {'next_read','next_cursor','cursor','parameters','reading_hint','recover_hint','model_tool','model_arguments'}}
            for field in ('instruction','reason','next_step','message','limitations','recover'):
                if isinstance(out.get(field),str):out[field]=public_hint(out[field])
                if self.dynamic and isinstance(out.get(field),str):
                    out[field]=out[field].replace('read_evidence_section','the returned section_reads tool')
            if isinstance(out.get('diagnostics'),list):out['diagnostics']=[public_hint(v) if isinstance(v,str) else v for v in out['diagnostics']]
            nxt=value.get('next_read')
            if nxt:
                # Old classified next_read has no tool wrapper.
                args=nxt.get('arguments',nxt)
                sec=args.get('section',active_section)
                if operation in {'read_lines','search_text'} and 'start' in args:
                    artifact=args['artifact']
                    tok=self.token(key,0,kind='text',artifact=artifact,start=args['start'],end=args['end'],char_offset=args.get('char_offset',0))
                    out['next_read']=dict(tool='read_chunk',arguments=dict(artifact=artifact,cursor=tok))
                else:
                    pos=args.get('cursor',0)
                    out['next_read']=self.continuation(key,pos,sec)
            elif 'next_read' in value: out['next_read']=None
            # Search match-list pagination can coexist with excerpt continuation.
            if value.get('next_cursor') is not None and not (operation=='read_lines') and (not nxt or operation=='search_text'):
                continuation=self.continuation(key,value['next_cursor'],active_section)
                if nxt: out['next_matches']=continuation
                else: out['next_read']=continuation
            return out
        result=convert(deepcopy(answer), section)
        result['recover']=self.recover(key)
        if self.dynamic and record.get('operation')=='query_cell_connections' and 'id' not in result:
            result['capability_reads']={name:dict(tool='read_connection_'+name,arguments=dict(evidence_id=key,**({} if name=='coverage' else {'cursor':''}))) for name in ('details','coverage','unresolved')}
        limit=self.tools.settings['max_tool_result_chars']
        size=len(json.dumps(result,ensure_ascii=False))
        if size>limit and 'id' not in result:
            # Reserve space for translated opaque continuations, without rerunning
            # the query or changing the immutable evidence. Queries can be large.
            original=self.tools.settings
            budget=(_preview_budget or limit)-(size-limit)-256
            if _attempt>=5 or budget<=0:
                raise ToolArgumentError('Saved page metadata exceeds result budget; narrow the query or increase max_tool_result_chars')
            self.tools.settings=dict(original,max_tool_result_chars=budget)
            try: smaller=self.tools.preview(record,position,section=section)
            finally:self.tools.settings=original
            if 'query' in answer:smaller['query']=answer['query']
            converted=self.view(smaller,key,section,position=position,record_override=record,_attempt=_attempt+1,_preview_budget=budget)
            if len(json.dumps(converted,ensure_ascii=False))>limit:
                raise ToolArgumentError('Saved page metadata exceeds result budget; narrow the query or increase max_tool_result_chars')
            return converted
        return result

    def call(self, name, args, *, offered=None):
        try:
            self.validate(name,args,offered)
            return self._call(name,args)
        except ToolArgumentError as exc: raise ToolArgumentError(public_hint(str(exc))) from exc

    def _call(self, name, args):
        tools=self.tools
        if name in {'read_report_records','read_report_findings','read_ff_evidence','read_structural_evidence','read_membership_evidence','read_delivery_evidence'}:
            _,sections=self.section_reader(tools.records[args['evidence_id']])
            return self._read_saved('read_evidence_section',dict(args,section=args.get('section',sections[0])))
        if name.startswith('read_connection_') or name.startswith('read_report_'):
            return self.read_capability(name,args)
        if name in {'read_evidence','read_evidence_section','read_chunk','list_evidence'}:
            return self._read_saved(name,args)
        old, internal=ROUTES[name](args)
        answer=tools.call(old,internal)
        key=answer['evidence_id']; record=tools.records[key]
        record.update(model_tool=name,model_arguments=deepcopy(args))
        from .tools import encoded
        (tools.output_dir/(key+'.json')).write_text(encoded(record)+'\n',encoding='utf-8')
        tools.record_queries[key]=dict(tool=name,arguments=deepcopy(args),executor=old)
        return self.view(answer,key)

    def _read_saved(self, name, args):
        tools=self.tools
        if name in {'read_evidence','read_evidence_section','read_chunk','list_evidence'}:
            tools.current()
            if name=='list_evidence':
                offset=0 if args['cursor']=='' else self.decode(args['cursor'],kind='index')['position']
                from .context import evidence_row
                if self.dynamic:
                    keys=self.reader_keys();rows=[evidence_row(tools,key) for key in keys[offset:offset+20]]
                    tools.context_stats['evidence_index_pages']+=1
                    while rows and len(json.dumps(rows,ensure_ascii=False,default=str))>tools.settings['max_tool_result_chars']-700:rows.pop()
                    value=dict(status='FACT',proof=False,complete=True,rows=rows,operation='list_evidence',index_total=len(keys))
                else:
                    value=tools.list_evidence(offset);rows=value['rows'];keys=range(value['index_total'])
                    value.pop('index_cursor',None);value.pop('index_next_cursor',None)
                if not value['rows'] and offset<value['index_total']:
                    raise ToolArgumentError('Evidence index item exceeds result budget; increase max_tool_result_chars or read a known evidence_id directly')
                nxt=offset+len(rows) if offset+len(rows)<len(keys) else None
                value['next_read']=None if nxt is None else dict(tool=name,arguments=dict(cursor=self.token(None,nxt,kind='index')))
                return value
            section=args.get('section'); key=args.get('evidence_id')
            if name=='read_chunk':
                data=self.decode(args['cursor'],kind='text',artifact=args['artifact']);key=data['evidence_id']
            else:
                data=dict(position=0) if args['cursor']=='' else self.decode(args['cursor'],key=key,section=section)
            if key not in tools.records: raise ToolArgumentError('$.evidence_id: unknown or stale evidence; use list_evidence with cursor=""')
            tools.context_stats['evidence_restores']+=1
            record=tools.records[key]
            if name=='read_chunk':
                rows=[r for r in record['rows'] if data['start']<=r['line']<=data['end']]
                value=dict(record,operation='read_lines',rows=rows,char_offset=data['char_offset'],line_end=data['end'])
                value.pop('search',None)
                answer=tools.preview(value,0)
            else:
                try: answer=tools.preview(record,data['position'],section=section)
                except ValueError as exc: raise ToolArgumentError(str(exc)) from exc
            if name=='read_evidence' and record.get('model_tool'):
                answer['query']=self.saved_query(record)
            return self.view(answer,key,section,position=data['position'],record_override=value if name=='read_chunk' else None)

    def read_capability(self, name, args):
        tools=self.tools;tools.current()
        key=args['evidence_id'];record=tools.records[key]
        if name=='read_connection_details':return self._read_saved('read_evidence',args)
        tools.context_stats['evidence_restores']+=1
        if name=='read_connection_unresolved':
            section='connection_unresolved'
            pos=0 if args['cursor']=='' else self.decode(args['cursor'],key=key,section=section)['position']
            from .instance_identity import model_value
            rows=model_value(dict(record,rows=record['unresolved']))['rows']
            if pos>len(rows):raise ToolArgumentError('$.cursor: outside saved unresolved rows; restart with cursor=""')
            page=rows[pos:pos+tools.settings['page_rows']]
            def render():
                end=pos+len(page)
                return dict(evidence_id=key,run_id=record.get('run_id'),status=record['status'],proof=record['proof'],
                            complete=record['complete'],artifact=record['artifact'],view='unresolved',rows=page,
                            total_rows=len(rows),omitted=bool(pos or end<len(rows)),recover=self.recover(key),
                            next_read=dict(tool=name,arguments=dict(evidence_id=key,cursor=self.token(key,end,section))) if end<len(rows) else None)
            result=render()
            while len(json.dumps(result,ensure_ascii=False))>tools.settings['max_tool_result_chars'] and page:
                page.pop();result=render()
            if (not page and pos<len(rows)) or len(json.dumps(result,ensure_ascii=False))>tools.settings['max_tool_result_chars']:
                raise ToolArgumentError('Saved unresolved row exceeds result budget; increase max_tool_result_chars')
            return result
        fields={'read_connection_coverage':('observed','pin_summary','libraries','limitations'),
                'read_report_columns':('available_fields',),
                'read_report_aggregates':('observed',)}[name]
        result={k:deepcopy(record[k]) for k in ('evidence_id','run_id','artifact','sha256','operation','table','status','proof','complete',*fields) if k in record}
        result['recover']=self.recover(key)
        if name=='read_connection_coverage':
            result['query']=self.saved_query(record) if record.get('model_tool') else dict(omitted=True)
            result['limitations']='Coverage refers only to saved local pin resolution; no cross-hierarchy source proof.'
        if len(json.dumps(result,ensure_ascii=False,default=str))>tools.settings['max_tool_result_chars']:
            raise ToolArgumentError('Saved metadata exceeds result budget; increase max_tool_result_chars')
        return result

    def saved_query(self, record):
        """Restore archived query conditions without repeating an unbounded script."""
        args=deepcopy(record['model_arguments'])
        budget=self.tools.settings['max_tool_result_chars']//3
        query=dict(tool=record['model_tool'],arguments=args)
        if len(json.dumps(query,ensure_ascii=False))<=budget:return query
        # Explicitly flag unavailable conditions; never present a truncated
        # include/exclude/mapping array as the actual full query scope.
        for field in sorted(args,key=lambda k:len(json.dumps(args[k],ensure_ascii=False)),reverse=True):
            value=args[field]
            args[field]=dict(omitted=True,reason='Saved argument exceeds preview budget; increase max_tool_result_chars to restore it',
                             items=len(value) if isinstance(value,(list,str)) else None)
            query['arguments_omitted']=True
            if len(json.dumps(query,ensure_ascii=False))<=budget:break
        return query


def model_api(tools):
    return getattr(tools, 'model_api', None) or ModelTools(tools)
