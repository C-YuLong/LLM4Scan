# ScanScan：Scan Insertion 智能体开发

已交付全部 11 个公开案例的端到端 Demo，按《Scan_Insertion竞赛冲冠实现方案.md》开发。
使用官方 `scan-agent-base:ubuntu24` 中的真实 Scan 工具，本地模型按用户要求使用 `gpt-5.6-luna`。
尚未提交评测平台，不是完整参赛版本。

## 当前开发与协作

最新系统加固已将任务书指定的 Wrapper 端口实现检查接入自动修复，并反馈精确失败报告行；没有硬编码设计名或端口名。定向闭环通过，见 [系统加固记录](dev/系统加固记录.md)。最新开发镜像为 `localhost/scanscan:hardening2`；下方 `demo-rc4` 的 11 例记录属于保留的历史交付，不代表新版本已做全量回归。

共同开发请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。仓库不包含官方数据、账号、API 配置、镜像和运行结果；文档中的这些本地路径需由各开发者自行准备。

## 完整 Demo 交付

完整结果、逐例耗时和产物位置见 [完整 Demo 验收报告](dev/完整Demo验收报告.md)。最终镜像 `localhost/scanscan:demo-rc4`（`c81529b339fd`）已覆盖 11/11 例通过结果：完整批次 `demo_all04` 通过 10 例，T1/C6 同镜像单例重跑通过。不是整批一次全过；原失败记录保留。

已对齐 [官方答疑](official/QA.MD)，全部失败与中止经过见 [完整 Demo 开发记录](dev/完整Demo开发记录.md)。两大例实际结果：openC906 为 1238 条链 / 615026 个唯一单元；VeeR 为 800 条链 / 799031 个唯一单元。运行、任务支持范围内的验收及对应独立审计均通过。

```powershell
python -X utf8 dev/run_stage.py --image localhost/scanscan:demo-rc4 --output-prefix demo_new
```

这条命令顺序运行全部 11 例，并自动执行各案例对应的结构、Wrapper 或报告产物审计。`--image` 模式不挂载工作区 submission 源码。结果写入 `dev/results/demo_new_summary.json` 及各案例的新目录，已有结果不会覆盖。

官方 Q&A 已取消工具调用次数限制，但每例总运行时限仍有效。公开例和平台流程成功不等于正式成绩；本地使用 Luna，正式评测须使用主办方提供的 URL 和模型。

## 历史阶段四交付

完整结果见 [阶段四验收报告](dev/阶段四验收报告.md)。本阶段新增两个真实工具闭环：T1/C3 得到 55 条内部链、15 条 Wrapper 链，整合 15 个黑盒 CTL 段；T1/C6 得到 88 条内部链、4 条 Wrapper 链，最大链长 99，280 个长移位寄存器均登记为 Segment 并入链。两例 DRC 为零，各通过 18 项专项检查。C3 存在任务允许的不可拆分 CTL 段长度 128，不宣称所有链均不超过 100。

```powershell
python dev/run_local.py --case task_1/case3 --output stage4_new_ctl
python dev/verify_wrapper.py --case ctl --output dev/results/stage4_new_ctl
python dev/run_local.py --case task_1/case6 --output stage4_new_des
python dev/verify_wrapper.py --case des --output dev/results/stage4_new_des
```

阶段四历史镜像 `localhost/scanscan:stage4`（`548ad1d46429`），入口 smoke 通过。当时累计跨版本有 9/11 例通过记录，尚未整批验证最新版。C6 该阶段经历 R1–R5 自动修复才通过，不是一次成功或稳定成功率。本阶段未重跑历史 27 项测试和全量回归，只做必要的真实案例调通、专项产物检查与一个验收逻辑正反检查。

当前 `run_stage.py` 已自动包含 Wrapper 专项审计；上面的独立命令仍可检查已有产物，无需重跑 Scan。[阶段三验收报告](dev/阶段三验收报告.md)、[阶段二验收报告](dev/阶段二验收报告.md)及下文 milestone1 内容为历史记录。

## 当前架构

```text
scan_agent.py：统一入口、时限与失败决策、模型调用、Task 1 策略
  └─ repair_task.py：Task 2 诊断与修复策略（显式接收模型调用函数）
两种策略共同使用：
  ├─ scan_runner.py：路径注入、diff、真实 Scan 执行、接受轮交付
  └─ acceptance.py：实际产物、执行状态、完整插链的阶段/DRC/报告一致性检查
```

