"""Small progress notes, never an acceptance contract or proof."""
from copy import deepcopy
import json

MAX_ITEMS = 16


class Checklist:
    def __init__(self, tools):
        self.tools = tools
        self.entries = {}

    def update(self, items):
        proposed = deepcopy(self.entries)
        for item in items:
            key = item['id']
            if key not in proposed:
                if item.get('requirement') is None or item.get('task_ref') is None:
                    raise ValueError('New checklist items require requirement and task_ref')
                proposed[key] = {}
            entry = proposed[key]
            for field in ('requirement', 'task_ref', 'gap'):
                if item.get(field) is not None:
                    entry[field] = item[field]
            ref = entry['task_ref']
            source = ref['source']
            if not source.startswith('input/') or not source.endswith('.md'):
                raise ValueError('Checklist references must point to input Markdown sources')
            lines = self.tools.read(source).splitlines()
            if not 1 <= ref['line_start'] <= ref['line_end'] <= len(lines):
                raise ValueError('Checklist task reference is outside the source')
            if any(e not in self.tools.records for e in item['evidence_ids']):
                raise ValueError('Checklist evidence IDs must belong to this candidate')
            entry.update(state=item['state'], evidence_ids=item['evidence_ids'])
            if entry['state'] == 'evidenced' and not entry['evidence_ids']:
                raise ValueError('evidenced needs evidence IDs; it is not an acceptance verdict')
            entry.setdefault('gap', '')
        if len(proposed) > MAX_ITEMS:
            raise ValueError(f'Checklist is limited to {MAX_ITEMS} short requirement groups')
        self.entries = proposed
        self.save()
        return dict(status='FACT', proof=False, complete=True, updated=[i['id'] for i in items],
                    message='Progress notes only; evidenced does not mean PASS. Latest checklist is supplied in instructions.')

    def snapshot(self):
        return [dict(id=k, **v) for k, v in self.entries.items()]

    def prompt(self):
        if not self.entries:
            return '\nVerification checklist: empty. Start with update_checklist: short requirement groups and original source references; no design inventories.\n'
        # One latest snapshot in instructions, never append full snapshots to history.
        rows = []
        for key, entry in self.entries.items():
            ref = entry['task_ref']
            rows.append([key, entry['requirement'], f"{ref['source']}:{ref['line_start']}-{ref['line_end']}",
                         entry['state'], entry['evidence_ids'], entry['gap']])
        return '\nLatest verification checklist [id, requirement, source lines, state, evidence IDs, gap]:\n' + json.dumps(rows, ensure_ascii=False, separators=(',', ':')) + '\nUse it to choose the next unfinished requirement. evidenced is a model progress note, not validated PASS.\n'

    def save(self):
        (self.tools.output_dir.parent / 'checklist.json').write_text(
            json.dumps(self.snapshot(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
