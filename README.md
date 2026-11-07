# ScanScan：Scan Insertion 智能体开发

## 合并后的运行入口

`main` 已合并 `development_zwy` 的 `Ourmethod/`。`submission/` 保留本地任务约束、网表修复与 EQY、诊断和验收增强；`Ourmethod/` 提供独立的 generator → Scan → Verify → 反馈修复流程，以及 direct/guided 模型核验。两套实现尚未统一运行时，默认仍使用 `submission/`。

本机通过同一个仅限 gproxy/Luna 的启动器选择实现：

```powershell
python -X utf8 dev/run_local.py --case task_1/case4 --output new_submission_run
python -X utf8 dev/run_local.py --implementation ourmethod --runtime-image localhost/scanscan:ourmethod --verifier guided_agent --verification-mode gate --case task_1/case4 --output new_ourmethod_run
```

`Ourmethod` 的模型核验目前仅支持 Task 1，需要其 [运行依赖](Ourmethod/README.md)，包括 Yosys、OpenSTA、tclint 和 pyslang；先按 [Dockerfile](Ourmethod/Dockerfile) 构建上述依赖镜像。`--runtime-image` 挂载当前源码，`--image` 则执行镜像内的冻结代码。配置默认使用 Responses；带 `deepseek_strict` 的示例不适用于 gproxy/Luna。模型核验保留 PASS、FAIL、UNKNOWN；单项通过不代表全任务通过。实验记录和完整结果留在本地 `dev/results/`，不纳入仓库。

本次 [合并实验](dev/分支合并实验_20261107.md)：Luna 下原有实现的 T1/C4、T2/C1 均通过独立验收；同一 T1/C4 的 Ourmethod guided、direct 候选通过 6 项结构检查，但全任务核验均为 UNKNOWN，未发布。guided 未在预算内核验完全部要求，direct 最终引用行号无效；不据此宣称新核验器已完成整例交付。

当前授权限制：用户已明确允许项目开发和测试直接调用 gproxy，无需逐次确认；测试模型仍按此前要求使用 Luna，百炼及其他 Key 仍禁止读取或使用。本地启动器已恢复仅限 gproxy 的入口，在读取 Key 前核对地址、协议及凭据变量名，不会自动切换服务。官方公开平台仍禁止其他模型，并会忽略自定义 URL/model，所以本地调用授权不代表可以上传 Luna 或把 gproxy Key 发往百炼；凭据打包及平台登录入口保持阻断。下文提交、充值及其他外部 API 验证等历史流程不是当前执行授权。详见 [AGENTS.md](AGENTS.md)。

历史 demo-rc4 已交付 11 个公开案例的本地端到端 Demo，按《Scan_Insertion竞赛冲冠实现方案.md》开发；不是当前版本正式模型的通过成绩。
使用官方 `scan-agent-base:ubuntu24` 中的真实 Scan 工具，本地模型按用户要求使用 `gpt-5.6-luna`。
已加入正式平台：首次提交 3737 因入口兼容问题为 0/11；修复后提交 3738 正式流程检查通过 **3/11**，其余 8 例失败。尚未挂榜，无隐藏题得分。

## 当前开发与协作

