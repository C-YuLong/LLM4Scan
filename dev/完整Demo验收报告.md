# ScanScan 完整端到端 Demo 验收报告

状态：完整公开案例 Demo 已交付，同一最终镜像覆盖 11/11 例有效通过结果。**不是整批一次全过**：完整批次 `demo_all04` 为 10/11，最后的 T1/C6 经同镜像单例重跑通过；原失败记录及 `all_passed=false` 原样保留。

## 题目与交付范围

比赛要求构建由大语言模型驱动的 Scan Insertion 智能体：Task 1 根据中文任务书、库和网表生成并执行 DFT 脚本；Task 2 诊断给定错误脚本，修复、执行并提供可追溯证据。难点包括时钟与复位、门控控制、多时钟分区、CTL 黑盒、Wrapper/Segment，以及大网表下的上下文与时限管理。

本 Demo 使用官方镜像中的真实 Scan 工具、开发供应商的 `gpt-5.6-luna`，执行全部 11 个公开案例。不是参考答案回放，运行时不读取 golden dofile 或 preset issues。实现方案与参考论文见 [原实现方案](../Scan_Insertion竞赛冲冠实现方案.md)；本报告专门记录可复现交付及实际验收边界。

## 固定版本与复现

- 镜像：`localhost/scanscan:demo-rc4`
- 镜像 ID：`c81529b339fd2c5e6889a63c871106a6a2b8095e5db2674423c06e9d6ed38a8d`
- 完整批次：[demo_all04_summary.json](results/demo_all04_summary.json)（10/11）
- 最后一例重跑：[demo_rc4_c6_retry01_summary.json](results/demo_rc4_c6_retry01_summary.json)（1/1）
- 运行时未挂载工作区 submission 源码；全部案例使用以上同一镜像。
- 大例串行执行。没有重跑历史单元测试套件；只进行真实失败所需的修正、一次严格 JSON 接口最小验证和案例对应的产物审计。

在本机 `D:\ScanScan` 的 PowerShell 中执行：

```powershell
python -X utf8 dev/run_stage.py --image localhost/scanscan:demo-rc4 --output-prefix demo_new
```

此命令默认运行全部 11 例，已有同名前缀会被拒绝。当前批次为尽早检查实际失败，顺序是 T2/C5、C3、C4、C1、C2，随后 T1/C1–C6；全部顺序运行，没有并发 Scan。若需要完全相同的调度顺序：

```powershell
python -X utf8 dev/run_stage.py --image localhost/scanscan:demo-rc4 --output-prefix demo_new_ordered --cases task_2/case5 task_2/case3 task_2/case4 task_2/case1 task_2/case2 task_1/case1 task_1/case2 task_1/case3 task_1/case4 task_1/case5 task_1/case6
```

本地启动器使用现有 WSL Podman 引擎，读取当前 Codex provider 配置和其指定的密钥环境变量，经 stdin 注入容器；不在命令行、代码或镜像中写入密钥。需要本机能访问该模型服务及官方 License 服务。镜像只复制 `submission/`，不包含账号、公开输入、开发结果或 Codex 配置。

## 本批次结果

耗时为本机批量入口测得的秒数，包含容器运行和开发侧独立审计；不是官方计时或性能基准。通过要求包括程序运行验收和相应独立审计；Task 2 还必须通过模型问题证据核验。下列结果来自以上完整批次及单例重跑，使用同一最终镜像，不拼接历史不同版本的通过记录。最终所选 11 例耗时合计 1826.015 秒；完整批次加单例重跑共 1952.609 秒，不含此前开发失败批次及人工排查时间。

| 案例 | 耗时/秒 | 状态 | 主要核对内容 |
| --- | ---: | --- | --- |
| T1/C1 | 38.672 | 通过 | ICG/FF/顶层端口及控制连接结构 |
| T1/C2 | 52.890 | 通过 | AC97 分区、时钟、唯一单元、CTL/SCANDEF |
| T1/C3 | 86.391 | 通过 | 55 内部链、15 Wrapper 链、15 黑盒段 |
| T1/C4 | 58.969 | 通过 | 预处理结构及输出/证据溯源 |
| T1/C5 | 384.187 | 通过 | 1238 条链、615026 个唯一单元、最大链长 500 |
| T1/C6 | 77.828 | 重跑通过 | 88 内部链、3 Wrapper 链、280 Segment、最大链长 99 |
| T2/C1 | 93.157 | 通过 | 4 条链、1610 个唯一单元、DRC 0 |
| T2/C2 | 70.109 | 通过 | 36 条链、10543 个唯一单元、三个分区 |
| T2/C3 | 119.953 | 通过 | 5 条链、359 个唯一单元、DRC 0 |
| T2/C4 | 103.500 | 通过 | 24 条链、2257 个唯一单元、DRC 0 |
| T2/C5 | 740.359 | 通过 | 800 条链、799031 个唯一单元、最大链长 999 |

