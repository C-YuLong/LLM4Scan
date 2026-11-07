"""Candidate-bound, read-only evidence tools and complete program checks."""
from collections import Counter
from dataclasses import asdict
from fnmatch import fnmatchcase
from hashlib import sha256
import json
from pathlib import Path
import re
import time
import uuid

from .checklist import Checklist
from .ff_facts import collect_ff_changes
from .gaps import annotate
from stage_artifacts import model_stage_table
from .result_view import grouped_preview
from .text_view import text_preview
from .log_events import parse_log_events
from .source_reader import SOURCE_TOOLS, GUIDE, parse_source, source_view
from .opensta import OpenSTAPool, preview as opensta_preview, DESCRIPTION as OPENSTA_DESCRIPTION
from .batch_connections import native_script, decode_population, collect, preview as connection_preview, DESCRIPTION as CONNECTION_DESCRIPTION
from .trace_connection import native_script as trace_script, resolve as resolve_trace, preview as trace_preview, DESCRIPTION as TRACE_DESCRIPTION
from .instance_identity import model_value, instance_script, decode_instance, location
from .structural.library_facts import library_metadata
from .context import evidence_row, signature
from .tool_schema import TOOL_SCHEMAS, ToolArgumentError, validate_schema, validate_conditions, normalize

from .structural import NetlistDB, Operations, YosysRunner
from .structural.report_data import parse_report, run_assertion, matches
from .structural.proofs import scope_cells, role_cells
from .structural.loader import load_design


def encoded(value):
    return json.dumps(value, ensure_ascii=False, default=lambda x: sorted(x, key=str) if isinstance(x, set) else str(x))


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def artifact_guidance(key):
    """Navigation hints only; neither parse recognition nor acceptance evidence."""
    name = Path(key).name
    suffix = Path(key).suffix.lower()
    reports = {
        "scan_chain.rpt": ("Scan chain summary", "Chain counts, lengths, and chain-level attributes", "chains"),
        "scan_chain_cell.rpt": ("Actual scan chain members", "Which instances actually participate in scan chains", "members"),
        "scan_signal.rpt": ("Scan signal configuration", "Scan signal roles and settings", "signals"),
        "scan_cfg.rpt": ("Scan configuration", "Scan configuration parameters", "configuration"),
        "wrapper_cfg.rpt": ("Wrapper configuration", "Wrapper configuration parameters", "configuration"),
        "drc.rpt": ("DRC report", "Reported design-rule violations", "drc"),
    }
    if name in reports:
        role, use_for, table = reports[name]
        return dict(role=role, use_for=use_for, query_hint=f"query_report_rows with table={table}; parsing must be confirmed")
    if suffix in {".v", ".sv"}:
        role = "Original input netlist" if key.startswith("input/") else "Current candidate netlist (stage unspecified)"
        return dict(role=role, use_for="Cells, hierarchy, and actual connections",
                    query_hint="query_connections_by_role/pin for populations/local pins; trace_connection for a concrete pin to a top input; check_icg_rewire/query_ff_changes for before/after evidence; read_instance_source for declarations/definitions; opensta_query for additional native facts")
    if name == "stage_artifacts.json":
        return dict(role="Observed stage and snapshot provenance",use_for="Trace declared operations to executed commands and saved netlists; not correctness proof",query_hint="read_lines for full provenance")
    if suffix == ".lib":
        return dict(role="Cell library", use_for="Cell definitions and pin metadata",
                    query_hint="query_pin_catalog on an associated netlist; opensta_query for additional native library facts")
    if suffix in {".ctl", ".def"}:
        role, use_for, tables = (("Scan structure description", "Declared scan structures and members", "ctl_members or ctl_chains")
                                 if suffix == ".ctl" else ("Physical design exchange", "Scan chain descriptions, if present", "def_chains"))
        return dict(role=role, use_for=use_for, query_hint=f"query_report_rows with table={tables}; parsing must be confirmed")
    if suffix == ".log" and key.startswith("run/"):
        return dict(role="Execution log", use_for="Execution events, errors, and warnings",
                    query_hint="query_report_rows table=log_events/log_members for FF replacement events; search_text or read_lines, then copy next_read for other text")
    if suffix == ".md":
        return dict(role="Task source or supporting documentation", use_for="Original requirements, clarifications, or limitations; full text is supplied in task_sources",
                    query_hint="read_lines for specific source ranges")
    if suffix in {".dofile", ".tcl"}:
        return dict(role="Candidate script", use_for="Implementation intent, not proof of actual results; supplied in candidate_script when short",
                    query_hint="read_tcl_commands for complete commands and contexts; read_lines for raw source")
    if name == "execution.json":
        return dict(role="Execution summary", use_for="Runner-recorded execution status; also supplied in execution",
                    query_hint="read_lines for details")
    return dict(role="Other artifact", use_for="Additional source or output; inspect before deciding its relevance")