Task 2 不再反向导入主入口；初始化和总时限只由入口管理。生成体不能重定义 runner 路径，两类任务共享同一条规则。没有新增框架或第三方依赖。

程序验收保存到 `runs/Rn/acceptance.json`，与模型 `verification_Rn.json` 分开。Task 2 必须同时满足程序检查、模型核验和问题证据要求才交付。报告提供可扫描数量/上链数量时，检查它们与链长总和的一致性；这不能证明工具将所有应扫描对象都正确分类。Task 1 按任务区分预处理与完整插链，后者接入受支持的任务配置、DRC、分区和输出检查；不是完整语义证明。

## 已实现

- `/submission/agent_system -input ... -output ...` 统一入口。
- Task 1：读取任务书、文件清单及实际层级实例路径，由模型生成脚本和需求映射，执行真实工具；工具或验收失败且剩余预算超过 30 秒时继续反馈修复，保存每轮脚本、日志、验收和 diff。
- Task 2：保留原始脚本的语义，仅迁移路径；R1 保存真实现场，R2 完整修复；工具或验收失败且预算允许时继续 R3+，保留逐轮核验、diff 与修复尝试。
- 单次完整插链按文档顺序组织 DRC → 链预生成 → 插入，避免模型重复猜测阶段顺序。
- 原始日志、每轮 dofile、diff、实际网表/报告、决策引用与最终副本。
- 检查日志中的 ERROR，而不只检查退出码；总时间预算与 Scan 进程组超时终止。
- 接口异常、工具启动失败、总时限中止均落盘失败决策；Scan 中止保留 execution.json，旧结果目录拒绝覆盖。
- 只读输入挂载；凭据从当前 Codex provider 的环境变量读取，经 stdin 注入容器，不落盘。
- 独立 Yosys 结构验收脚本，用于 T1/C1、C4。它不是 LEC，也不是通用 Verilog 分析器。
- Task 2 在线程序检查实际阶段顺序和 DRC 报告；任务明确要求零违例时，非零或缺失总数不能被模型意见放行。未要求零值时保留任务相关的警告核验，不擅自放宽其他用例。

## 在当前 Windows 主机运行

本机 Podman Windows 端 SSH 转发启动失败，因此开发命令直接使用其现有 WSL 引擎。
不需要改变其他项目的容器配置。

```powershell
python .\dev\run_local.py --case task_1/case4 --output t1c4_new_run
python .\dev\run_local.py --case task_2/case1 --output t2c1_new_run
python .\dev\verify_flow.py --output dev/results/t2c1_new_run --picorv32
python .\dev\run_local.py --case task_2/case4 --output t2c4_new_run
python .\dev\verify_flow.py --output dev/results/t2c4_new_run --cv32e40p
```

`--output` 指定 `dev/results/` 中的新目录；已有结果不会被覆盖。
本地启动器读取用户级 Codex `config.toml` 当前 provider；需要其 `env_key` 指定的环境变量可用。
官方 License 地址来自提供的参考 README。该启动器为本地开发用途，不放入提交镜像。

从当前源码构建同样的提交入口（以下使用新标签，不覆盖交付镜像）：

```powershell
wsl -d podman-machine-default -u user -- podman build --pull=never -t localhost/scanscan:demo-new -f /mnt/d/ScanScan/Dockerfile /mnt/d/ScanScan
```

镜像中仅复制 `submission/`，不包含账号文件、官方数据、Codex 配置或测试输出。
正式运行需注入 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 和 `SCANINSERTION_LICENSE_SERVER`。
目前客户端仅实现 Responses 协议；正式指定模型的 Chat Completions 接入仍待实现。

## 当前局限与下一阶段

