"""Responses API transport with independently configured role clients."""

import json
import os
import re
import time
import urllib.request
from urllib.error import HTTPError

from model_contract import TASK1
from generator.task1 import TASK1_INSTRUCTIONS
import telemetry


def reasoning_effort(timeout, setting=None):
    setting = (os.environ.get("LLM_REASONING_EFFORT", "auto") if setting is None else setting).strip().lower() or "auto"
    if setting not in {"auto", "none", "low", "medium", "high", "max"}:
        raise ValueError("Unsupported LLM_REASONING_EFFORT")
    return ("low" if timeout <= 180 else "medium") if setting == "auto" else setting



def normalize_json_output(text):
    candidate = text.strip()
    # Only unwrap a complete code block. Never search for arbitrary braces or
    # rewrite escapes inside a Tcl script embedded in JSON.
    match = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", candidate, re.S | re.I)
    return (match[1], "markdown_fence") if match else (candidate, "none")



def generate(prompt, timeout, instructions=None, output_schema=TASK1, *, _settings=None):
    model = os.environ["LLM_MODEL"] if _settings is None else _settings.model
    base_url = os.environ["LLM_BASE_URL"] if _settings is None else _settings.base_url
    api_key = os.environ["LLM_API_KEY"] if _settings is None else _settings.api_key
    payload = {
        "model": model,
        "instructions": instructions or TASK1_INSTRUCTIONS,
        "input": prompt,
        "reasoning": {"effort": reasoning_effort(timeout, None if _settings is None else _settings.reasoning_effort)},
        "stream": True,
        "store": False,
        "text": {"format": {"type": "json_schema", "name": "task_contract", "schema": output_schema}},
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/responses",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
    )
    completed = None
    properties = output_schema.get("properties", {})
    code, label = ("llm_verify", "模型核验结果与问题证据") if "all_requirements_met" in properties else (
        ("llm_diagnose", "模型诊断原始问题并生成修复脚本") if "issues" in properties else (
            ("llm_repair", "模型修改候选脚本") if "repair_action" in properties else ("llm_plan", "模型生成或修复 Task 1 脚本")))
    request_deadline = time.monotonic() + timeout
    failed_http = []
    for attempt in range(2):
        try:
            with telemetry.phase(code, label, model_call=True, attempt=attempt+1,
                                 model=model, request_chars=len(prompt)+len(payload["instructions"]),
                                 reasoning_effort=payload["reasoning"]["effort"]) as metric:
                metric["activity"] = "等待 API 连接及首条事件"
                call_started = time.monotonic()
                with urllib.request.urlopen(request, timeout=max(0.001, request_deadline - time.monotonic())) as response:
                    metric["headers_seconds"] = round(time.monotonic()-call_started, 3)
                    metric["http_status"] = response.status
                    metric["activity"] = "已连接，等待模型输出"
                    for line in response:
                        if not line.startswith(b"data: ") or line.strip() == b"data: [DONE]":
                            continue
                        event = json.loads(line[6:])
                        telemetry.model_event(metric, event)
                        if event.get("type") == "response.completed":
                            completed = event["response"]
                            break
                        if event.get("type") in {"error", "response.failed", "response.incomplete"}:
                            raise RuntimeError("Model request failed or incomplete")
                    if completed is None:
                        raise RuntimeError("Missing response.completed")
            break
        except HTTPError as exc:
            if attempt or exc.code not in (502, 503, 504) or request_deadline - time.monotonic() <= 30:
                raise
            failed_http.append(exc.code)
    if completed is None:
        raise RuntimeError("Missing response.completed")
    text = "".join(c["text"] for item in completed.get("output", []) for c in item.get("content", []) if c.get("type") == "output_text")
    response_dir = telemetry.save_response(metric, completed, text)
    usage = completed.get("usage")
    if failed_http and usage is not None:
        usage["failed_http_statuses"] = failed_http
    with telemetry.phase("model_parse", "解析模型 JSON 响应") as parse_metric:
        json_text, normalization = normalize_json_output(text)
        parse_metric["normalization"] = normalization
        if response_dir:
            (response_dir / "json_candidate.txt").write_text(json_text, encoding="utf-8")
            (response_dir / "parse_info.json").write_text(json.dumps({"normalization": normalization})+"\n", encoding="utf-8")
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as exc:
            if response_dir:
                (response_dir / "parse_error.json").write_text(json.dumps({
                    "message": exc.msg, "line": exc.lineno, "column": exc.colno,
                    "position": exc.pos, "output_chars": len(text),
                    "coordinate_space": "json_candidate.txt",
                }, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
            raise
        if not isinstance(parsed, dict):
            raise ValueError("Model JSON must be an object")
    return parsed, usage


class ModelClient:
    """Role-local client. Never changes process environment or shares a conversation."""
    def __init__(self, settings):
        self.settings = settings

    def generate(self, prompt, timeout, instructions=None, output_schema=TASK1):
        return generate(prompt, timeout, instructions, output_schema, _settings=self.settings)

    def respond(self, messages, tools, instructions, timeout, max_output_tokens, output_schema, tool_choice="auto"):
        if isinstance(tool_choice,dict) and tool_choice.get("type")=="function":
            # Some compatible providers ignore named tool_choice when other
            # functions remain available. Make the final decision the only
            # offered function without changing or discarding past evidence.
            tools=[tool for tool in tools if tool["name"]==tool_choice["name"]]
        if self.settings.api_protocol == "deepseek_beta_strict":
            from deepseek_strict import respond
            return respond(self.settings, messages, tools, instructions, timeout, max_output_tokens, tool_choice)
        payload = dict(model=self.settings.model, instructions=instructions, input=messages,
                       tools=tools, tool_choice=tool_choice, parallel_tool_calls=False, store=False, stream=True,
                       max_output_tokens=max_output_tokens,
                       reasoning={"effort": reasoning_effort(timeout, self.settings.reasoning_effort)},
                       text={"format": {"type": "json_schema", "name": "verification", "schema": output_schema}})
        request = urllib.request.Request(self.settings.base_url + "/responses",
            data=json.dumps(payload).encode(), headers={"Authorization": "Bearer " + self.settings.api_key,
                                                       "Content-Type": "application/json"})
        completed = None
        request_deadline=time.monotonic()+timeout
        with telemetry.phase("llm_verify", "Verify 按需查证", model_call=True,
                             model=self.settings.model, reasoning_effort=payload["reasoning"]["effort"],
                             request_chars=len(json.dumps(payload, ensure_ascii=False))) as metric:
            with urllib.request.urlopen(request, timeout=max(0.001, timeout)) as response:
                iterator=iter(response)
                while True:
                    remaining=request_deadline-time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Verify model deadline exhausted")
                    socket=getattr(getattr(getattr(response,"fp",None),"raw",None),"_sock",None)
                    if socket is not None:socket.settimeout(max(0.001,remaining))
                    try:line=next(iterator)
                    except StopIteration:break
                    if not line.startswith(b"data: ") or line.strip() == b"data: [DONE]":
                        continue
                    event = json.loads(line[6:])
                    telemetry.model_event(metric, event)
                    if event.get("type") == "response.completed":
                        completed = event["response"]
                        break
                    if event.get("type") in {"error", "response.failed", "response.incomplete"}:
                        raise RuntimeError("Verify model request failed; native tool support is required")
            if completed is None:
                raise RuntimeError("Missing response.completed for Verify")
            metric["usage"] = completed.get("usage")
        output_text = "".join(c.get("text", "") for item in completed.get("output", [])
                              for c in item.get("content", []) if c.get("type") == "output_text")
        telemetry.save_response(metric, completed, output_text)
        return completed