COMPACT_DESCRIPTIONS = {
    "trace_connection":TRACE_DESCRIPTION,
    "query_cell_connections":CONNECTION_DESCRIPTION,
    "opensta_query":OPENSTA_DESCRIPTION,
    "read_tcl_commands":"Read complete Tcl commands by exact name (null lists all); comments/literal strings excluded. Returns argument locations, parent contexts, raw source and end-exclusive positions. No evaluation. Copy next_read tool/arguments; partial blocks cannot prove absence. Source FACT is not program proof.",
    "read_verilog_blocks":"Prefer instance_path+top (module/name=null) to resolve types automatically; kind=module reads its type definition, kind=instance reads its declaration. Traditional mode: top/instance_path=null, module=containing module TYPE, name=local declaration name; null browses. Returns full shared declaration, type, source positions. No elaboration/external includes. Copy next_read tool/arguments to complete blocks. Use query_design for hierarchy and compare_design for actual changes.",
    "list_artifacts":"Current artifact index. IDs are exact; no GT access. Hashes are checked internally.",
    "read_lines":"Inclusive 1-based lines; char_offset=null means 0-based Unicode-character offset 0 in start line. Long lines are chunked (<=6000 chars); copy next_read into read_lines to continue. partial/omitted text never proves absence.",
    "search_text":"Literal search, paginated; long matches return bounded excerpts with char_start/char_end and next_read. Hits are observations, not complete proof.",
    "query_report":"Whole-table query/assertion. FACT returns records; assertions return findings; each is a count/items page. artifact is the file queried; execution_artifact is only for drc_policy. log_events/log_members require a registered .log artifact and parse SCAN-7600/7601 FF reports; filter event_id/event_kind (dff_replaced/sff_replaced), not mixed commands. These are reported operations, not netlist/actual-chain proof. Use available_fields, not invented columns. Filters AND: eq/ne exact string, prefix string prefix, under path plus descendants, port_format %d template, le integer upper bound. select/group/count with expected=null are FACT only. Set subset is left<=right. Nulls: select, no filters, cursor=0; right table/field inherit left. Empty/incomplete data cannot prove PASS.",
    "query_design":"Use query_design for structural selections, hierarchy, port mappings and pin catalogs; query_cell_connections for bulk local connections, trace_connection for a concrete pin to a top input. Verilog query using input libraries. roles are categories; types are exact library/design names; library-defined types with no instances yield zero matches. Nulls: select, auto top, root scope, cursor=0. Paths omit module top. Saved evidence holds full sets despite pagination. connections needs instance+pin. pin_catalog takes a netlist, not .lib.",
    "compare_design":"Registered input or current-run baseline vs a distinct current-run netlist. include/exclude match the selected BEFORE hierarchy paths/globs; stage_to_stage proves that transition, not original-design coverage; empty scope populations cannot pass. icg_rewire target_port is a scalar top INPUT signal; original_value defaults to '0'. ff_changes uses only top/include/exclude (other request fields null). Its FACT JSON groups changed, unchanged.ordinary_ff, unchanged.scan_ff, added, missing and connections.changed/unresolved; count is full, items are bounded previews. Use inspect_check section to recover a group; change and connection groups overlap; unchanged is not automatically a violation; task exceptions are model interpretations. ff_mapping is forward DFF->SFF only; mixed reversal stages return UNKNOWN. ff_region_plain checks region roles/population. unchanged uses scope+properties (default cell_type,pins); others use include/exclude. No complex chain or functional-equivalence proof.",
    "check_membership":"Results separate set_differences and duplicates. Full saved sets, not previews. subset: expected<=actual. Path/glob filters include descendants. Defaults: no filters, require_unique=true. Empty/incomplete sets cannot prove PASS. Original-design expected set proves coverage; two exports prove consistency only.",
    "inspect_check":"Restore/page saved evidence. For query_cell_connections, trace_connection, opensta_query and source blocks, section must be JSON null, never string \"null\" or \"records\". Named sections apply only to classified report/comparison/check results; use their next_read arguments. Use each section next_read as exact arguments. Long text returns next_read for character continuation; row cursors cannot advance within a long line. Full sets stay server-side.",
    "check_delivery":"Results separate matched_files, missing_files and empty_files. Nonempty candidate files; relative paths/globs. final.dofile=candidate script, *.log=run log. runner_outputs are pending publication, checked by controller after PASS. Not circuit proof.",
    "update_checklist":"Upsert <=16 short requirement groups by ID. New entries need requirement+task_ref; null retains those fields on updates. evidence_ids replaces with <=4 IDs. States track progress, not verdicts; keep gap short. Omitted IDs remain. Atomic update; latest snapshot is injected separately.",
    "list_evidence":"Saved evidence index, 20 per page; continue at index_next_cursor. inspect_check restores full records. Summaries do not add proof.",
}


