"""Single-writer, crash-readable diagnostics for isolated verification sessions."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import time


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class SessionDiagnostics:
    """Only this session writes its files; updates are copied into the controller queue."""
    def __init__(self, directory, worker_id, requirement=None, notify=None, queued_at=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.notify = notify
        self.created = time.monotonic() if queued_at is None else queued_at
        self.started = None
        self.sequence = 0
        self.status = dict(worker_id=worker_id, requirement_id=requirement and requirement['id'],
                           requirement=requirement, runtime_status='queued', verdict='UNKNOWN',
                           phase='queued', queued_at=utc_now(), started_at=None, ended_at=None,
                           queue_seconds=0, elapsed_seconds=0, termination=None, failure_type=None,
                           metrics={}, path=('planner/status.json' if worker_id=='planner' else f'workers/{worker_id}/status.json'))
        self.publish()

    def publish(self):
        atomic_json(self.directory/'status.json', self.status)
        if self.notify:
            self.notify(deepcopy(self.status))

    def event(self, kind, **details):
        self.sequence += 1
        phase = details.get('phase')
        if phase:
            self.status['phase'] = phase
            if phase!='done':self.status['last_active_phase']=phase
        if 'question' in details:self.status['question']=deepcopy(details['question'])
        if self.started is not None:
            self.status['elapsed_seconds'] = round(time.monotonic()-self.started, 3)
        event = dict(sequence=self.sequence, event=kind, timestamp=utc_now(),
                     worker_id=self.status['worker_id'], requirement_id=self.status['requirement_id'],
                     elapsed_seconds=self.status['elapsed_seconds'], **details)
        with (self.directory/'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(event, ensure_ascii=False)+'\n')
            stream.flush()
        self.publish()

    def start(self, phase):
        self.started = time.monotonic()
        self.status.update(runtime_status='running', started_at=utc_now(),
                           queue_seconds=round(self.started-self.created, 3))
        self.event('started', phase=phase)

    def finish(self, result, runtime_status=None):
        reason = result.get('termination')
        if runtime_status is None:
            runtime_status = ('timed_out' if reason in {'time_budget', 'closing_time_budget'} else
                              'error' if reason in {'request_error', 'tool_error', 'initialization_error', 'invalid_plan'} else 'completed')
        self.status['last_phase']=self.status.get('last_active_phase',self.status['phase'])
        self.status.update(runtime_status=runtime_status, verdict=result['status'], termination=reason,
                           ended_at=utc_now(), metrics=deepcopy(result['metrics']),
                           failure_type=result.get('failure_type'))
        self.event('finished', phase='done', verdict=result['status'], termination=reason,
                   failure_type=result.get('failure_type'))


def evidence_path(verification_dir, evidence_id):
    """Read old flat layouts and new worker layouts without exposing arbitrary paths."""
    root = Path(verification_dir).resolve()
    index = root/'evidence_index.json'
    if index.exists():
        relative = json.loads(index.read_text(encoding='utf-8')).get(evidence_id)
        if relative is not None:
            path = (root/relative).resolve()
            if not path.is_relative_to(root):
                raise ValueError('Evidence index escapes verification directory')
            return path
    if Path(evidence_id).name != evidence_id:
        raise ValueError('Invalid evidence ID')
    return root/'evidence'/(evidence_id+'.json')
