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


if __name__ == "__main__":
    unittest.main()