class EvidenceTools:
    def __init__(self, source, run_dir, script, output_dir, settings, deadline, *, design_cache=None):
        self.source, self.run_dir, self.output_dir = map(lambda x: Path(x).resolve(), (source, run_dir, output_dir))
        self.settings, self.deadline = settings, deadline
        self.artifacts, self.texts, self.records = {}, {}, {}
        self.design_cache = {} if design_cache is None else design_cache
        self.prefix = self.run_dir.name + "_" + uuid.uuid4().hex[:10]
        self.op = Operations(example_limit=1000000)
        for root, prefix in ((self.source, "input"), (self.run_dir, "run")):
            for path in sorted(root.rglob("*")):
                relative = path.relative_to(root)
                if (not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root)
                    or any(x in {"verification", "gt_comparison", "gt_cache", "final_results"} for x in relative.parts)
                    or "golden" in path.name.lower() or path.name.startswith(".")):
                    continue
                if prefix == "input" and path.suffix not in {".v", ".sv", ".lib", ".ctl", ".md", ".def", ".tcl", ".dofile"}:
                    continue
                if prefix == "run" and not (relative.parts[0] in {"reports", "deliverables"} or path.name in {"execution.json", "stage_artifacts.json", run_dir.name + ".log"}):
                    continue
                self.artifacts[prefix + "/" + relative.as_posix()] = dict(path=path, sha256=digest(path))
        self.texts["candidate.dofile"] = script
        self.virtual_hashes = {key: sha256(value.encode()).hexdigest() for key, value in self.texts.items()}
        self.identity = {key: value["sha256"] for key, value in self.artifacts.items()} | self.virtual_hashes
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stage_path=self.artifacts.get("run/stage_artifacts.json")
        try:stage_record=json.loads(self.read("run/stage_artifacts.json")) if stage_path else None
        except (ValueError,OSError):stage_record=None
        self.stage_artifacts=model_stage_table(stage_record,self.artifacts,self.run_dir.name)
        self.checklist = Checklist(self)
        self.source_cache = {}
        self.opensta_pool = OpenSTAPool(self.output_dir,self.deadline)
        self.opensta_results = {}
        self.connection_results = {}
        self.trace_results = {}
        self.instance_identity_cache = {}
        self.record_queries = {}
        self.text_continuations = {}
        self.query_signatures = set()
        self.context_stats = dict(repeated_queries=0,evidence_restores=0,evidence_index_pages=0)
        self._definitions = {}
        for name in ('update_checklist', 'list_evidence', 'list_artifacts', 'trace_connection', 'query_cell_connections', 'opensta_query', 'read_tcl_commands', 'read_verilog_blocks', 'read_lines', 'search_text', 'query_report', 'query_design', 'compare_design', 'check_membership', 'inspect_check', 'check_delivery'):
            self.register(name)

    def register(self, name):
        # One description per tool, shared by compact and legacy contexts.
        self._definitions[name] = dict(type="function", name=name, description=COMPACT_DESCRIPTIONS[name], strict=True, parameters=TOOL_SCHEMAS[name])

    @property
    def definitions(self):
        return list(self._definitions.values())

    def close(self):
        self.opensta_pool.close()

    def current(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError("Verification tool deadline exhausted")
        for artifact in self.artifacts.values():
            if not artifact["path"].is_file() or digest(artifact["path"]) != artifact["sha256"]:
                self.close()
                raise ValueError("Candidate/input changed during verification")

    def read(self, key):
        if key in self.texts:
            return self.texts[key]
        if key not in self.artifacts:
            raise ValueError("Unknown artifact ID; use exact input/... or run/... ID from the initial manifest")
        item = self.artifacts[key]
        if digest(item["path"]) != item["sha256"]:
            raise ValueError("Stale artifact")
        return item["path"].read_text(encoding="utf-8", errors="replace")

    def _save(self, value):
        cursor=value.pop("_cursor",0)
        if value.get('operation') in SOURCE_TOOLS:
            # Saved source cursors address chunks. Resizing a model page must
            # never change those chunk boundaries on subsequent reads.
            value.setdefault('source_chunk_chars',max(1,min(1200,self.settings['max_tool_result_chars']//12)))
        if value.get("artifact") in self.identity:value.setdefault("sha256",self.identity[value["artifact"]])
        key = f"{self.prefix}_E{len(self.records)+1:04d}"
        value = dict(value, evidence_id=key, run_id=self.run_dir.name)
        answer=self.preview(value,cursor)
        for field in ("next_read","partial","omitted"):
            if field in answer:value[field]=answer[field]
        self.records[key] = value
        (self.output_dir / (key + ".json")).write_text(encoded(value) + "\n", encoding="utf-8")
        return answer

    def preview(self, value, cursor=0, section=None, legacy=False):
        # Model call audit metadata belongs on disk, not repeated in every page.
        value={k:v for k,v in value.items() if k not in {'model_tool','model_arguments'}}
        value=model_value(value)
        if value.get("operation")=="trace_connection":
            if section is not None:raise ToolArgumentError("Trace paths require section=null")
            return trace_preview(value,cursor,self.settings["max_tool_result_chars"],self.settings["page_rows"])
        if value.get("operation")=="query_cell_connections":
            if section is not None:raise ToolArgumentError("Connection populations require section=null")
            return connection_preview(value,cursor,self.settings["max_tool_result_chars"],self.settings["page_rows"])
        if value.get("operation")=="opensta_query":
            if section is not None:raise ToolArgumentError("OpenSTA output requires section=null")
            return opensta_preview(value,cursor,self.settings["max_tool_result_chars"])
        if value.get("operation") in SOURCE_TOOLS:
            if section is not None:raise ToolArgumentError("Source blocks require section=null")
            return source_view(value,cursor,self.settings["max_tool_result_chars"])
        if value.get("operation") in {"read_lines","search_text"} or "search" in value or (value.get("rows") and all("line" in r and "text" in r for r in value["rows"])):
            if section is not None:raise ValueError('$.section: text evidence requires section=null; follow read_lines next_read for text')
            return text_preview(value,cursor,self.settings["max_tool_result_chars"])
        if not legacy:
            grouped=grouped_preview(value,cursor,self.settings["page_rows"],self.settings["max_tool_result_chars"],section)
            if grouped is not None:return grouped
        result = {k:v for k,v in value.items() if k not in {"rows", "violations", "values"}}
        if "details" in result.get("observed",{}):
            observed=dict(result["observed"]);details=observed.pop("details")
            if isinstance(details,dict):
                observed["subchecks"]=[dict(kind=x.get("kind"),status=x.get("status"),checked=x.get("checked_count"),
                                             violations=len(x.get("differences",[])),unknown=len(x.get("unknown_items",[]))) for x in details.get("subchecks",[])]
                observed["unknown_examples"]=details.get("unknown_items",[])[:3]
            result["observed"]=observed
        rows = value.get("violations", value.get("rows", []))
        size = len(rows) if value.get("operation") in {"read_lines","list_evidence"} else self.settings["page_rows"]
        page = rows[cursor:cursor + size]
        # Reduce rows, never cut a JSON document or hide an omitted tail.
        while page and len(encoded(result | {"rows": page})) > self.settings["max_tool_result_chars"]:
            page = page[:-1]
        result.update(rows=page, total_rows=len(rows), cursor=cursor,
                      next_cursor=cursor+len(page) if cursor+len(page)<len(rows) else None,
                      omitted=cursor+len(page)<len(rows))
        if not page and rows and cursor < len(rows):
            result["reason"] = "Row exceeds result budget; narrow query or read source lines"
        if len(encoded(result)) > self.settings["max_tool_result_chars"]:
            result = {k: result[k] for k in ("evidence_id","run_id","status","proof","complete","next_cursor") if k in result} | {"omitted": True, "reason": "Metadata exceeds result budget; narrow query"}
        return result

    def call(self, name, args):
        if name not in self._definitions or not isinstance(args, dict):
            raise ToolArgumentError("$: unknown tool or invalid arguments; allowed tools: " + ", ".join(self._definitions))
        try:
            validate_schema(args, self._definitions[name]["parameters"])
            args=normalize(args)
            validate_conditions(name,args)
        except ValueError as exc:
            raise ToolArgumentError(str(exc)) from exc
        self.current()
        for field in ("artifact","before","after"):
            value=args.get(field)
            if isinstance(value,str) and value not in self.artifacts and value not in self.texts:
                aliases=[prefix+value for prefix in ("run/","input/") if prefix+value in self.artifacts]
                if len(aliases)==1:args[field]=aliases[0]
        if name not in {"update_checklist", "list_artifacts", "list_evidence"}:
            key=signature(name,args)
            if key in self.query_signatures:self.context_stats["repeated_queries"]+=1
            self.query_signatures.add(key)
        if name == "inspect_check":
            self.context_stats["evidence_restores"]+=1
            if args["evidence_id"] not in self.records:raise ValueError("$.evidence_id: unknown or stale evidence ID")
            record=self.records[args["evidence_id"]]
            try:answer=self.preview(record,args["cursor"],section=args.get("section"),legacy=args.get("section") is None)
            except ValueError as exc:raise ToolArgumentError(str(exc)) from exc
            if "next_read" in answer and (record.get("operation") in {"read_lines","search_text"} or "search" in record):
                self.text_continuations[args["evidence_id"]]=answer["next_read"]
            return answer
        try:
            value = getattr(self, name)(**args)
        except ValueError as exc:
            if name=="update_checklist":raise ToolArgumentError("$.items: "+str(exc)) from exc
            raise
        answer=self._save(annotate(value))
        self.record_queries[answer["evidence_id"]]=dict(tool=name,arguments=args)
        return answer

    def list_evidence(self, cursor):
        self.context_stats["evidence_index_pages"]+=1
        keys=[key for key,query in self.record_queries.items() if query["tool"] not in {"update_checklist","list_artifacts","list_evidence"}]
        page=keys[cursor:cursor+20]
        rows=[evidence_row(self,key) for key in page]
        while rows and len(encoded(rows))>self.settings["max_tool_result_chars"]-700:rows.pop()
        return dict(status="FACT",proof=False,complete=True,rows=rows,
                    operation="list_evidence",index_total=len(keys),index_cursor=cursor,
                    index_next_cursor=cursor+len(rows) if cursor+len(rows)<len(keys) else None)

    def model_artifact_manifest(self):
        if self.settings.get("context_mode","compact")=="legacy":return self.artifact_manifest()
        rows=[]
        for key in self.identity:
            info=artifact_guidance(key)
            purpose=info["role"]
            hint=info.get("query_hint", "")
            # A single reusable hint, not repeated prose about parser guarantees.
            if "table=" in hint:purpose+="; "+hint.split(";",1)[0]
            links=[s["id"] for s in self.stage_artifacts["stages"] if s.get("after_snapshot")==key]
            row=dict(artifact_id=key,purpose=purpose,stage="input" if key.startswith("input/") else "candidate/unknown")
            if links:row.update(stage_ids=links,stage="recorded_snapshot")
            rows.append(row)
        return rows

    def update_checklist(self, items):
        return self.checklist.update(items)

    def artifact_manifest(self):
        return [dict(id=key, sha256=checksum,
                     bytes=self.artifacts[key]["path"].stat().st_size if key in self.artifacts
                     else len(self.texts[key].encode("utf-8")),
                     **artifact_guidance(key),
                     stage_ids=[s["id"] for s in self.stage_artifacts["stages"] if s.get("after_snapshot")==key]) for key, checksum in self.identity.items()]

    def list_artifacts(self):
        rows = ([dict(artifact=row["id"], **row) for row in self.artifact_manifest()]
                if self.settings.get("context_mode","compact")=="legacy" else self.model_artifact_manifest())
        return dict(status="FACT", proof=False, complete=True, rows=rows)

    def trace_connection(self, artifact, top, instance_path, pin, expected_top_port, cursor):
        request=dict(artifact=artifact,top=top,instance_path=instance_path,pin=pin,expected_top_port=expected_top_port)
        key=signature('trace_connection',request)
        if key not in self.trace_results:
            if cursor:raise ToolArgumentError('Start trace at cursor=0')
            native=self.opensta_query(artifact,top,trace_script(instance_path,pin,expected_top_port),0)
            facts=resolve_trace(native,expected_top_port)
            facts['observed']=dict(directly_connected=facts['directly_connected'],source_is_unique=facts['source_is_unique'],
                                   source_count=len(facts['actual_sources']),path_steps=len(facts['rows']))
            if facts['resolution_status']!='resolved':
                facts.update(gap_category='execution_error' if facts['resolution_status']=='query_error' else 'evidence_missing',
                             next_step='Inspect unresolved_reason and native diagnostics; never infer disconnection from unresolved/error traces.')
            self.trace_results[key]=dict(facts,status='FACT' if facts['resolution_status']=='resolved' else 'UNKNOWN',
                                         native=native,artifact=artifact,sha256=self.identity[artifact],
                                         top=top,instance_path=instance_path,pin=pin,expected_top_port=expected_top_port,
                                         libraries=native.get('libraries',[]),version=native.get('version'))
        if cursor>len(self.trace_results[key]['rows']):raise ToolArgumentError('Trace cursor outside saved path')
        return dict(self.trace_results[key],operation='trace_connection',proof=False,parameters={**request,'cursor':cursor},_cursor=cursor)

    def query_cell_connections(self, artifact, top, types, cursor, pin=None, pin_role=None, include=None, exclude=None):
        if (pin is None)==(pin_role is None):raise ToolArgumentError('Supply exactly one of pin or pin_role; set the other null')
        if len(set(types))!=len(types):raise ToolArgumentError('types must be distinct exact cell names')
        request=dict(artifact=artifact,top=top,types=types,pin=pin,pin_role=pin_role,include=include,exclude=exclude)
        key=signature('query_cell_connections',request)
        if key not in self.connection_results:
            if cursor:raise ToolArgumentError('Start connection query at cursor=0')
            native=self.opensta_query(artifact,top,native_script(types,top),0)
            try:
                if native['status']!='FACT':raise ValueError('Native connection discovery failed; inspect native diagnostics')
                metadata_key=('library_metadata',tuple(v['sha256'] for k,v in self.artifacts.items() if k.startswith('input/') and v['path'].suffix=='.lib'))
                if metadata_key not in self.design_cache:
                    metadata={}
                    for k,v in self.artifacts.items():
                        if k.startswith('input/') and v['path'].suffix=='.lib':
                            for typ,record in library_metadata(self.read(k)).items():
                                if typ in metadata and metadata[typ]!=record:
                                    record={**record,'unknowns':record.get('unknowns',[])+['Conflicting library definitions']}
                                metadata[typ]=record
                    self.design_cache[metadata_key]=metadata
                facts=collect(decode_population(native['output']),self.design_cache[metadata_key],request,self._filter)
                complete=not facts['unresolved']
                result=dict(status='FACT' if complete else 'UNKNOWN',complete=complete,reason='' if complete else 'Incomplete pin-role resolution; inspect unresolved rows',**facts)
            except (ValueError,KeyError,UnicodeError) as exc:
                result=dict(status='UNKNOWN',complete=False,rows=[],unresolved=[],groups=[],observed={},reason=str(exc))
            self.connection_results[key]=dict(result,native=native,version=native.get('version'),
                                              native_diagnostics=native.get('diagnostics',[]),native_message=native.get('result','')[:500],
                                              artifact=artifact,sha256=self.identity[artifact],libraries=native.get('libraries',[]))
        return dict(self.connection_results[key],operation='query_cell_connections',proof=False,
                    parameters={**request,'cursor':cursor},_cursor=cursor)

    def opensta_query(self, artifact, top, script, cursor):
        if artifact not in self.artifacts or Path(artifact).suffix.lower() not in {'.v','.sv'}:
            raise ToolArgumentError('opensta_query requires a registered Verilog artifact')
        if re.search(r'`\s*include\b',self.read(artifact)):
            raise ToolArgumentError('External Verilog includes are not registered OpenSTA sources')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*',top):raise ToolArgumentError('top requires an exact simple module identifier')
        libraries=[(k,v) for k,v in self.artifacts.items() if k.startswith('input/') and v['path'].suffix=='.lib']
        identity=(artifact,self.identity[artifact],tuple((k,v['sha256']) for k,v in libraries),top)
        cache_key=(identity,script)
        if cache_key not in self.opensta_results:
            if cursor:raise ToolArgumentError('Start query at cursor=0 before paging its saved output')
            answer=self.opensta_pool.query(identity,self.artifacts[artifact]['path'],[v['path'] for _,v in libraries],top,script)
            output=('LOAD\n'+answer.get('load_output','')+'\nQUERY\n'+answer.get('output','')+
                    '\nRESULT\n'+answer.get('result','')+'\nDIAGNOSTICS\n'+'\n'.join(answer.get('diagnostics',[])))
            self.opensta_results[cache_key]={**answer,'output':output}
        result=dict(self.opensta_results[cache_key])
        return dict(result,operation='opensta_query',proof=False,artifact=artifact,sha256=self.identity[artifact],
                    libraries=[dict(artifact=k,sha256=v['sha256']) for k,v in libraries],
                    parameters=dict(artifact=artifact,top=top,script=script,cursor=cursor),_cursor=cursor)

    def _source_blocks(self, artifact, language, parameters):
        suffix=Path(artifact).suffix.lower()
        if artifact not in self.identity:raise ToolArgumentError('Unknown artifact ID; use list_artifacts')
        if suffix not in ({'.tcl','.dofile'} if language=='tcl' else {'.v','.sv'}):
            raise ToolArgumentError('Source reader requires a registered '+language+' file')
        text=self.read(artifact)
        checksum=self.identity[artifact]
        key=(language,artifact,checksum)
        if key not in self.source_cache:
            self.source_cache[key]=parse_source(language,text,self.deadline)
        parsed=self.source_cache[key]
        blocks=parsed['blocks']
        if language=='tcl':
            if parameters.get('command') is not None:blocks=[b for b in blocks if b['name']==parameters['command']]
        else:
            blocks=[b for b in blocks if b['kind']==parameters['kind']
                    and (parameters.get('module') is None or b.get('module')==parameters['module'])
                    and (parameters.get('name') is None or b['name']==parameters['name'])]
            definitions={}
            for block in parsed['blocks']:
                if block['kind']=='module':definitions.setdefault(block['name'],[]).append(block)
            enriched=[]
            for block in blocks:
                target=block['name'] if block['kind']=='module' else block['cell_type']
                candidates=definitions.get(target,[])
                enriched.append(dict(block,definition_location=location(artifact,checksum,candidates[0]) if len(candidates)==1 else None))
            blocks=enriched
        return dict(status='FACT' if parsed['complete'] else 'UNKNOWN',proof=False,complete=parsed['complete'],
                    artifact=artifact,sha256=self.identity[artifact],operation='read_tcl_commands' if language=='tcl' else 'read_verilog_blocks',
                    parameters=parameters,blocks=blocks,diagnostics=parsed['diagnostics'],
                    observed=dict(block_count=len(blocks)),_cursor=parameters['cursor'])

    def read_tcl_commands(self, artifact, cursor, command=None):
        return self._source_blocks(artifact,'tcl',dict(artifact=artifact,command=command,cursor=cursor))

    def read_verilog_blocks(self, artifact, kind, cursor, module=None, name=None, top=None, instance_path=None):
        parameters=dict(artifact=artifact,kind=kind,module=module,name=name,top=top,instance_path=instance_path,cursor=cursor)
        if instance_path is None:
            if top is not None:raise ToolArgumentError('top is only used with instance_path; use null in traditional mode')
            return self._source_blocks(artifact,'verilog',parameters)
        if not top or module is not None or name is not None:
            raise ToolArgumentError('Instance mode requires top and instance_path; module/name must be null')
        native=self.opensta_query(artifact,top,instance_script(instance_path,top),0)
        def unknown(reason,identity=None,**extra):
            return dict(status='UNKNOWN',proof=False,complete=False,artifact=artifact,sha256=self.identity[artifact],
                        operation='read_verilog_blocks',parameters=parameters,blocks=[],diagnostics=[reason],reason=reason,
                        resolved_instance=identity,identity_evidence=native,_cursor=cursor,**extra)
        if native['status']!='FACT':return unknown('Instance identity lookup failed: '+str(native.get('result','')))
        key=(artifact,self.identity[artifact],top,instance_path)
        try:
            if key not in self.instance_identity_cache:self.instance_identity_cache[key]=decode_instance(native['output'])
            identity=self.instance_identity_cache[key]
        except (ValueError,KeyError) as exc:return unknown(str(exc))
        selected_module=identity['containing_module_type'] if kind=='instance' else None
        selected_name=identity['instance_name'] if kind=='instance' else identity['module_type']
        result=self._source_blocks(artifact,'verilog',{**parameters,'module':selected_module,'name':selected_name})
        blocks=result['blocks']
        if len(blocks)!=1:
            references=[]
            if kind=='module' and not blocks:
                for lib,record in self.artifacts.items():
                    if lib.startswith('input/') and record['path'].suffix=='.lib':
                        libkey=('identity_library',record['sha256'])
                        if libkey not in self.design_cache:self.design_cache[libkey]=library_metadata(self.read(lib))
                        if identity['module_type'] in self.design_cache[libkey]:references.append(dict(artifact=lib,sha256=record['sha256']))
            return unknown('Only Liberty definition is available' if references else 'No unique source declaration matches the resolved instance',
                           identity,definition_kind='liberty_only' if references else 'unresolved',library_references=references,definition_location=None)
        block=blocks[0]
        # Verify the parsed local declaration references the actual design type.
        if kind=='instance' and block['cell_type']!=identity['module_type']:
            return unknown('Parsed declaration type conflicts with the design identity',identity)
        definition=block.get('definition_location')
        block={**block,**identity,'definition_location':definition}
        result.update(parameters=parameters,blocks=[block],resolved_instance={**identity,'definition_location':definition},identity_evidence=native,definition_kind='verilog')
        return result

    def read_lines(self, artifact, start, end, char_offset=0):
        lines = self.read(artifact).splitlines()
        if not 1 <= start <= end:
            raise ValueError("Line range outside artifact")
        if lines and start>len(lines):raise ValueError("Line range outside artifact")
        if char_offset and (not lines or char_offset>len(lines[start-1])):raise ValueError("char_offset exceeds the selected source line length")
        end=min(end,len(lines))
        rows = [dict(line=n, text=lines[n-1]) for n in range(start,end+1)]
        return dict(status="FACT", proof=False, complete=True, artifact=artifact, rows=rows,
                    operation="read_lines",char_offset=char_offset,line_start=start, line_end=end,line_count=len(lines),eof=end==len(lines))

    def search_text(self, artifact, query, cursor):
        if not query:
            raise ValueError("Empty search")
        rows = [dict(line=n,text=line) for n,line in enumerate(self.read(artifact).splitlines(),1) if query in line]
        return dict(status="FACT", proof=False, complete=True, artifact=artifact,
                    rows=rows, operation="search_text",search=query, search_page_rows=self.settings["page_rows"],match_count=len(rows),_cursor=cursor)

    def table(self, artifact, table):
        text = self.read(artifact)
        result = parse_log_events(text,table,artifact) if table in {"log_events","log_members"} else parse_report(text, table, artifact)
        if table in {"segments", "wrappers", "ctl_members", "ctl_chains", "def_chains"}:
            result = self.extra_table(text, table, artifact)
        result["complete"] = bool(result["recognized"] and not result["unknown"])
        if table in {"chains","members"}:
            for row in result["rows"]:
                match=re.fullmatch(r"([IW])\s+(\S+)",row.get("chain",""))
                if match:row.update(chain_kind=match[1],chain_number=match[2])
        return result

    @staticmethod
    def extra_table(text, table, source):
        rows, unknown, recognized = [], [], False
        if table in {"segments", "wrappers"}:
            marker = "SegmentProperty" if table == "segments" else "WrapperStyle"
            header = next(((n,line) for n,line in enumerate(text.splitlines(),1) if marker in line), None)
            if header:
                recognized=True
                columns=[(m.start(),m.group()) for m in re.finditer(r"\S+",header[1])]
                for n,line in enumerate(text.splitlines()[header[0]:],header[0]+1):
                    if not line.strip() or re.fullmatch(r"[\s=-]+",line) or line.strip().startswith(("Total ","[INFO]","Design:")):
                        continue
                    raw={key:line[offset:(columns[i+1][0] if i+1<len(columns) else len(line))].strip() for i,(offset,key) in enumerate(columns)}
                    row={"_source":source,"_line":n,**raw}
                    if not any(raw.values()): unknown.append(n)
                    else: rows.append(row)
        elif table in {"ctl_members","ctl_chains"}:
            recognized=bool(re.search(r"\bScanStructures\b",text))
            # ScanChain blocks contain no nested braces in supported flat export.
            blocks=list(re.finditer(r'ScanChain\s+"([^"]+)"\s*\{([^{}]*)\}',text))
            declared=len(re.findall(r"\bScanChain\s+",text))
            if declared != len(blocks): unknown.append(0)
            for block in blocks:
                name,body=block[1],block[2]
                line=text[:block.start()].count("\n")+1
                length=re.search(r"ScanLength\s+(\d+)\s*;",body)
                if table=="ctl_chains":
                    if not length: unknown.append(line)
                    else: rows.append(dict(chain=name,length=int(length[1]),_source=source,_line=line))
                else:
                    cells=re.search(r"ScanCells\s+(.*?);",body,re.S)
                    if cells is None: unknown.append(line)
                    else:
                        for member in re.findall(r'"([^"]+)"',cells[1]):
                            rows.append(dict(chain=name,instance=member,_source=source,_line=line))
        else:
            count=re.search(r"SCANCHAINS\s+(\d+)\s*;",text)
            recognized=bool(count and "END SCANCHAINS" in text)
            rows=[dict(chain=m[1],_source=source,_line=text[:m.start()].count("\n")+1) for m in re.finditer(r"(?m)^\s*-\s+SubScanChain_(\S+)",text)]
            if not count or int(count[1])!=len(rows): unknown.append(0)
        return dict(rows=rows,unknown=unknown,recognized=recognized)

    def validate_report_artifacts(self, artifact, request):
        """Reject incompatible log inputs before reading or parsing either table."""
        log_tables={"log_events","log_members"}
        operation=request.get("operation","select")
        selections=[("$.artifact",artifact,request["table"])]
        if operation in {"set_equal","subset","disjoint"}:
            selections.append(("$.request.right_artifact",request.get("right_artifact"),request.get("right_table",request["table"])))
        for field,key,table in selections:
            if table not in log_tables:continue
            registered=key in self.artifacts
            if not registered and isinstance(key,str):
                aliases=[prefix+key for prefix in ("run/","input/") if prefix+key in self.artifacts]
                if len(aliases)==1:key=aliases[0];registered=True
            path=self.artifacts[key]["path"] if registered else Path(str(key))
            suffix=path.suffix.lower()
            if not registered or suffix!=".log":
                kind="Verilog netlist" if suffix in {".v",".sv"} else (suffix+" file" if suffix else "file without a suffix")
                available=sorted(k for k,v in self.artifacts.items() if v["path"].suffix.lower()==".log")
                raise ToolArgumentError(f"{field}: table={table} requires a registered .log execution log; received {key!r} ({kind}, {'registered' if registered else 'not registered'}). Set {field} to a log artifact. Available .log artifacts: {available}")
        if "execution_artifact" in request and operation!="drc_policy":
            raise ToolArgumentError(f"$.request.execution_artifact: only supported for drc_policy; received {request['execution_artifact']!r} for operation={operation}. Put the file to query in $.artifact (or $.request.right_artifact for the right-hand table), and set execution_artifact=null.")

    def query_report(self, artifact, request):
        q=request
        self.validate_report_artifacts(artifact,q)
        try:validate_conditions('query_report',dict(request=normalize(request)))
        except ValueError as exc:raise ToolArgumentError(str(exc)) from exc
        table=q["table"]; parsed=self.table(artifact,table)
        tables={table:parsed}
        operation=q.get("operation","select")
        supported={"select","count","group","unique","forall","set_equal","subset","disjoint","chain_consistency","drc_policy"}
        if operation not in supported:
            raise ValueError("Unsupported report operation")
        for name in ("filters","conditions","right_filters"):
            for rule in q.get(name,[]):
                if (not isinstance(rule,dict) or set(rule)!={"field","op","value"} or not isinstance(rule["field"],str)
                    or rule["op"] not in {"eq","ne","prefix","under","port_format","le"} or not isinstance(rule["value"],str)):
                    raise ValueError("Invalid report filter")
        query=dict(table=table,operation=operation,filters=q.get("filters",[]),field=q.get("field",""),expected=str(q.get("expected","")),
                   conditions=q.get("conditions",[]),right_table=q.get("right_table", "right"),right_field=q.get("right_field",q.get("field","")),
                   right_filters=q.get("right_filters",[]),conditions_fields=q.get("conditions_fields",[]),
                   allowed_rules=q.get("allowed_rules",[]),ignored_rules=q.get("ignored_rules",[]),allow_empty=False)
        if operation in {"set_equal","subset","disjoint"}:
            query["right_table"]="__right"
            tables["__right"]=self.table(q["right_artifact"],q.get("right_table",table))
        if operation=="chain_consistency":
            tables["members"]=self.table(q["right_artifact"],"members")
        if operation=="drc_policy":
            tables["execution"]=self.table(q["execution_artifact"],"execution")
        if operation=="count" and "expected" not in q:
            # A plain count is an observation, never a failed assertion against ''.
            query["operation"]="select"
        if operation=="count" and "expected" in q and not str(q["expected"]).isdigit():
            raise ValueError("Count assertion requires a nonnegative expected number")
        result=run_assertion(query,tables)
        rows=[r for r in parsed["rows"] if matches(r,query["filters"])]
        status={"passed":"PASS","failed":"FAIL","unverified":"UNKNOWN"}[result["verdict"]]
        fact=operation in {"select","group"} or (operation=="count" and "expected" not in q)
        complete=all(t.get("complete",False) for t in tables.values()) and not result.get("unknown")
        if not complete and not fact: status="UNKNOWN"
        return dict(status="FACT" if fact else status, proof=not fact, complete=complete,
                    artifact=artifact,table=table,operation=operation,parameters=query,
                    available_fields=sorted({key for row in parsed["rows"] for key in row if not key.startswith("_")}),
                    rows=(rows if operation=="select" else []) if fact else result["details"], values=[r["instance"] for r in rows if "instance" in r] or [r["chain"] for r in rows if "chain" in r],
                    observed={k:v for k,v in result.items() if k!="details"},population_origin="log_event" if table in {"log_events","log_members"} else "candidate_report",
                    proof_scope="reported_log_events" if table in {"log_events","log_members"} else "report_predicate",
                    reason="Incomplete log event parsing" if table in {"log_events","log_members"} and not complete else "",
                    parse_issues=parsed.get("issues",[]),parse_unknown=parsed["unknown"],_cursor=q.get("cursor",0))

    def design(self, artifact, top=None):
        item=self.artifacts[artifact]
        if item["path"].suffix not in {".v",".sv"}:
            raise ValueError("Expected registered Verilog")
        libraries=[v["path"] for k,v in self.artifacts.items() if k.startswith("input/") and v["path"].suffix==".lib"]
        key=(item["sha256"],tuple(digest(p) for p in libraries),top)
        if key not in self.design_cache:
            runner=YosysRunner([self.source,self.run_dir], timeout=max(0.001,self.deadline-time.monotonic()))
            result=load_design(runner,item["path"],libraries,top,self.design_cache)
            if result.status!="ok":
                # Keep detailed parser diagnostics locally, not arbitrary endpoint errors.
                (self.output_dir/("import_error_"+str(len(self.design_cache))+".json")).write_text(encoded(result.to_dict()))
                raise ValueError("Design import unavailable: "+",".join(e.get("code","parse_error") for e in result.errors))
            self.design_cache[key]=result.data
        db=self.design_cache[key]
        db.deadline=self.deadline
        return db

    def query_design(self, artifact, request):
        validate_conditions('query_design',dict(request=normalize(request)))
        q=request; db=self.design(artifact,q.get("top") or None)
        operation=q.get("operation","select"); rows=[]; values=[]; unknown=0
        if operation in {"select","count","group"}:
            known_types={r["type"] for r in db.instances.values()} | set(db.index.get("library",{}))
            unknown_types=sorted(set(q.get("types",[]))-known_types)
            if unknown_types:
                raise ToolArgumentError(f"$.request.types: unknown cell types {unknown_types}; use query_design_cells(operation='group', types=[], roles=[], scope='') with the same artifact/top to discover exact module types, or read the associated .lib. Library-defined types with no instances yield zero matches.")
            if set(q.get("roles",[])) - {"DFF","SFF","FF","ICG","latch","combinational"}:
                raise ValueError("Unknown cell role; supported roles: DFF, SFF, FF, ICG, latch, combinational")
            selected,unknown=self.op.select(db,q.get("scope",""),{k:q[k] for k in ("types","roles") if k in q})
            rows=[dict(path=p,type=v["type"],instance_name=p.rsplit("/",1)[-1],containing_module_type=db.instances[p]["parent_module"],definition_location=None) for p,v in selected.items()]; values=list(selected)
            observed={"selected":len(rows),"unknown":unknown}
            if operation=="group": observed["groups"]=dict(Counter(row["type"] for row in rows))
            if operation!="select":rows=[]
        elif operation=="ports":
            rows=[dict(port=k,direction=v["direction"],width=len(v["bits"])) for k,v in db.index["ports"].items()]; observed={"selected":len(rows)}
        elif operation=="hierarchy":
            rows=[]
            for entry in db.index["hierarchy"]:
                path='/'.join(entry['path'])
                rows.append(dict(entry,instance_name=path.rsplit('/',1)[-1] if path else None,containing_module_type=db.instances[path]['parent_module'] if path else None,definition_location=None))
            observed={"selected":len(rows)}
        elif operation=="pin_catalog":
            library=db.index.get("library",{})
            selected=q.get("types") or sorted({r["type"] for r in db.instances.values() if r["leaf"]})
            if set(selected)-set(library):raise ToolArgumentError(f"$.request.types: types not defined in associated library: {sorted(set(selected)-set(library))}; use query_design_cells(operation='group',types=[],roles=[],scope='') on the same artifact/top to discover instantiated module types, then query_pin_catalog")
            rows=[dict(type=k,**library[k]) for k in selected]; observed={"selected":len(rows)}
        elif operation=="port_mappings": rows=db.index["port_mappings"]; observed={"selected":len(rows)}
        elif operation=="connections":
            result=db.get_pin_connection(q["instance"],q["pin"])
            if result.status!="ok": raise ToolArgumentError("$.instance_path/$.pin: unknown instance or pin; discover instance_path with query_design_cells and actual pins with query_pin_catalog or query_connections_by_role")
            fact=result.to_dict()
            instance=db.instances[q['instance']]
            fact['data'].update(module_type=instance['type'],instance_name=q['instance'].rsplit('/',1)[-1],containing_module_type=instance['parent_module'],definition_location=None)
            rows=[fact]; observed={"selected":1}
        else: raise ValueError("Unsupported design operation")
        return dict(status="FACT",proof=False,complete=not unknown,artifact=artifact,operation=operation,parameters=q,
                    observed=observed,rows=rows,values=values,population_origin="original_design" if artifact.startswith("input/") else "candidate_design",_cursor=q.get("cursor",0))

    @staticmethod
    def _paths(db, patterns):
        paths=set(db.scopes)
        result=[]
        for pattern in patterns:
            found=[pattern] if pattern in paths else [p for p in paths if fnmatchcase(p,pattern)]
            if not found: raise ValueError("Unknown hierarchy scope: "+pattern)
            result.extend(p.split("/") for p in sorted(found))
        return result

    def compare_design(self, before, after, request):
        validate_conditions('compare_design',dict(request=normalize(request)))
        if before not in self.artifacts or not before.startswith(("input/", "run/")):
            raise ValueError("Structural baseline must be a registered input or current-run netlist")
        if after not in self.artifacts or not after.startswith("run/"):
            raise ValueError("Structural candidate must be a registered current-run netlist")
        if any(self.artifacts[key]["path"].suffix not in {".v", ".sv"} for key in (before, after)):
            raise ValueError("Structural comparison requires two Verilog netlists")
        if before == after:
            raise ValueError("Structural comparison requires two distinct artifacts")
        provenance=dict(baseline_origin="original_input" if before.startswith("input/") else "candidate_stage",
                        proof_scope="original_to_candidate" if before.startswith("input/") else "stage_to_stage")
        q=request; a=self.design(before,q.get("top") or None); b=self.design(after,q.get("top") or None)
        kind=q["operation"]
        if kind not in {"icg_integrity","icg_rewire","ff_total","ff_region_plain","ff_mapping","ff_changes","no_scan_structure","unchanged"}:
            raise ValueError("Unsupported structural comparison")
        scope={"include":self._paths(a,q.get("include",[])),"exclude":self._paths(a,q.get("exclude",[]))}
        if kind=="ff_changes":
            facts=collect_ff_changes(a,b,scope,self.deadline)
            facts.update(**provenance,operation=kind,parameters=q,artifacts=[before,after],population_origin="design_comparison")
            return facts
        if kind=="ff_mapping":
            # This copied checker proves forward replacement. A final export
            # may intentionally contain a later reverse-replacement stage;
            # treating those cells as forward failures would be an unsound
            # task verdict. No task-specific scope or filename is assumed.
            selected=scope_cells(a.index,scope)
            reverse=["/".join(path) for path,old in selected.items()
                     if a.index.get("library",{}).get(old["type"],{}).get("role")=="scan_ff"
                     and path in b.index["cells"]
                     and b.index.get("library",{}).get(b.index["cells"][path]["type"],{}).get("role")=="ordinary_ff"]
            if reverse:
                return dict(**provenance,status="UNKNOWN",proof=True,complete=False,operation=kind,parameters=q,
                            artifacts=[before,after],observed=dict(reverse_replacements=len(reverse)),
                            rows=[dict(instance=p) for p in reverse],
                            reason="Forward-only FF checker received a stage with SFF-to-DFF changes. Select the pre-reversal stage or correct scope; use ff_region_plain for reverse-region role/population. This is not a proven task violation.")
        if kind=="unchanged":
            result=self.op.check_unchanged(a,b,q.get("scope",""),properties=q.get("properties"))
        else:
            result=self.op.structural(kind,a,b,scope=scope,target_port=q.get("target_port",""),original_value=q.get("original_value","0"),mapping=q.get("mapping",[]),deadline=self.deadline)
        return dict(**provenance,status=result.status,proof=True,complete=result.status!="UNKNOWN",operation=kind,
                    parameters=q,artifacts=[before,after],observed=result.observed,violations=result.violations,reason=result.reason)

    @staticmethod
    def _filter(values, include, exclude):
        def selected(value, pattern):
            return value==pattern or value.startswith(pattern.rstrip("/")+"/") or fnmatchcase(value,pattern)
        return [v for v in values if (not include or any(selected(v,p) for p in include)) and not any(selected(v,p) for p in exclude)]

    def check_membership(self, request):
        q=request
        for field in ("expected_id", "actual_id"):
            if q[field] not in self.records:raise ValueError(f"$.request.{field}: unknown or stale evidence ID")
        left=self.records[q["expected_id"]]; right=self.records[q["actual_id"]]
        relation=q["relation"]
        if q["expected_id"]==q["actual_id"]:
            raise ValueError("Comparing a set with itself cannot establish task coverage; use independently queried expected and actual populations")
        if relation not in {"equal","subset","disjoint"}: raise ValueError("Unknown set relation")
        a=self._filter(left.get("values",[]),q.get("expected_include",[]),q.get("expected_exclude",[]))
        b=self._filter(right.get("values",[]),q.get("actual_include",[]),q.get("actual_exclude",[]))
        if "values" not in left or "values" not in right: raise ValueError("Queries do not expose complete member sets")
        if left.get("population_origin")!="original_design":
            # Comparing two exports is a consistency proof, not original-design coverage.
            level="export_consistency"
        else: level="original_population_coverage"
        if right.get("population_origin")=="log_event":level="reported_operation_membership"
        complete=bool(left.get("complete") and right.get("complete"))
        sa,sb=set(a),set(b)
        delta=sa^sb if relation=="equal" else sa-sb if relation=="subset" else sa&sb
        bad=[dict(instance=v,reason="set_mismatch") for v in sorted(delta)]
        if q.get("require_unique",True): bad.extend(dict(instance=v,reason="duplicate",count=n) for v,n in Counter(b).items() if n>1)
        # An empty actual set is legitimate for a disjointness assertion only if
        # the underlying complete report itself contained a nonempty population.
        applicable=bool(a and right.get("values"))
        status="UNKNOWN" if not complete or not applicable else "FAIL" if bad else "PASS"
        return dict(status=status,proof=True,complete=complete and applicable,operation="membership",parameters=q,
                    proof_level=level,observed=dict(expected=len(a),actual=len(b),violations=len(bad)),violations=bad,
                    source_evidence=[q["expected_id"],q["actual_id"]],reason="" if applicable else "Empty population does not establish applicability")

    def check_delivery(self, request):
        q=request;requested=q.get("files",[]);pending=q.get("runner_outputs",[])
        if not isinstance(requested,list) or not requested or set(pending)-{"decision_log.json"}:
            raise ValueError("Expected candidate file list and known runner outputs")
        rows=[];bad=[]
        names=[key[4:] for key in self.artifacts if key.startswith("run/")]
        for name in requested:
            if name=="final.dofile":
                matched=["candidate.dofile"]
            elif name=="*.log":
                matched=[p for p in names if p==self.run_dir.name+".log"]
            else:
                matched=[p for p in names if p==name or fnmatchcase(p,name)]
                if not matched and "/" not in name:
                    matched=[p for p in names if fnmatchcase(Path(p).name,name)]
            if not matched: bad.append(dict(file=name,reason="Missing required candidate artifact"))
            for path in matched:
                if path!="candidate.dofile" and self.artifacts["run/"+path]["path"].stat().st_size==0:
                    bad.append(dict(file=path,reason="Empty candidate artifact"))
                rows.append(dict(requested=name,artifact=path))
        return dict(status="FAIL" if bad else "PASS",proof=True,complete=True,operation="delivery",
                    proof_level="candidate_files_ready_for_runner_publication",parameters=q,
                    observed=dict(requested=len(requested),matched=len(rows)),violations=bad,rows=rows,
                    pending_runner_outputs=pending)
