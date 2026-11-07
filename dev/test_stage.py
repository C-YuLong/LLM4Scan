"""Focused checks for stage bookkeeping; no live runner or model calls."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import run_stage


class StageBookkeepingTests(unittest.TestCase):
    def test_cli_help_smoke(self):
        completed = subprocess.run([sys.executable, str(Path(__file__).parent / "run_stage.py"), "--help"],
                                   capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0)
        self.assertIn("task_2/case2", completed.stdout)

    def test_token_alias_is_counted_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            usage = {"usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}}
            for name in ("verification.json", "verification_R1.json", "model_plan.json"):
                (root / name).write_text(json.dumps(usage), encoding="utf-8")
            self.assertEqual(run_stage._tokens(root)["totals"], {"input_tokens": 4, "output_tokens": 6, "total_tokens": 10})

    def test_duplicate_or_existing_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "stage_case").mkdir()
            with self.assertRaises(ValueError):
                run_stage._validate_unique_outputs(root, "stage", ["stage_case"])
            with self.assertRaises(ValueError):
                run_stage._validate_unique_outputs(root, "fresh", ["a", "a"])

    def test_each_correction_is_counted_without_root_or_alias_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12,
                     'input_tokens_details': {'cached_tokens': 0}}
            second = {'input_tokens': 20, 'output_tokens': 3, 'total_tokens': 23,
                      'prompt_tokens_details': {'cached_tokens': 8}}
            aggregate = {'input_tokens': 30, 'output_tokens': 5, 'total_tokens': 35}
            (root / 'model_plan.json').write_text(json.dumps({
                'usage': aggregate, 'diagnostic_attempts': [{'usage': first}, {'usage': second}]}))
            verification = {'usage': second, 'attempts': [{'usage': first}, {'usage': second}]}
            for name in ('verification.json', 'verification_R2.json'):
                (root / name).write_text(json.dumps(verification))
            result = run_stage._tokens(root)
            self.assertEqual(result['totals'], {'input_tokens': 60, 'output_tokens': 10, 'total_tokens': 70})
            self.assertEqual(result['calls'], 4)
            self.assertEqual(result['cached_input_tokens'], 16)
            self.assertEqual(result['uncached_input_tokens'], 44)

    def test_missing_cache_usage_is_not_zero_hits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'model_plan.json').write_text(json.dumps({
                'usage': {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12}}))
            result = run_stage._tokens(root)
            self.assertIsNone(result['cached_input_tokens'])
            self.assertIsNone(result['uncached_input_tokens'])

    def test_failed_request_is_not_reported_as_complete_zero_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'decision_log.json').write_text(json.dumps({'failure_type': 'HTTPError', 'http_status': 503}))
            result = run_stage._tokens(root)
            self.assertTrue(result['incomplete'])
            self.assertIsNone(result['cached_input_tokens'])
            self.assertIsNone(result['uncached_input_tokens'])
            self.assertEqual(result['calls'], 0)


if __name__ == "__main__":
    unittest.main()
