"""Deterministic, recoverable context projection; audit history stays untouched."""
from copy import deepcopy
from .result_view import grouped_data
import json

NAVIGATION = {'update_checklist', 'list_artifacts', 'list_evidence'}
RECENT_TURNS = 3
TABLE_LIMIT = 32


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def byte_size(value):
    return len(packed(value).encode('utf-8'))


def signature(tool, arguments):
    return packed([tool, arguments])


def evidence_row(tools, evidence_id, repeats=1):
    record = tools.records[evidence_id]
    query = tools.record_queries.get(evidence_id, {})
    args = query.get('arguments', record.get('parameters', {}))
    row = dict(id=evidence_id, tool=query.get('tool', record.get('operation', 'unknown')),
               status=record.get('status'), proof=bool(record.get('proof')),
               complete=bool(record.get('complete')), repeats=repeats)
    for key in ('artifact', 'artifacts', 'operation', 'table', 'proof_scope', 'baseline_origin', 'gap_category', 'next_step', 'limitations', 'next_read', 'partial', 'omitted', 'resolution_status', 'unresolved_reason'):
        if key in record: row[key] = record[key]
    if evidence_id in getattr(tools, 'text_continuations', {}): row['next_read'] = tools.text_continuations[evidence_id]
    row['query'] = args if len(packed(args)) <= 320 else dict(omitted=True, recover='inspect_check')
    observed = record.get('observed', {})
    stats = {k:v for k,v in observed.items() if isinstance(v, (int, float, bool)) or v is None}
    # Small categorical aggregates are useful, but never copy arbitrary long payloads.
    for key in ('actual', 'groups', 'changes', 'normal_connections'):
        if key in observed and len(packed(observed[key])) <= 120: stats[key] = observed[key]
    if stats: row['observed'] = stats
    if set(observed) - set(stats): row['observed_omitted'] = True
    if record.get('gaps'): row['gaps'] = record['gaps']
    if record.get('reason'):
        reason = record['reason']
        row['reason'] = reason[:160]
        if len(reason) > 160: row['reason_omitted'] = True
    if query.get('executor',query.get('tool')) == 'compare_design':
        examples = record.get('violations', [])
        if examples:
            row['counterexample'] = examples[0] if len(packed(examples[0])) <= 512 else dict(omitted=True, recover='inspect_check')
    sections=grouped_data(record)
    if sections is not None:
        row['sections']={name:len(items) for name,items in sections.items()}
        row['recover']=dict(evidence_id=evidence_id,cursor=0)
        row['recover_hint']='inspect_check: set section to a listed name; previews are not proofs'
    guided=getattr(tools,'guided_state',None)
    if guided is not None:
        row['applicability']=guided.disposition(evidence_id)
    api=getattr(tools,'model_api',None)
    if api is not None:
        row=api.view(row,evidence_id)
        if sections is not None:
            row['section_reads']={name:api.recover(evidence_id,name) for name in sections}
        if api.dynamic and evidence_id not in api.reader_keys():
            for field in ('recover','section_reads','next_read'):row.pop(field,None)
            row['recovery_requires_question_scope']=sorted(api.paths('read_evidence',dict(evidence_id=evidence_id)))
    return row


def evidence_ids(messages):
    ids = set()
    for message in messages:
        if message.get('type') == 'function_call_output':
            try: result = json.loads(message['output'])
            except (ValueError, TypeError): continue
            if result.get('evidence_id'): ids.add(result['evidence_id'])
    return ids