十小时窗口后继续修复已完成：当前源码/镜像为 `repair51`（1c26c0933c59）。原三道失败中，repair50 的 T2/C2、T1/C6 分别在 126.594、96.797 秒通过独立产物验收；T2/C3 在 repair50 超时后，经同产物核验冲突处理修复，repair51 在 94.062 秒通过，八根因核对有依据。不是同版三例全测或公开 11/11；T2/C2 诊断误报仍未清零。具体改动、分支验证边界及全部失败见[交付文档的继续修复结果](dev/十小时迭代交付_20261002.md#继续修复结果2026-10-02-1107)。

用户授权的十小时迭代已完成阶段交付，真实成功、失败和新组合案例见[迭代记录](dev/十小时迭代记录_20261001.md)。本地默认测试环境使用项目 Dockerfile 构建的 dev-runtime（包含 EQY 所需 make），仍挂载当前源码；显式 --image 才执行冻结镜像内的代码。

十小时收尾 d48（a6a72533b452）同镜像分批公开验收 **8/11 通过、3/11 失败**：T1/C6 需求映射与脚本不一致，T2/C2 最终核验超时，T2/C3 被程序误判为诊断漏报（后续复查确认原诊断存在）。两个大例均通过，其中 T2/C5 到 R5 才撤回多余 JTAG 控制并成功。未达到全通过，不用 d44 的历史 10/11 或其他定向成功替换本次失败。该窗口收尾 d49（f669ac33a0de）仅另含完整报告检查性能修复，两项离线检查及完整真实报告正反审计通过，当时未有新模型整例成绩。精简结果与逐例来源见[交付文档](dev/十小时迭代交付_20261002.md)，不拼跨版成绩。

新防线在修复前冻结题目允许的排除范围，核对完整寄存器报告及开发侧输入覆盖。d44 大例串行在 689.813 秒通过 14 项强化审计，实际覆盖 **799,031 FF**、仅排除允许的 94 个 TAP FF，并保留独立调试复位诊断。原始引用与候选修复证据由真实行号绑定；“允许残留”与 Ignore 权限按分句区分。产物通过不等于诊断满分，正式平台仍为 3738 的 **3/11**，本轮未提交。

新增能力已有真实闭环：原始 SERV 的工具导出失真由模型自动等价改写，EQY 与 18 项独立检查通过；更严格的 UART 任务自动改写 17 个带数据使能 FF，19 项检查、六根因通过。新层级迁移留出首次在 274.30 秒完成 12 处源码改写、覆盖 144 个 FF 和 12 路独立使能，EQY、18 项检查及六根因均通过。这些不代表隐藏题分数；短时限仍有超时风险，H05 初始化和另一个 512 FF 复用层级题的合法参考均待定。后者旧成功因错误时钟波形关联已撤回，不能继续算作泛化成功。

历史对照：d16（f89260a275a9）曾同版公开 **11/11** 本地产物检查通过；d29 整批为 **8/11**。d35 旧自动汇总 9/11，但 C5 越界排除 783,928 个 RAM FF 被误放行，实际有效产物至多 **8/11**。错误证据与独立复核记录保留，不能据旧标志宣称当前版本全部通过。

最新[正式平台联调](dev/正式平台联调记录.md)：3737 暴露平台预创建 `container.log` 触发旧结果保护，已保留失败并修复。第二次提交 **3738** 已结束：构建成功，T1/C1、T1/C4、T2/C1 通过平台运行与文件完整性检查，合计 **3/11**，8 例失败，运行 1466.48 秒；不是正式评分或全部正确性证明。此前指定 URL/model 的本地 T1/C4（107.25 秒，六项结构检查）及 T2/C1（65.92 秒，12 项产物检查、六根因全命中）仍作为独立历史记录。当前未挂榜，无排名；费用按官方 Q8 由队伍承担。全部失败保留。

针对 3738 原始失败记录的共用问题已修改并构建 `capability29`：保留题目未指定的复位极性、合并时不丢失定制 Wrapper 要求、补全提取纠错反馈、按题目行号生成诊断引文、识别 `${变量}` 产物路径。7 项定向离线检查通过。两次指定模型验证被百炼 **Arrearage（欠费/账户计费状态异常）** 拒绝；新包未提交，不能宣称新版本已通过这些案例。具体定位和证据见上述联调记录。

最新 `capability30` 又修复了“无效诊断引用被过滤后，非信号问题可能跳过纠正而漏报”的程序缺口，一个定向回归检查通过；命令说明补充 `dump_ctl` 不能使用 `-file`。已用 [离线回放命令](dev/replay_platform_failures.py) 覆盖八个失败环节，3 例的要求提取可继续、1 例的路径解析不再崩溃，错误旧答案和真实工具失败仍拒绝。镜像与私有 candidate30 已生成，未上传；回放不计新整例通过，官方仍为 3/11。

此后源码已完成请求缓存复用修复：公共手册前置、纠错只在原始上下文末尾追加反馈、变化内容后置；验收和证据检查不放宽。8 项针对性离线检查通过，没有 API 消耗；实际新命中率尚未测量。此修改尚未重建镜像或生成新提交包，历史 `capability30` 不含该改动，详见上述联调记录。

随后恢复仅限 gproxy 的本地入口并用当前源码实测 T1/C4：Luna 生成、真实 Scan 和独立结构验收通过，含验收共 52.687 秒；仅此一个案例，不代表最新版本全部公开例通过。输入 54,451、输出 1,475 token，本次单请求缓存命中为 0，尚无同前缀重复对照来确认缓存收益。汇总在 `dev/results/gproxy_cache_smoke_20261001_01_summary.json`；未使用其他服务 Key，未提交平台。

最新缓存对照已补齐：旧请求连续两次命中 0；Responses 显式提供固定 `prompt_cache_key` 后，重复请求命中 4,864/5,546（87.7%），生产代码新进程复测同样命中。Chat Completions 路径不添加该参数。该结果是小型重复请求的实测，不代表整例或比赛模型命中率；没有重跑 Scan 或整套案例。详情见[正式平台联调记录](dev/正式平台联调记录.md)。

进一步修复控制资料在不同进程中的无序遍历，并无损压缩模型输入 JSON。6 项定向检查通过；T1/C4 的两次真实完整运行及独立结构验收均通过，输入从 54,451 降为 51,971（约 -4.6%）。首轮缓存命中 0，第二轮命中 50,944/51,971（98.02%）；这是同一案例复用，不是全任务平均值或正式比赛成绩。结果位于 `dev/results/gproxy_compact_20261001_01_summary.json` 与 `gproxy_compact_20261001_02_summary.json`，未重跑整套或提交平台。

此前已接入 Task 2 网表工作副本修复与真实 Yosys EQY：证明原始与最终采用的 Pre-scan 功能一致后才运行 Scan，失败不发布。诊断位置从真实源文件生成；按官方区分配置问题必填任务引用、命令/DRC 问题可留空。新增 [knowledge 知识库与技能入口](knowledge/README.md)，按官方规范、工具说明、实测、未解决四类标注可信度，并接入比赛程序的关键词检索。具体成绩及未完成项见 [比赛对齐与网表修复阶段](dev/比赛对齐与网表修复阶段.md)。

该轮 capability19 在两道各六个真实缺陷的合成网表修复题上通过 EQY、Scan 和独立验收，最终 12 个根因均经人工核对正确。capability20 为最后的引用路径约束、知识更新与最终诊断连续编号打包，仅做入口和资源检查，未重跑模型题。H05 仍未解决；一个错误标成结构故障的复位案例作为不应过度修复的对照保留，不计网表修复通过。

此前交付 [控制拓扑与在线验收阶段](dev/控制拓扑与在线验收阶段.md)：新增六个合成控制案例，初始冻结批次 3/6；针对实际失败修复后，跨版本已有 6/6 通过记录，4 个注入根因全部命中、无误报/漏报，无故障对照零修改。新增基于 Liberty 引脚与函数的控制连接提取，将链成员/CTL 一致性、默认分区和显式信号缺陷漏报检查接入运行流程。该阶段镜像 `localhost/scanscan:capability17` 仅定向实测 H07，不冒充该版本六例全量或历史全量成绩。额外探索例 H05 的两级 FF 控制传播尚未证明参考可行，不计入六例分母；详情及诊断文字精度边界见阶段文档。本地输入和全部失败记录保存在 `benchmarks/control6/` 及两个预检目录，未推送 GitHub。

此前交付 [Scan32 本地能力挑战](dev/Scan32说明.md)：32 个原创合成案例，规模 8～1,200,000 个扫描 FF，跨版本开发和定向修复后 32/32 通过真实全流程与独立验收；29 个注入根因全部命中、无误报，两例无故障对照零修改。该阶段镜像 `localhost/scanscan:capability12` 实测 C25～C32 为 8/8，不宣称该镜像首次 32 连过。输入、隔离答案、完整运行及[逐例成绩](benchmarks/scan32/scoreboard.md)整齐保存在本地 `benchmarks/scan32/`，不纳入 Git；不是官方隐藏分数、32 个独立工业 IP 或完整 LEC。

此前的[自建能力增强阶段报告](dev/能力增强阶段报告.md)与[案例及评分说明](dev/cases/README.md)：初始 8 例统一冻结批次产物 7/8 通过，7 个注入根因全部正确识别，两例无故障对照零误报；失败例定向修复后通过，另有纯文字需求补充案例通过。累计 9 个自建案例有通过记录；该阶段镜像 `localhost/scanscan:capability6` 仅做定向验证，不冒充该版本全量通过或正式成绩。

上一轮[能力评估与泛化测试](dev/能力评估与泛化测试.md)记录了首次双时钟探索及其发现的缺口，作为历史基线保留。

此前系统加固已将任务书指定的 Wrapper 端口实现检查接入自动修复，并反馈精确失败报告行；没有硬编码设计名或端口名。定向闭环通过，见 [系统加固记录](dev/系统加固记录.md)。该阶段镜像为 `localhost/scanscan:hardening2`；下方 `demo-rc4` 的 11 例记录属于保留的历史交付，不代表新版本已做全量回归。

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
  ├─ control_context.py：按实际 Liberty 控制引脚提取上游连接、模块作用域及单元函数
  ├─ knowledge_search.py：检索 knowledge/ 中附来源、可信度与边界的经验
  ├─ scan_runner.py：路径注入、diff、真实 Scan 执行、接受轮交付
  │   └─ netlist_repair.py：Task 2 工作副本修改、真实 EQY、证明失败时阻止 Scan
  └─ acceptance.py：实际产物、执行状态、阶段/DRC/任务配置及链成员/CTL 一致性检查
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

首次使用或 Dockerfile 依赖变更后，先构建本地开发环境；官方基础镜像缺少 make，不能单独完成网表等价校验：

    wsl -d podman-machine-default -u user -- podman build --pull=never -t localhost/scanscan:dev-runtime -f /mnt/d/ScanScan/Dockerfile /mnt/d/ScanScan

```powershell
python .\dev\run_local.py --case task_1/case4 --output t1c4_new_run
python .\dev\run_local.py --case task_2/case1 --output t2c1_new_run
python .\dev\verify_flow.py --output dev/results/t2c1_new_run --picorv32
python .\dev\run_local.py --case task_2/case4 --output t2c4_new_run
python .\dev\verify_flow.py --output dev/results/t2c4_new_run --cv32e40p
```

`--output` 指定 `dev/results/` 中的新目录；已有结果不会被覆盖。
本地启动器只选择用户级 Codex `config.toml` 中名为 `gproxy` 的 provider，核对 `https://gproxy.lin.pub/codex/v1`、Responses 协议及 `CODEX_KEY` 变量名后才读取该变量；不跟随其他当前 provider，也不读取其他服务 Key。凭据仅经 stdin 注入容器，不写入命令参数或结果文件。
官方 License 地址来自提供的参考 README。该启动器为本地开发用途，不放入提交镜像。

从当前源码构建同样的提交入口（以下使用新标签，不覆盖交付镜像）：

```powershell
wsl -d podman-machine-default -u user -- podman build --pull=never -t localhost/scanscan:demo-new -f /mnt/d/ScanScan/Dockerfile /mnt/d/ScanScan
```

镜像复制 `submission/` 与项目编写的 `knowledge/`，并安装基础镜像中 EQY 缺少的 make；不包含账号文件、官方数据、Codex 配置或测试输出。
正式运行需注入 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 和 `SCANINSERTION_LICENSE_SERVER`。
客户端默认使用比赛示例的 Chat Completions 协议；`LLM_API_MODE=responses` 用于本地 Luna，启动器会显式设置。不会自动切换模型或供应商。`dev/run_local.py --competition-api` 当前直接拒绝，不读取百炼 Key。此前指定模型本地 T1/C4、T2/C1 以及正式提交 3738 的 3/11 均为历史记录，不代表本轮 gproxy 测试。

正式 ZIP 打包入口 `dev/package_submission.py` 当前已禁用，不能使用 gproxy 授权恢复百炼凭据打包。历史私有包内含凭据，不要上传 GitHub、共享或复用提交。`.env` 不写入镜像。

## 当前局限与下一阶段

- 历史 `demo-rc4` 已覆盖全部 11 例，但完整批次为 10/11，最后一例单独重跑通过；不是最新镜像全量、稳定成功率或隐藏设计支持证明。链成员/CTL 一致性已接入容器内修复，开发侧完整网表功能引脚及串行连通性审计尚未全部回灌。
- Task 1 已接入支持范围内的任务验收及语义失败修复；预处理结构检查仍在开发侧。每轮记录围绕首个失败检查，不代表通用多根因诊断或完整功能证明。
- Task 2 已支持 R3+；在线验收包括支持句式中的链数/链长/分区/输出/配置/信号要求，DRC 零值按任务判断，不对所有 case 一刀切。完整需求仍依赖模型及开发侧检查，诊断误报、遗漏或合并根因仍未达到比赛所需的稳定准确度。
- Task 2 原始配置缺陷采用 dofile 静态引用，不能当作被遮蔽故障的真实运行报错；官方对这种引用的接受方式还需联调。
- 模型判定不是确定性语义证明。支持范围内的链数、分区及报告成员已有程序验收，但不能证明工具没有把应扫描的输入 FF 错误排除，也不等于完整功能等价证明。
- Task 1 可在一个 DRC、预生成和完整插入流程之前执行 ICG 等前置处理；Task 2 阶段重排仍只支持单次三阶段。不支持任意复杂 Tcl 或多次完整插链。
- 解析公开文件的中文秒数限制；官方 Q&A（Q15/A16）已明确取消工具调用次数限制，仅限制总运行时间。隐藏任务说明均为中文（Q28）。
- 异常处理保留失败类型、已有 Scan 执行记录和已保存的诊断；尚未生成的诊断不会补写或伪造。
- 已跑通公开多分区、CTL 黑盒、Wrapper、Segment 及两个大例；新增小型网表等价改写/EQY 的真实闭环，不等于支持任意网表修复或全工业设计功能证明。H05 两级 FF 控制传播仍未解决。大例有历史计时记录，但新能力未做百万规模性能回归。

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
