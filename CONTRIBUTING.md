# 协作开发

仓库只保存源码、开发工具及项目文档。不上传账号/API 密钥、`.env`、官方材料、库与网表、容器镜像、运行产物或模型响应。`official/` 和 `dev/results/` 在本地保留但不进入 Git；历史报告中指向它们的链接仅在具备这些本地数据时可用。

## 环境与入口

- Python 3.11+；提交代码使用标准库。历史 `dev/test_luna.py` 另需 `requests`。
- 向主办方获取官方基础镜像 `scan-agent-base:ubuntu24`、合法 License 和公开输入；不要通过本仓库分发。
- 公开输入目录：`official/extracted/public_cases/task_*/case*/input/`。
- 本地 `dev/run_local.py` 针对 Windows + WSL Podman，读取当前用户的 Codex provider 配置及其指定的密钥环境变量，不把配置提交到 Git。
- Linux 容器入口：`/submission/agent_system -input /input -output /output`。运行需提供只读输入、可写输出、模型及 License 环境变量；详见 README。默认 Chat Completions，本地 Luna 显式 `LLM_API_MODE=responses`。主办方真实接口仍待联调。

构建项目（需要本机已经载入官方基础镜像）：

```text
docker build -t scanscan:dev .
```

## 提交流程

1. 从 `main` 创建自己的功能分支，按问题提交小而完整的改动，通过 PR 合并；不强制推送共享分支。
2. 先说明问题来自任务要求、工具契约还是实际失败。不要将设计名、案例编号、参考脚本或预期链数硬编码进运行时。
3. 优先复用已有 runner、验收和修复循环。明确区分工具执行失败、产物语义错误、证据错误和外部接口失败。
4. 先用已有产物检查变更；确需真实运行时，选择最小代表案例并使用新的结果目录。不默认运行全量 Scan、付费模型调用或历史测试套件。
   本机 32 GiB 内存曾因三个大网表同时运行耗尽提交内存、导致 WSL 失去响应。大例使用 `run_stage.py --cases ...` 顺序执行，不同时启动多组大例；中断结果保留并标记，不算模型成功或正常时限内失败。
5. 提交前检查 `git diff --cached --stat` 和文件列表；不要用 `git add -f` 把被忽略的数据加入仓库。

不需要官方数据的本阶段定向检查：

```text
python -m unittest dev.test_acceptance.AcceptanceTests.test_wrapper_policy_uses_explicit_port_lists_not_design_names
```

其他部分测试依赖本地官方输入或历史结果；缺少这些材料时不应宣称测试通过。在线修复测试可能调用付费 API 和真实 EDA 工具，运行前确认环境和范围。

自建能力案例及独立评分见 [dev/cases/README.md](dev/cases/README.md)。原创小网表属于测试源码，
可以随仓库协作；官方库、生成后的输入包、oracle 运行副本和结果仍只放在被忽略的 dev/results。
本地 runner 现在只挂载本例输入和独立输出，不能把开发侧答案与输入放在同一目录。
配置缺陷的 violated_requirement 必须引用任务原文；按官方指南，命令/DRC 缺陷允许为空。
旧回放计划可能不满足当前证据契约，不能把被拒绝的旧计划当成实时模型诊断。

可复用规则和实验结论放在 [knowledge/](knowledge/README.md)，标明来源、可信度、适用范围与失败反例。项目技能入口不自动安装到个人 Codex；比赛运行时直接检索同一批 Markdown，不依赖 Codex 的技能机制。不要将官方原文全集、原始模型响应或带秘密信息的日志复制到知识库。
