"""Exercise R3 with saved model plans and REAL Scan execution, without API calls."""

import argparse
import copy
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "submission"))
import scan_agent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--failed-plan", type=Path, required=True)
    parser.add_argument("--fixed-plan", type=Path, required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    failed = json.loads(args.failed_plan.read_text(encoding="utf-8"))["plan"]
    fixed = json.loads(args.fixed_plan.read_text(encoding="utf-8"))["plan"]
    responses = [copy.deepcopy(p) for p in (failed, failed, fixed)]
    for plan, action in zip(responses[1:], ("Replay unchanged failed candidate to exercise continued repair", "Replay corrected candidate")):
        plan["repair"] = {
            "located_object": "ICG exclusion hierarchy",
            "root_cause": "Historical model candidate confused the ICG exclusion and nonscan FF scopes",
            "action": action,
        }
    argv = ["agent_system", "-input", args.input, "-output", str(args.output)]
    with patch.object(scan_agent, "generate", side_effect=[(p, {"development_replay": True}) for p in responses]), patch.object(sys, "argv", argv):
        code = scan_agent.main()
    decision = json.loads((args.output / "decision_log.json").read_text(encoding="utf-8"))
    statuses = [run["exit_status"] for run in decision["tool_runs"]]
    passed = code == 0 and decision["final_run"] == "R3" and statuses == ["error", "error", "completed"]
    result = {"passed": passed, "statuses": statuses, "model_responses": "historical replay; not live Luna repair", "tool_execution": "real dftexp_scan"}
    (args.output / "replay_check.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