def evidence_table(tools, recent):
    recent_ids = evidence_ids(recent)
    pinned = {key for item in tools.checklist.snapshot() for key in item['evidence_ids']}
    groups = {}
    for key, query in tools.record_queries.items():
        if query['tool'] in NAVIGATION or key not in tools.records: continue
        groups.setdefault(signature(query['tool'], query['arguments']), []).append(key)
    candidates = []
    order = {key:i for i,key in enumerate(tools.records)}
    for keys in groups.values():
        # If a duplicate is in the recent raw window, its summary is unnecessary;
        # explicitly pinned older IDs still remain addressable in the table.
        selected = set(keys) & pinned
        if not set(keys) & recent_ids: selected.add(keys[-1])
        for key in selected - recent_ids:
            row = evidence_row(tools, key, len(keys))
            dispositions=row.get('applicability',[])
            inactive=bool(dispositions) and all(x['use'] in {'inapplicable','superseded','context_only'} for x in dispositions)
            rank = (key in pinned and not inactive, row['status'] == 'FAIL' and row['proof'] and not inactive, row['status'] == 'UNKNOWN' and not inactive, order[key])
            candidates.append((rank, row))
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    rows = [row for _,row in candidates[:TABLE_LIMIT]]
    return dict(rows=rows, omitted=max(0,len(candidates)-len(rows)),
                recover=(('list_evidence(cursor=""), then read_evidence or the offered section_reads tools' if tools.model_api.dynamic else 'list_evidence(cursor=""), then read_evidence/read_evidence_section') if getattr(tools,'model_api',None) else 'list_evidence(cursor), then inspect_check(evidence_id, cursor, section)'),
                note='Exact tool observations, not new verdicts; omitted conditions must be recovered before interpreting scope.')


class ContextProjection:
    def __init__(self, initial, mode):
        self.initial = deepcopy(initial)
        self.mode = mode
        self.turn_starts = []
        self.seen_outputs = set()

    def start_turn(self, messages):
        self.turn_starts.append(len(messages))

    def build(self, messages, tools):
        if self.mode == 'legacy':
            # Old behavior for controlled comparisons, without mutating audit history.
            projected = deepcopy(messages)
            for message in projected:
                if message.get('type') != 'function_call_output': continue
                if message['call_id'] not in self.seen_outputs or len(message['output']) <= 1200: continue
                data = json.loads(message['output'])
                summary = {k:data[k] for k in ('evidence_id','run_id','status','complete','proof','operation','artifact','table','proof_scope','baseline_origin','observed','message','next_read','partial','omitted') if k in data}
                if data.get('evidence_id') in tools.records:
                    indexed=evidence_row(tools,data['evidence_id'])
                    for field in ('sections','recover','recover_hint','section_reads'):
                        if field in indexed:summary[field]=indexed[field]
                if data.get('evidence_id'):
                    summary.update(omitted=True,read_more='read_evidence' if getattr(tools,'model_api',None) else 'inspect_check',total_rows=data.get('total_rows'))
                    if getattr(tools,'model_api',None):summary['recover']=tools.model_api.recover(data['evidence_id'])
                message['output'] = packed(summary)
            return projected, '', dict(recent_history=byte_size(projected[1:]), evidence_table=0, unresolved_errors=0)
        start = self.turn_starts[-RECENT_TURNS] if len(self.turn_starts) >= RECENT_TURNS else 1
        recent = deepcopy(messages[start:])
        # Retain every incomplete call even if a caller invokes build mid-turn.
        calls = {m['call_id'] for m in messages[1:] if m.get('type') == 'function_call'}
        answered = {m['call_id'] for m in messages[1:] if m.get('type') == 'function_call_output'}
        incomplete = calls - answered
        if incomplete:
            for offset in self.turn_starts:
                if any(m.get('call_id') in incomplete for m in messages[offset:start]):
                    start = min(start, offset)
                    recent = deepcopy(messages[start:])
                    break
        table = evidence_table(tools, recent)
        # Latest unresolved error per tool, cleared only by a successful call of
        # that same tool. Errors still visible in the raw window are not duplicated.
        tool_names = {m['call_id']:m['name'] for m in messages if m.get('type') == 'function_call'}
        unresolved = {}
        for m in messages:
            if m.get('type') != 'function_call_output': continue
            data = json.loads(m['output']); name = tool_names.get(m['call_id'], 'unknown')
            if data.get('error_type') or ('message' in data and data.get('retryable') is not None):
                unresolved[name] = (m['call_id'], data)
            else: unresolved.pop(name, None)
        recent_calls = {m.get('call_id') for m in recent}
        errors = {name:data for name,(call_id,data) in unresolved.items() if call_id not in recent_calls}
        dynamic = '\nArchived evidence table:\n' + packed(table)
        if errors: dynamic += '\nUnresolved tool errors:\n' + packed(errors)
        return [deepcopy(self.initial)] + recent, dynamic, dict(recent_history=byte_size(recent), evidence_table=byte_size(table), unresolved_errors=byte_size(errors))

    def mark_seen(self, projected):
        self.seen_outputs.update(m['call_id'] for m in projected if m.get('type') == 'function_call_output')
