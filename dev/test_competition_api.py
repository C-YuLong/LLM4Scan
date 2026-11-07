"""Offline protocol/shape check; not a claim of access to the official endpoint."""
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from model_contract import record, STRING, ModelResponseError
from scan_agent import generate
from dev import run_local


class LocalApiAuthorizationTests(unittest.TestCase):
    def test_official_deepseek_uses_only_its_own_key_over_stdin(self):
        with patch.object(run_local.os, 'environ', {'DEEPSEEK_API_KEY': 'fake-deepseek-test-key'}), \
                patch.object(sys, 'argv', ['run_local', '--provider', 'deepseek', '--output', 'unused-output']), \
                patch.object(Path, 'read_text') as read_config, \
                patch.object(Path, 'is_dir', return_value=True), \
                patch.object(Path, 'mkdir'), \
                patch.object(run_local.subprocess, 'run') as launch:
            launch.return_value.returncode = 0
            self.assertEqual(run_local.main(), 0)
        read_config.assert_not_called()
        environment = json.loads(launch.call_args.kwargs['input'])
        self.assertEqual(environment['LLM_BASE_URL'], 'https://api.deepseek.com')
        self.assertEqual(environment['LLM_MODEL'], 'deepseek-flash')
        self.assertEqual(environment['LLM_API_MODE'], 'responses')
        self.assertEqual(environment['LLM_REASONING_EFFORT'], 'none')
        self.assertEqual(environment['LLM_API_KEY'], 'fake-deepseek-test-key')
        self.assertNotIn('fake-deepseek-test-key', ' '.join(launch.call_args.args[0]))

    def test_ourmethod_uses_authorized_provider_and_forwards_verifier(self):
        provider = {'base_url': 'https://gproxy.lin.pub/codex/v1',
                    'env_key': 'CODEX_KEY', 'wire_api': 'responses'}
        with patch.object(run_local.os, 'environ', {
                'CODEX_HOME': 'D:/unused-config', 'CODEX_KEY': 'fake-gproxy-test-key'}), \
                patch.object(Path, 'home', return_value=Path('D:/unused-user')), \
                patch.object(sys, 'argv', ['run_local', '--output', 'unused-output',
                    '--implementation', 'ourmethod', '--verifier', 'guided_agent',
                    '--verification-mode', 'gate', '--runtime-image', 'local-test-runtime']), \
                patch.object(Path, 'read_text', return_value=''), \
                patch.object(run_local.tomllib, 'loads', return_value={
                    'model_providers': {'gproxy': provider}}), \
                patch.object(Path, 'is_dir', return_value=True), \
                patch.object(Path, 'mkdir'), \
                patch.object(run_local.subprocess, 'run') as launch:
            launch.return_value.returncode = 0
            self.assertEqual(run_local.main(), 0)
        command = launch.call_args.args[0]
        environment = json.loads(launch.call_args.kwargs['input'])
        self.assertTrue(any(value.endswith('/Ourmethod:/submission:ro') for value in command))
        self.assertEqual(command[command.index('--verifier') + 1], 'guided_agent')
        self.assertEqual(command[command.index('--verification-mode') + 1], 'gate')
        self.assertIn('local-test-runtime', command)
        self.assertEqual(environment['LLM_BASE_URL'], provider['base_url'])
        self.assertEqual(environment['LLM_MODEL'], 'gpt-5.6-luna')
        self.assertNotIn('fake-gproxy-test-key', ' '.join(command))

    def test_rejects_other_credentials_before_read_or_launch(self):
        provider = {'base_url': 'https://gproxy.lin.pub/codex/v1',
                    'env_key': 'CODEX_KEY', 'wire_api': 'responses'}
        for change, flags in [({'base_url': 'https://invalid.example/v1'}, []),
                              ({'env_key': 'OTHER_KEY'}, []),
                              ({'wire_api': 'chat_completions'}, [])]:
            with self.subTest(change=change, flags=flags), \
                    patch.object(run_local.os, 'environ', {'CODEX_HOME': 'D:/unused-config'}), \
                    patch.object(Path, 'home', return_value=Path('D:/unused-user')), \
                    patch.object(sys, 'argv', ['run_local', '--output', 'unused-output'] + flags), \
                    patch.object(Path, 'read_text', return_value='') as read_config, \
                    patch.object(run_local.tomllib, 'loads', return_value={
                        'model_providers': {'gproxy': {**provider, **change}}}), \
                    patch.object(Path, 'mkdir') as mkdir, \
                    patch.object(run_local.subprocess, 'run') as launch, \
                    patch.object(sys, 'stderr', io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    run_local.main()
                self.assertEqual(caught.exception.code, 2)
                launch.assert_not_called()
                mkdir.assert_not_called()
                if flags:
                    read_config.assert_not_called()

    def test_explicit_gproxy_selection_and_stdin_only_key(self):
        provider = {'base_url': 'https://gproxy.lin.pub/codex/v1',
                    'env_key': 'CODEX_KEY', 'wire_api': 'responses'}
        with patch.object(run_local.os, 'environ', {
                'CODEX_HOME': 'D:/unused-config', 'CODEX_KEY': 'fake-gproxy-test-key'}), \
                patch.object(Path, 'home', return_value=Path('D:/unused-user')), \
                patch.object(sys, 'argv', ['run_local', '--output', 'unused-output']), \
                patch.object(Path, 'read_text', return_value=''), \
                patch.object(run_local.tomllib, 'loads', return_value={
                    'model_provider': 'not-authorized', 'model_providers': {'gproxy': provider}}), \
                patch.object(Path, 'is_dir', return_value=True), \
                patch.object(Path, 'mkdir'), \
                patch.object(run_local.subprocess, 'run') as launch:
            launch.return_value.returncode = 0
            self.assertEqual(run_local.main(), 0)
        environment = json.loads(launch.call_args.kwargs['input'])
        self.assertEqual(environment['LLM_BASE_URL'], provider['base_url'])
        self.assertEqual(environment['LLM_API_KEY'], 'fake-gproxy-test-key')
        self.assertEqual(environment['LLM_MODEL'], 'gpt-5.6-luna')
        self.assertEqual(environment['LLM_API_MODE'], 'responses')
        self.assertNotIn('fake-gproxy-test-key', ' '.join(launch.call_args.args[0]))

    def test_competition_api_reads_project_env_and_stdin_only_key(self):
        dotenv = '# comment\nLLM_API_KEY=fake-dashscope-key\n'
        with patch.object(sys, 'argv', ['run_local', '--output', 'unused-output', '--competition-api']), \
                patch.object(Path, 'read_text', return_value=dotenv), \
                patch.object(run_local.tomllib, 'loads') as loads, \
                patch.object(Path, 'is_dir', return_value=True), \
                patch.object(Path, 'mkdir'), \
                patch.object(run_local.subprocess, 'run') as launch:
            launch.return_value.returncode = 0
            self.assertEqual(run_local.main(), 0)
        loads.assert_not_called()
        environment = json.loads(launch.call_args.kwargs['input'])
        self.assertEqual(environment['LLM_BASE_URL'], 'https://dashscope.aliyuncs.com/compatible-mode/v1')
        self.assertEqual(environment['LLM_API_KEY'], 'fake-dashscope-key')
        self.assertEqual(environment['LLM_MODEL'], 'deepseek-v4-pro')
        self.assertEqual(environment['LLM_API_MODE'], 'chat_completions')
        self.assertNotIn('fake-dashscope-key', ' '.join(launch.call_args.args[0]))


class CompetitionApiTests(unittest.TestCase):
    def test_responses_rejects_missing_required_fields(self):
        event = {'type': 'response.completed', 'response': {
            'output': [{'content': [{'type': 'output_text', 'text': '{}'}]}]}}
        with patch.dict(os.environ, {'LLM_BASE_URL': 'https://invalid.example/v1',
                                     'LLM_MODEL': 'offline-model', 'LLM_API_KEY': 'not-a-real-key',
                                     'LLM_API_MODE': 'responses'}), \
                patch('urllib.request.urlopen', return_value=io.BytesIO(
                    ('data: ' + json.dumps(event) + '\n\n').encode())):
            with self.assertRaises(ModelResponseError) as caught:
                generate('test question', 140, 'Return JSON', record({'answer': STRING}))
        self.assertEqual(caught.exception.plan, {})
        self.assertIn('answer', str(caught.exception))

    def test_responses_honors_explicit_non_thinking_mode(self):
        event = {'type': 'response.completed', 'response': {
            'output': [{'content': [{'type': 'output_text', 'text': '{"answer":"ok"}'}]}]}}
        def serve(request, **kwargs):
            self.assertEqual(request.full_url, 'https://api.deepseek.com/responses')
            self.assertEqual(json.loads(request.data)['reasoning'], {'effort': 'none'})
            return io.BytesIO(('data: '+json.dumps(event)+'\n\n').encode())
        with patch.dict(os.environ, {'LLM_BASE_URL': 'https://api.deepseek.com',
                                     'LLM_MODEL': 'deepseek-flash', 'LLM_API_KEY': 'not-a-real-key',
                                     'LLM_API_MODE': 'responses', 'LLM_REASONING_EFFORT': 'none'}), \
                patch('urllib.request.urlopen', side_effect=serve):
            plan, _ = generate('test question', 140, 'Return JSON', record({'answer': STRING}))
        self.assertEqual(plan, {'answer': 'ok'})

    def test_shared_guide_precedes_phase_and_schema_in_both_protocols(self):
        guide = 'COMMON-SCAN-REFERENCE\n' * 100
        for protocol in ('chat_completions', 'responses'):
            with self.subTest(protocol=protocol):
                payloads = []
                def serve(request, **kwargs):
                    payloads.append(json.loads(request.data))
                    reply = '{"answer":"ok"}'
                    if protocol == 'chat_completions':
                        result = {'choices': [{'finish_reason': 'stop', 'message': {'content': reply}}]}
                        return io.BytesIO(json.dumps(result).encode())
                    result = {'type': 'response.completed', 'response': {
                        'output': [{'content': [{'type': 'output_text', 'text': reply}]}]}}
                    return io.BytesIO(('data: ' + json.dumps(result) + '\n\n').encode())
                with patch.dict(os.environ, {'LLM_BASE_URL': 'https://invalid.example/v1', 'LLM_MODEL': 'offline-model',
                                             'LLM_API_KEY': 'not-a-real-key', 'LLM_API_MODE': protocol}), \
                        patch('urllib.request.urlopen', side_effect=serve):
                    for phase, field in [('GENERATE', STRING), ('VERIFY', {'type': 'string', 'enum': ['ok']})]:
                        plan, _ = generate('case-specific facts', 120, phase, record({'answer': field}), tool_guide=guide)
                        self.assertEqual(plan, {'answer': 'ok'})
                instructions = [p['messages'][0]['content'] if protocol == 'chat_completions' else p['instructions']
                                for p in payloads]
                for text, phase, payload in zip(instructions, ('GENERATE', 'VERIFY'), payloads):
                    self.assertEqual(text.count(guide), 1)
                    self.assertLess(text.index(guide), text.index(phase))
                    if protocol == 'chat_completions':
                        self.assertNotIn('prompt_cache_key', payload)
                        self.assertLess(text.index(guide), text.index('conforming to this schema'))
                        self.assertEqual(payload['messages'][1]['content'], 'case-specific facts')
                    else:
                        self.assertEqual(payload['prompt_cache_key'], 'scanscan-scan-agent-v1')
                        self.assertEqual(payload['input'], 'case-specific facts')
                        self.assertIn('No Markdown fences', text)
                        expected = {'type': 'string', 'enum': ['ok']} if phase == 'VERIFY' else STRING
                        self.assertIn(json.dumps(record({'answer': expected}), separators=(',', ':')), text)
                    self.assertEqual(payload['model'], 'offline-model')
                self.assertGreaterEqual(len(os.path.commonprefix(instructions)), len(guide))

    def test_short_official_calls_keep_model_and_disable_long_thinking(self):
        response = {'model': 'deepseek-v4-pro', 'choices': [
            {'finish_reason': 'stop', 'message': {'content': '{"answer":"ok"}'}}],
            'usage': {'prompt_tokens': 2, 'completion_tokens': 3}}
        def serve(request, **kwargs):
            body = json.loads(request.data)
            self.assertEqual(body['model'], 'deepseek-v4-pro')
            self.assertIs(body['enable_thinking'], False)
            self.assertEqual(body['response_format'], {'type': 'json_object'})
            return io.BytesIO(json.dumps(response).encode())
        with patch.dict(os.environ, {'LLM_BASE_URL': 'https://invalid.example/v1', 'LLM_MODEL': 'deepseek-v4-pro',
                                     'LLM_API_KEY': 'not-a-real-key', 'LLM_API_MODE': 'chat_completions'}), \
                patch('urllib.request.urlopen', side_effect=serve):
            _, usage = generate('test question', 140, 'Return JSON', record({'answer': STRING}))
            self.assertIs(usage['enable_thinking'], False)
            self.assertEqual(usage['model'], 'deepseek-v4-pro')

    def test_invalid_json_preserves_response_without_executing_it(self):
        content = '```json\n{"answer":"ok"}\n```'
        for protocol in ('chat_completions', 'responses'):
            if protocol == 'chat_completions':
                response = {'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]}
                wire = json.dumps(response).encode()
            else:
                response = {'type': 'response.completed', 'response': {
                    'output': [{'content': [{'type': 'output_text', 'text': content}]}],
                    'usage': {'input_tokens': 12, 'output_tokens': 3, 'total_tokens': 15}}}
                wire = ('data: ' + json.dumps(response) + '\n\n').encode()
            with self.subTest(protocol=protocol), \
                    patch.dict(os.environ, {'LLM_BASE_URL': 'https://invalid.example/v1', 'LLM_MODEL': 'offline-model',
                                             'LLM_API_KEY': 'not-a-real-key', 'LLM_API_MODE': protocol}), \
                    patch('urllib.request.urlopen', return_value=io.BytesIO(wire)):
                with self.assertRaises(ModelResponseError) as caught:
                    generate('test question', 140, 'Return JSON', record({'answer': STRING}))
                self.assertEqual(caught.exception.plan, content)
                self.assertIn('Invalid JSON', str(caught.exception))
                if protocol == 'responses':
                    self.assertEqual(caught.exception.usage['total_tokens'], 15)

    def test_default_chat_protocol_and_reject_missing_field(self):
        schema = record({'answer': STRING})
        for content, valid in [({'answer': 'ok'}, True), ({}, False), ({'answer': None}, False)]:
            response = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(content)}}],
                        'usage': {'prompt_tokens': 12, 'completion_tokens': 3, 'total_tokens': 15}}
            def serve(request, **kwargs):
                self.assertTrue(request.full_url.endswith('/chat/completions'))
                body = json.loads(request.data)
                self.assertEqual(body['model'], 'competition-test-model')
                self.assertEqual(body['messages'][1]['content'], 'test question')
                self.assertNotIn('reasoning', body)
                return io.BytesIO(json.dumps(response).encode())
            with patch.dict(os.environ, {'LLM_BASE_URL': 'https://invalid.example/v1', 'LLM_MODEL': 'competition-test-model',
                                         'LLM_API_KEY': 'not-a-real-key', 'LLM_API_MODE': 'chat_completions'}), \
                    patch('urllib.request.urlopen', side_effect=serve):
                if valid:
                    plan, usage = generate('test question', 10, 'Return JSON', schema)
                    self.assertEqual(plan, content)
                    self.assertEqual(usage['input_tokens'], 12)
                else:
                    with self.assertRaises(ModelResponseError) as caught:
                        generate('test question', 10, 'Return JSON', schema)
                    self.assertEqual(caught.exception.plan, content)
                    self.assertIn('$', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