- 最终镜像已覆盖全部 11 例，但完整批次为 10/11，最后一例单独重跑通过；不是稳定成功率或隐藏设计支持证明。开发侧独立审计发现的语义问题尚未全部自动回灌容器内修复。
- Task 1 已接入支持范围内的任务验收及语义失败修复；预处理结构检查仍在开发侧。每轮记录围绕首个失败检查，不代表通用多根因诊断或完整功能证明。
- Task 2 已支持 R3+；在线验收包括支持句式中的链数/链长/分区/输出/配置/信号要求，DRC 零值按任务判断，不对所有 case 一刀切。完整需求仍依赖模型及开发侧检查，诊断误报、遗漏或合并根因仍未达到比赛所需的稳定准确度。
- Task 2 原始配置缺陷采用 dofile 静态引用，不能当作被遮蔽故障的真实运行报错；官方对这种引用的接受方式还需联调。
- 模型判定不是确定性语义证明。链数、覆盖、分区等需继续实现独立程序验收。
- Task 1 可在一个 DRC、预生成和完整插入流程之前执行 ICG 等前置处理；Task 2 阶段重排仍只支持单次三阶段。不支持任意复杂 Tcl 或多次完整插链。
- 解析公开文件的中文秒数限制；官方 Q&A（Q15/A16）已明确取消工具调用次数限制，仅限制总运行时间。隐藏任务说明均为中文（Q28）。
- 异常处理保留失败类型、已有 Scan 执行记录和已保存的诊断；尚未生成的诊断不会补写或伪造。
- 已跑通公开多分区、CTL 黑盒、Wrapper、Segment 及两个大例；尚无跨隐藏设计的泛化证明、网表连通性/功能等价证明，未完成输入网表修复与 EQY。大例有真实计时记录，但未作稳定性能基准。

详细本次结果见 `dev/开发进展_20260922.md`。

## 最新闭环回归（2026-09-23）

- T1/C4：`dev/results/t1c4_flow_20260923_01/`，Luna 生成与真实 Scan 执行共 38.83 秒；六项 Yosys 结构检查及交付证据审计通过。
- T2/C1：`dev/results/t2c1_flow_20260923_01/`，Luna 诊断、修复及核验共 77.07 秒；六个缺陷、两轮真实 Scan，独立检查 4 条链 / 1610 个唯一 FF / clk / test_se / DRC 0 通过。
- Task 1 多轮分支：`dev/results/t1c4_replay_20260923_01/`，回放历史模型候选，真实执行得到 R1 error → R2 error → R3 completed，16.86 秒。它验证控制流，不代表在线 Luna 连续修复能力。
- `dev/test_execution.py`：Linux 容器内三个测试通过，覆盖单次 Scan 超时、全局 deadline、工具无法启动、API 异常和旧结果保护。
- `dev/verify_flow.py`：检查 final_results 与接受轮次逐字节一致、diff 存在、证据原文与行号；`--picorv32` 额外检查公开 T2/C1 的报告要求，不是通用语义验收器或 LEC。

新增 T2/C4：`t2c4_flow_20260923_03` 在线 Luna 运行 52.40 秒；24 条链、最大链长 100、CTL 中 2257 个唯一单元、DRC 0，16 项报告/文件/证据检查通过。但该轮诊断含路径相关误报，不把产物正确等同于诊断满分。

语义失败闭环：`t2c4_retry_20260923_02` 用历史失败计划作为 R2 候选，后续 Luna 核验与修复实时调用，真实 Scan 执行 R1→R2→R3，共 63.45 秒。R2 虽工具正常退出但 DRC 2219，被拒绝；R3 修复极性后通过上述 16 项检查。开发启动器 `--initial-plan PATH` 仅用于这种首响应回放，提交代码不含回放逻辑。

Task 2 重构后 T2/C1 回归 `t2c1_loop_20260923_01` 43.55 秒、12 项检查通过。容器内四个定向测试通过，包含“模型批准但报告 DRC 非零时程序仍拒绝”。最新镜像 `localhost/scanscan:milestone1` 为 `9ef29a498025`。

本次架构优化后的回归：T1/C4 `t1c4_arch_20260923_01` 22.53 秒，结构与证据检查通过；T2/C1 `t2c1_arch_20260923_01` 63.92 秒，12 项检查通过；T2/C4 `t2c4_arch_20260923_01` 71.14 秒（首修复候选回放，后续在线 Luna），R2 DRC 拒绝→R3 通过 16 项检查。Task 1 三轮历史候选回放 `t1c4_arch_replay_20260923_01` 19.13 秒通过。六个定向测试通过，当前镜像更新为 `ae5ef7e87384`。耗时受网络、模型输出及并行运行影响，不据此宣称性能提升。
