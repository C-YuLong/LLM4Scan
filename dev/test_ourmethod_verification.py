"""Offline regressions for final citations and candidate publication checks."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Ourmethod'))
from configuration import DEFAULTS
from verifier.direct_agent import verify
from verifier.guided_agent import _verify_session
from verifier.diagnostics import SessionDiagnostics


def response(name, arguments, number):
    return dict(output=[dict(type='function_call', name=name, call_id=f'call{number}',
                             arguments=json.dumps(arguments))],
                usage=dict(input_tokens=1, output_tokens=1, total_tokens=2))


class VerificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'input'
        self.source.mkdir()
        (self.source / 'task_spec.md').write_text('Deliver a script.\nKeep inputs unchanged.\nWrite a decision.\n')
        self.run = self.root / 'runs/R1'
        (self.run / 'deliverables').mkdir(parents=True)
        self.script = 'puts ready\n'
        (self.run / 'deliverables/R1.dofile').write_text(self.script)
        self.execution = dict(exit_status='completed')

    @staticmethod
    def final(evidence_id):
        ref = dict(source='input/task_spec.md', line_start=1, line_end=3)
        return dict(status='PASS', summary='Candidate script ready; decision is pending publication.',
                    reviewed_task=True, unverified=[], context=[dict(task_ref=ref, reason='Delivery requirements')],
                    items=[dict(requirement='Candidate script ready', task_ref=ref, status='PASS', kind='delivery',
                                evidence_ids=[evidence_id], message='Complete candidate delivery check passed.',
                                judgment_basis='program', inapplicable_checks=None, counterexamples=None)])

    def test_bad_final_citations_are_corrected_within_existing_call_budget(self):
        for corrected in (True, False):
            with self.subTest(corrected=corrected):
                requests = []
                evidence_id = None
                def respond(messages, definitions, instructions, timeout, max_output, schema, choice):
                    nonlocal evidence_id
                    requests.append(messages)
                    number = len(requests)
                    if number == 1:
                        return response('check_delivery', dict(files=['final.dofile'],
                                                               runner_outputs=['decision_log.json']), number)
                    self.assertEqual(choice, dict(type='function', name='finish_verification'))
                    if number == 2:
                        evidence = json.loads(next(m['output'] for m in messages
                                                   if m.get('type') == 'function_call_output'))
                        self.assertEqual(evidence['pending_runner_outputs'], ['decision_log.json'])
                        evidence_id = evidence['evidence_id']
                    else:
                        correction = json.loads(next(m['output'] for m in reversed(messages)
                                                     if m.get('type') == 'function_call_output'))
                        self.assertIn('$.items[0].task_ref', correction['message'])
                        self.assertIn('$.context[0].task_ref', correction['message'])
                    value = self.final(evidence_id)
                    if number == 2 or not corrected:
                        value['items'][0]['task_ref'] = dict(source='input/task_spec.md', line_start=2, line_end=1)
                        value['context'][0]['task_ref'] = dict(source='input/task_spec.md', line_start=3, line_end=2)
                    return response('finish_verification', value, number)
                settings = dict(deepcopy(DEFAULTS['verifiers']['direct_agent']), max_model_turns=3)
                result = verify(self.source, self.run, self.script, self.execution,
                                self.root / str(corrected), settings, Mock(respond=respond))
                self.assertEqual(result['status'], 'PASS' if corrected else 'UNKNOWN')
                self.assertEqual(result['metrics']['model_calls'], 3)
                self.assertEqual(result['metrics']['argument_retry_rounds'], 1 if corrected else 2)
                self.assertFalse((self.root / 'final_results').exists())

    def test_bad_evidence_arguments_can_be_corrected_before_final_submission(self):
        calls = 0
        def respond(messages, definitions, instructions, timeout, max_output, schema, choice):
            nonlocal calls
            calls += 1
            if calls <= 2:
                self.assertEqual(choice, 'auto')
                return response('check_delivery', dict(files=['final.dofile'],
                    runner_outputs='invalid' if calls == 1 else ['decision_log.json']), calls)
            evidence = json.loads(next(m['output'] for m in reversed(messages)
                                       if m.get('type') == 'function_call_output'))
            return response('finish_verification', self.final(evidence['evidence_id']), calls)
        result = verify(self.source, self.run, self.script, self.execution, self.root / 'arguments',
                        deepcopy(DEFAULTS['verifiers']['direct_agent']), Mock(respond=respond))
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(calls, 3)
        self.assertEqual(result['metrics']['argument_retry_rounds'], 1)

    def test_failed_predicate_does_not_force_finish_before_applicability_review(self):
        calls = 0
        failed_id = None
        def respond(messages, definitions, instructions, timeout, max_output, schema, choice):
            nonlocal calls, failed_id
            calls += 1
            if calls == 1:
                return response('check_delivery', dict(files=['unrequired.txt'], runner_outputs=[]), calls)
            evidence = json.loads(next(m['output'] for m in reversed(messages)
                                       if m.get('type') == 'function_call_output'))
            if calls == 2:
                self.assertEqual(evidence['status'], 'FAIL')
                self.assertEqual(choice, 'auto')
                failed_id = evidence['evidence_id']
                return response('check_delivery', dict(files=['final.dofile'],
                                                       runner_outputs=['decision_log.json']), calls)
            value = self.final(evidence['evidence_id'])
            item = value['items'][0]
            item['judgment_basis'] = 'model'
            item['evidence_ids'].append(failed_id)
            item['inapplicable_checks'] = [dict(evidence_id=failed_id,
                reason='The task requires a script and decision, not unrequired.txt.')]
            return response('finish_verification', value, calls)
        result = verify(self.source, self.run, self.script, self.execution, self.root / 'failed_check',
                        deepcopy(DEFAULTS['verifiers']['direct_agent']), Mock(respond=respond))
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(calls, 3)
        self.assertEqual(result['unresolved_program_failures'], [])
        self.assertEqual(result['interpreted_program_failures'][0]['evidence_id'], failed_id)

    def test_changed_input_aborts_instead_of_requesting_citation_correction(self):
        calls = 0
        def respond(messages, *args):
            nonlocal calls
            calls += 1
            (self.source / 'task_spec.md').write_text('Changed while verifying.\n')
            return response('finish_verification', self.final('missing'), calls)
        result = verify(self.source, self.run, self.script, self.execution, self.root / 'changed',
                        deepcopy(DEFAULTS['verifiers']['direct_agent']), Mock(respond=respond))
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(calls, 1)
        self.assertEqual(result['metrics']['argument_retry_rounds'], 0)

    def test_guided_worker_queries_delivery_without_repeating_its_question(self):
        calls = 0
        def respond(messages, definitions, *args):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.assertIn('check_delivery', {d['name'] for d in definitions})
                return response('check_delivery', dict(files=['final.dofile'],
                                                       runner_outputs=['decision_log.json']), calls)
            evidence = json.loads(next(m['output'] for m in reversed(messages)
                                       if m.get('type') == 'function_call_output'))
            return response('assess_requirement', dict(status='PASS', judgment_basis='program',
                message='Complete candidate delivery check passed; runner decision remains pending.',
                conditions_match=True, next_question=None,
                evidence=[dict(evidence_id=evidence['evidence_id'], use='applicable',
                               reason='Covers the candidate script.', replaced_by=None)]), calls)
        requirement = dict(id='R1', requirement='Deliver the candidate script and runner decision.')
        output = self.root / 'guided'
        result = _verify_session(self.source, self.run, self.script, self.execution, output,
            deepcopy(DEFAULTS['verifiers']['guided_agent']), Mock(respond=respond),
            deadline=time.monotonic()+60, diagnostics=SessionDiagnostics(output, '001', requirement),
            requirement=requirement)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['metrics']['model_calls'], 2)
        self.assertFalse((self.root / 'decision_log.json').exists())


if __name__ == '__main__':
    unittest.main()
