"""Local stage timing, provider-reported usage and configurable cost estimates."""

from contextlib import contextmanager
from datetime import datetime
from functools import wraps
import csv
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
from zoneinfo import ZoneInfo


_session = None

TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_tokens")


def write_stage_reports(target, stages, currency="CNY"):
    """Export each measured stage; absent provider usage stays unknown, never zero."""
    target = Path(target)
    fields = ("case", "id", "code", "label", "run", "attempt", "status", "outcome",
              "elapsed_seconds", "model_call", "reasoning_effort", *TOKEN_FIELDS,
              "usage_complete", "estimated_cost", "currency")
    rows = []
    for stage in stages:
        tokens = stage.get("tokens") or {}
        if not stage.get("model_call"):
            tokens = dict.fromkeys(TOKEN_FIELDS, 0)
        row = {key: stage.get(key) for key in fields}
        row.update({key: tokens.get(key) for key in TOKEN_FIELDS})
        row["usage_complete"] = all(tokens.get(k) is not None for k in TOKEN_FIELDS[:3])
        row["currency"] = stage.get("currency") or currency
        rows.append(row)
    tmp = target / "stage_metrics.csv.tmp"
    with tmp.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(target / "stage_metrics.csv")
    def cell(value):
        return "未知" if value is None else str(value).replace("|", "\\|").replace("\n", " ")
    text = ["# 逐阶段耗时与 Token", "",
            "Token 按 API usage 记录。缓存属于输入，推理属于输出，不额外累加；本地步骤为 0。缺失用量记为未知。一次模型请求内部的读任务、思考、写脚本不能再精确拆分计费。", "",
            "| 案例 | ID | 阶段 | 状态 | 秒 | 输入 | 输出 | 合计 | 缓存输入 | 推理 | 估算费用 |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---:|---|"]
    for row in rows:
        values = [row.get("case") or "—", row.get("id"), row.get("label") or row.get("code"),
                  row.get("status"), row.get("elapsed_seconds"),
                  *(row.get(k) for k in TOKEN_FIELDS),
                  f"{cell(row.get('estimated_cost'))} {row['currency']}"]
        text.append("| " + " | ".join(cell(v) for v in values) + " |")
    tmp = target / "stage_report.md.tmp"
    tmp.write_text("\n".join(text) + "\n", encoding="utf-8")
    tmp.replace(target / "stage_report.md")


def auto_output(source, root):
    source = Path(source).resolve()
    label = "_".join(source.parts[-3:-1]) if source.name == "input" else source.name
    label = "".join(c if c.isalnum() or c in "_-" else "_" for c in label)[:64]
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S_%f")
    return Path(root).resolve() / f"{label}_{stamp}_{os.urandom(3).hex()}"


class Tee:
    def __init__(self, original, log):
        self.original, self.log = original, log

    def write(self, text):
        self.original.write(text)
        self.log.write(text)
        self.flush()
        return len(text)

    def flush(self):
        self.original.flush()
        self.log.flush()

    def __getattr__(self, name):
        return getattr(self.original, name)


def _price(name):
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"Invalid nonnegative price: {name}")
    return result


def usage_counts(usage):
    if not isinstance(usage, dict):
        return None
    def integer(value):
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    return {
        "input_tokens": integer(usage.get("input_tokens")),
        "output_tokens": integer(usage.get("output_tokens")),
        "total_tokens": integer(usage.get("total_tokens")),
        "cached_input_tokens": integer((usage.get("input_tokens_details") or {}).get("cached_tokens")),
        "reasoning_tokens": integer((usage.get("output_tokens_details") or {}).get("reasoning_tokens")),
    }


def estimate_cost(counts, prices):
    if counts is None or counts["input_tokens"] is None or counts["output_tokens"] is None:
        return None
    incoming, outgoing = prices["input"], prices["output"]
    if incoming is None or outgoing is None:
        return None
    cached_rate = prices["cached_input"]
    cached_rate = incoming if cached_rate is None else cached_rate
    cached = counts["cached_input_tokens"]
    if cached is None:
        if cached_rate != incoming:
            return None
        cached = 0
    if cached > counts["input_tokens"]:
        return None
    # Reasoning is already included in output_tokens; never bill it twice.
    return ((counts["input_tokens"] - cached) * incoming + cached * cached_rate
            + counts["output_tokens"] * outgoing) / 1_000_000


