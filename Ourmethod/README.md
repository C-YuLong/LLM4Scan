# Ourmethod 核心实现

独立实现脚本生成、真实 Scan 执行、按证据核验和反馈修复。运行时不依赖仓库中的实验、批测或 GT 比较脚本。

## 模块

- `agent_system` / `scan_agent.py`：启动入口与主循环。
- `generator/`、`repair_task.py`：生成和修复任务。
- `scan_runner.py`、`stage_artifacts.py`：工具执行与阶段产物记录。
- `verifier/`：legacy、direct、guided 核验器，以及只读证据工具。
- `verifier/structural/`：网表、Liberty 和报告解析，逐对象结构检查。
- `llm_client.py`、`deepseek_strict.py`：模型请求与工具调用协议。
- `configuration.py`、`config*.toml`：组件选择、模型配置及配额。
- `telemetry.py`、`verifier/diagnostics.py`：耗时、用量及子任务诊断。
- `offline_verify.py`：对自行保存的候选进行核验，不重新执行 Scan。

## 运行

需要 Python 3.11+、可用且已授权的 Scan 工具；结构和连接查询还需要 Yosys、OpenSTA。源码解析依赖固定版本的 tclint 和 pyslang：

```bash
python3 -m pip install -r Ourmethod/verifier/source-reader-requirements.txt
```

通过本地环境变量提供模型配置和 Scan 环境；TOML 中仅存环境变量名称，不存密钥。

```bash
export LLM_MODEL='your-model'
export LLM_BASE_URL='https://your-model-endpoint'
export LLM_API_KEY='your-api-key'

python3 Ourmethod/agent_system \
  --config Ourmethod/config.direct_agent.toml \
  --verification-mode gate \
  -input /path/to/input \
  -output /path/to/new-output
```

`config.toml` 默认使用 legacy；`config.direct_agent.toml` 使用 direct，默认 shadow（记录新核验结果，旧核验控制交付）。`config.guided_agent.deepseek_strict.toml` 使用 guided + gate，核验端需兼容其配置的 DeepSeek Beta Strict 协议，不能直接用于任意模型服务。`gate` 表示所选核验器控制反馈和交付。模型核验后端当前用于 Task 1。

guided 默认并发 3，每项最多 8 次模型调用、24 次工具调用；整次 Verify 120 秒。`max_total_tokens=0` 关闭总 token 门槛，但仍记录用量；`dynamic_evidence_tools=false` 保持固定工具接口。每项独立上下文，规划只提交 ID 和要求描述，工具范围不在规划中重复填写。

在具备官方基础镜像的环境中，可从仓库根目录构建：

```bash
docker build --platform linux/amd64 -f Ourmethod/Dockerfile -t llm4scan:ourmethod .
```

实际运行时应自行挂载输入、输出并注入模型和许可证环境，不要将凭据写入镜像或提交到仓库。

## 状态与诊断

guided 结果从输出轮次的 `verification/subtasks.md` 查看；`subtasks.json` 是机器可读总览，`workers/001/` 等目录保存状态、事件、完整交互和证据。PASS、FAIL、UNKNOWN 为核验结论；超时、请求异常、未启动等为独立运行状态。工具局部 PASS 不自动代表整项要求或全案通过。

当前仍在开发，完整任务尚有模型响应异常、调用次数耗尽和收尾不足等问题。本次核心快照不包含 `tests/`、`evaluation/`、`evaluations/`、GT/批测入口、历史结果、官方设计数据或模型调用记录。
