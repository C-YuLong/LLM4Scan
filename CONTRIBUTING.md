# 协作开发

仓库只保存源码、开发工具及项目文档。不上传账号/API 密钥、`.env`、官方材料、库与网表、容器镜像、运行产物或模型响应。`official/` 和 `dev/results/` 在本地保留但不进入 Git；历史报告中指向它们的链接仅在具备这些本地数据时可用。

## 环境与入口

- Python 3.11+；提交代码使用标准库。历史 `dev/test_luna.py` 另需 `requests`。
- 向主办方获取官方基础镜像 `scan-agent-base:ubuntu24`、合法 License 和公开输入；不要通过本仓库分发。
- 公开输入目录：`official/extracted/public_cases/task_*/case*/input/`。
- 本地 `dev/run_local.py` 针对 Windows + WSL Podman，读取当前用户的 Codex provider 配置及其指定的密钥环境变量，不把配置提交到 Git。
- Linux 容器入口：`/submission/agent_system -input /input -output /output`。运行需按本地环境提供只读输入、可写输出、模型及 License 环境变量；详见 README。正式主办方接口适配仍待完成。

构建项目（需要本机已经载入官方基础镜像）：

```text
docker build -t scanscan:dev .
```

## 提交流程

1. 从 `main` 创建自己的功能分支，按问题提交小而完整的改动，通过 PR 合并；不强制推送共享分支。
2. 先说明问题来自任务要求、工具契约还是实际失败。不要将设计名、案例编号、参考脚本或预期链数硬编码进运行时。
3. 优先复用已有 runner、验收和修复循环。明确区分工具执行失败、产物语义错误、证据错误和外部接口失败。
4. 先用已有产物检查变更；确需真实运行时，选择最小代表案例并使用新的结果目录。不默认运行全量 Scan、付费模型调用或历史测试套件。
5. 提交前检查 `git diff --cached --stat` 和文件列表；不要用 `git add -f` 把被忽略的数据加入仓库。

不需要官方数据的本阶段定向检查：

```text
python -m unittest dev.test_acceptance.AcceptanceTests.test_wrapper_policy_uses_explicit_port_lists_not_design_names
```

其他部分测试依赖本地官方输入或历史结果；缺少这些材料时不应宣称测试通过。在线修复测试可能调用付费 API 和真实 EDA 工具，运行前确认环境和范围。