class Session:
    def __init__(self, target):
        self.target = Path(target)
        self.started = time.monotonic()
        self.lock = threading.RLock()
        self.records = []
        self.active = []
        self.deadline = None
        self.prices = {"input": _price("LLM_INPUT_PRICE_PER_MILLION"),
                       "cached_input": _price("LLM_CACHED_INPUT_PRICE_PER_MILLION"),
                       "output": _price("LLM_OUTPUT_PRICE_PER_MILLION")}
        self.currency = os.environ.get("LLM_PRICE_CURRENCY", "CNY")
        self.stdout, self.stderr = sys.stdout, sys.stderr
        self.console = (self.target / "console.log").open("a", encoding="utf-8", buffering=1)
        sys.stdout, sys.stderr = Tee(self.stdout, self.console), Tee(self.stderr, self.console)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.heartbeat, daemon=True)
        self.thread.start()
        print(f"[运行开始] 结果目录：{self.target}", flush=True)
        self.save()

    def emit(self, kind, record):
        with self.lock:
            event = {"event": kind, "elapsed_seconds": round(time.monotonic() - self.started, 3),
                     "stage": {k: v for k, v in record.items() if k != "_started"}}
            with (self.target / "stages.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    def snapshot(self):
        now = time.monotonic()
        records = [dict(r, elapsed_seconds=round(now-r["_started"], 3)) if r["status"] == "running" else dict(r)
                   for r in self.records]
        for r in records:
            r.pop("_started", None)
            r["tokens"] = usage_counts(r.get("usage")) if r["model_call"] else {
                key: 0 for key in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_tokens")}
            r["estimated_cost"] = estimate_cost(r["tokens"], self.prices) if r["model_call"] else 0.0
        calls = [r for r in records if r["model_call"]]
        totals = {key: sum(r["tokens"][key] or 0 for r in calls if r["tokens"] is not None)
                  for key in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_tokens")}
        incomplete = [r["id"] for r in calls if r["tokens"] is None or any(
            r["tokens"][key] is None for key in ("input_tokens", "output_tokens", "total_tokens"))]
        costs_missing = [r["id"] for r in calls if r["estimated_cost"] is None]
        return {"model": os.environ.get("LLM_MODEL"), "output_dir": str(self.target),
                "elapsed_seconds": round(now-self.started, 3), "exit_code": getattr(self, "exit_code", None),
                "remaining_seconds": max(0, round(self.deadline-now, 3)) if self.deadline else None,
                "prices_per_million": self.prices, "currency": self.currency,
                "known_token_totals": totals, "usage_complete": not incomplete,
                "usage_missing_calls": incomplete, "cost_missing_calls": costs_missing,
                "known_estimated_cost": sum(r["estimated_cost"] or 0 for r in calls),
                "total_estimated_cost": None if costs_missing else sum(r["estimated_cost"] for r in calls),
                "cost_note": "Estimate from configured rates, not provider invoice. Missing usage is unknown, not zero. Reasoning tokens are included in output tokens.",
                "stages": records}

    def save(self):
        with self.lock:
            data = self.snapshot()
            tmp = self.target / "metrics.json.tmp"
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(self.target / "metrics.json")
            write_stage_reports(self.target, data["stages"], self.currency)

    def describe(self, record):
        counts = usage_counts(record.get("usage"))
        if not record["model_call"]:
            return "token=0"
        if counts is None:
            return (f"token=未知（API 尚未返回 usage）；收到推理字符={record.get('reasoning_chars', 0)}，"
                    f"输出字符={record.get('output_chars', 0)}（字符不是 token）")
        cost = estimate_cost(counts, self.prices)
        return (f"输入token={counts['input_tokens']} 输出token={counts['output_tokens']} "
                f"其中推理token={counts['reasoning_tokens']} 缓存输入token={counts['cached_input_tokens']}；"
                + (f"估算费用={cost:.6f} {self.currency}" if cost is not None else "费用=未知（缺单价或用量）"))

    def heartbeat(self):
        while not self.stop.wait(10):
            with self.lock:
                for record in self.active:
                    if record["code"] == "scan" and record.get("run_dir"):
                        run = Path(record["run_dir"])
                        try:
                            with (run / f"{run.name}.log").open("rb") as stream:
                                stream.seek(0, 2)
                                stream.seek(max(0, stream.tell()-8192))
                                tail = stream.read().decode("utf-8", errors="replace")
                            commands = re.findall(r"CMD-0034\]\s+@\d+:\s*([A-Za-z_]\w*)", tail)
                            if commands:
                                record["activity"] = "Scan 当前命令：" + commands[-1]
                        except OSError:
                            pass
                    remaining = f"，总预算剩余 {max(0, self.deadline-time.monotonic()):.1f}s" if self.deadline else ""
                    print(f"[进行中] {record['label']} | {record.get('activity', '执行中')} | "
                          f"本步 {time.monotonic()-record['_started']:.1f}s{remaining} | {self.describe(record)}", flush=True)
                    self.emit("heartbeat", record)
                self.save()

    def close(self, code):
        self.stop.set()
        self.thread.join(timeout=2)
        try:
            self.exit_code = code
            self.save()
            data = self.snapshot()
            print(f"[运行结束] 总耗时 {data['elapsed_seconds']:.2f}s | 已知token={data['known_token_totals']} | "
                  f"用量完整={data['usage_complete']} | 估算总费用={data['total_estimated_cost']} {self.currency}", flush=True)
            print(f"[已保存] {self.target}/console.log、stages.jsonl、metrics.json、stage_metrics.csv、stage_report.md", flush=True)
        finally:
            sys.stdout, sys.stderr = self.stdout, self.stderr
            self.console.close()


def start(target):
    global _session
    _session = Session(target)


def finish(code):
    global _session
    if _session is not None:
        try:
            _session.close(code)
        finally:
            _session = None


def set_deadline(deadline):
    if _session:
        _session.deadline = deadline


def save_response(record, response, output_text):
    """Persist final assistant output before parsing; omit private reasoning text."""
    if _session is None or "id" not in record:
        return None
    folder = _session.target / "model_calls" / f"C{record['id']:04d}"
    folder.mkdir(parents=True, exist_ok=False)
    data = {key: response.get(key) for key in ("id", "model", "status", "usage")}
    data["output"] = [item for item in response.get("output", []) if item.get("type") != "reasoning"]
    (folder / "response_without_reasoning.json").write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    (folder / "output.txt").write_text(output_text, encoding="utf-8")
    record["response_artifact"] = str(folder.relative_to(_session.target))
    _session.save()
    return folder


@contextmanager
def phase(code, label, *, model_call=False, **details):
    session = _session
    if session is None:
        yield {}
        return
    with session.lock:
        record = {"id": len(session.records)+1, "code": code, "label": label,
                  "status": "running", "model_call": model_call, "usage": None,
                  "_started": time.monotonic(), "started_seconds": round(time.monotonic()-session.started, 3), **details}
        session.records.append(record)
        session.active.append(record)
        print(f"[阶段开始] {label}", flush=True)
        session.emit("start", record)
        session.save()
    try:
        yield record
        record["status"] = "completed"
    except BaseException as exc:
        record["status"] = "aborted" if isinstance(exc, (TimeoutError, KeyboardInterrupt)) else "error"
        record["failure_type"] = type(exc).__name__
        if hasattr(exc, "code") and isinstance(exc.code, int):
            record["http_status"] = exc.code
        raise
    finally:
        with session.lock:
            record["elapsed_seconds"] = round(time.monotonic()-record["_started"], 3)
            session.active.remove(record)
            outcome = f" | 结果={record['outcome']}" if "outcome" in record else ""
            failure = f" | {record['failure_type']}" if "failure_type" in record else ""
            print(f"[阶段结束] {label} | {record['status']}{outcome}{failure} | {record['elapsed_seconds']:.2f}s | {session.describe(record)}", flush=True)
            session.emit("end", record)
            session.save()


def tracked(code, label, run_arg=None):
    def decorate(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            run = Path(args[run_arg]).name if run_arg is not None and len(args) > run_arg else None
            details = {"run_dir": str(args[run_arg])} if run else {}
            with phase(code, label + (f" [{run}]" if run else ""), **details) as metric:
                result = function(*args, **kwargs)
                if code == "scan":
                    metric["outcome"] = result["exit_status"]
                    metric["error_count"] = len(result.get("errors", []))
                elif code == "acceptance":
                    metric["outcome"] = "通过" if result[0]["accepted"] else "不通过"
                    metric["failed_checks"] = [k for k, v in result[0]["checks"].items() if v is not True]
                return result
        return wrapper
    return decorate


def model_event(record, event):
    if "_started" not in record:
        return
    kind = event.get("type", "")
    now = time.monotonic()
    milestones = []
    if "first_event_seconds" not in record:
        record["first_event_seconds"] = round(now-record["_started"], 3)
        milestones.append("first_event")
    if kind in ("response.reasoning_text.delta", "response.reasoning_summary_text.delta"):
        key, activity = "reasoning_chars", "正在接收模型推理"
    elif kind == "response.output_text.delta":
        key, activity = "output_chars", "正在接收脚本/诊断输出"
    else:
        key, activity = None, None
    if key:
        if key not in record:
            record["first_"+key+"_seconds"] = round(now-record["_started"], 3)
            milestones.append(key)
        record[key] = record.get(key, 0) + len(event.get("delta", ""))
        record["activity"] = activity
    response = event.get("response") or {}
    if isinstance(response.get("usage"), dict):
        record["usage"] = response["usage"]
    if kind in ("response.completed", "response.failed", "response.incomplete"):
        record["terminal_event"] = kind
        record["terminal_event_seconds"] = round(now-record["_started"], 3)
        milestones.append(kind)
    if milestones and _session:
        with _session.lock:
            _session.emit("model_progress", record)
            _session.save()
        print(f"[模型进度] {record['label']} | {record.get('activity', kind)} | "
              f"{now-record['_started']:.2f}s | {_session.describe(record)}", flush=True)