T1/C3 存在任务允许的不可拆分 CTL 段长度 128，不能宣称所有链长均不超过 100。T2/C2 保留官方 Q&A 允许的 DFTR10；T2/C5 只剩任务允许的 4 条 DFTR10，JTAG TAP 未纳入链。允许残留不等于将这些规则设为 Ignore。

T1/C6 首次独立审计发现模型将 `key2` 作为 Wrapper 数据入口，导致它没有按要求使用自定义专用 Wrapper，因此未计为通过。最终应使用 [T1/C6 重跑结果](results/demo_rc4_c6_retry01_task_1_case6/)，其 18 项独立检查全部通过。其他十例使用 `dev/results/demo_all04_task_1_caseN/` 或 `demo_all04_task_2_caseN/`：

- `decision_log.json`：最终接受轮、执行摘要、需求/缺陷与证据引用。
- `runs/Rn/`：每轮实际 dofile、日志、执行状态、报告与程序验收。
- `model_plan.json`、修复/核验 JSON：实际模型响应与可获得的 token 统计。
- `diffs/`：真实脚本差异；不覆盖早期失败轮。
- `final_results/`：仅从接受轮复制的网表、脚本、报告及日志。
- `flow_acceptance.json`、`wrapper_acceptance.json` 或 `structural_check/acceptance.json`：相应独立检查结果。

接受轮次：T1/C1、C2、C4、C5 为 R1，C3 为 R2；T1/C6 单例重跑为 R1。T2/C1、C3、C4、C5 为 R3，C2 为 R2。Task 2 的 R1 是原始故障现场，不是首次修复候选。

## 已实现的闭环

入口负责总时限、上下文和模型请求；Task 1 与 Task 2 策略共享真实 Scan runner 和产物验收。大网表只向模型提供主要层级、浮空时钟输入、代表性门控定义及必要接口，Scan 始终读取完整网表。模型用实例编号选择真实层级，程序精确展开，不猜测修补路径。

模型按严格 JSON 契约返回脚本、需求和核验记录；这约束结构，不替代内容验证。程序检查工具错误、任务配置、DRC、链数/链长/覆盖和所需输出。模型核验原始缺陷及真实修复证据；最终发布和根目录决策日志由 runner 生成，不交给 Tcl 伪造。对已观察到的 HTTP 502/503/504，仅在剩余预算超过 30 秒时重试一次模型请求，不延长 deadline。

仍使用 Luna：剩余模型调用预算不超过 180 秒时为 low，否则为 medium。当前供应商实际接受 strict JSON 请求；OpenAI Docs 官方页面在本环境返回 403，不将自定义模型别名的行为说成已获官方文档确认。失败网络请求未返回的 token 用量未知。

## 官方答疑与不能宣称的内容

已对齐 [官方 QA](../official/QA.MD)，它与赛题指南同等有效：取消工具调用次数限制，但保留总时限；T2/C2 可保留 DFTR10；正向证据可以来自实际产物；公开案例不计正式成绩，平台提示不能当作满分。

本地公开案例检查不是官方评分、隐藏案例泛化证明、网表连通性穷尽证明或功能等价证明，也不代表随机模型调用每次都成功。开发过程中有实际失败和中断，详见 [开发记录](完整Demo开发记录.md)，均保留原目录。

本次 T1/C6 说明开发侧独立审计仍能发现运行时支持范围之外的语义问题；此类审计失败尚未全部自动回灌到容器内修复。完整批次后只重跑了这一例，没有为获得整批全绿再跑另外十例，也没有修改原始失败汇总。

正式参赛仍需：主办方统一 URL/模型的接口联调（当前运行时实现 Responses，尚未实现正式 Chat Completions 适配）、平台提交验证、隐藏案例评测及诊断准确度提升。按 Q30，当前公开例无须修改原始网表；通用网表修复和 EQY 不在本次公开 Demo 中完成。没有提交比赛平台，也不承诺第一名。
