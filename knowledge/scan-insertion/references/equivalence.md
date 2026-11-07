# 网表工作副本与真实 EQY
检索词：EQY|LEC|等价|修改网表|netlist_edits|Pre-scan|重汇合|reconvergence|DFTR9|常量拼接|十六进制

## A-E01 官方要求，可信度 A

来源：赛题指南第 5、10、30、34～36 页及 Q&A Q30。修改原电路时必须保存最终使用的 Pre-scan、与原始输入的 diff、Yosys EQY 报告。失败或未完成不得交付成功。输入、标准单元库和工具本身不能修改。

## C-E02 环境实测，可信度 C

来源：2026-10-01 `scan-agent-base:ubuntu24` 环境探测：EQY/Yosys/求解器已在 `/opt/oss-cad-suite/bin`，缺少 make；项目 Dockerfile 安装 make。EQY 当前版本成功标志为工作目录的 `PASS` 文件，不能误用 SymbiYosys 的 status 文件规则。

`benchmarks/netlist2/qualification/structural_original/N01_clock_reconvergence` 中，正确脚本仍产生 32 个 DFTR9。`equivalent_rewrite/.../reports/lec_report.rpt` 对重汇合逻辑改写实际给出 EQY PASS，但该次启动器误读状态，未启动 Scan。它只证明等价试验，不能计为模型或全流程通过。

修正状态读取后，`equivalent_rewrite2/N01_clock_reconvergence` 达到真实 EQY PASS、Scan DRC 0 和参考产物验收通过；仍是无模型预检。负例 `reject_wrong_data/N01_clock_reconvergence` 故意把 data[0] 接成 data[1]，EQY 返回 2，系统未启动 Scan、未发布最终结果。以上记录验证证明门槛，不代替模型测试。

2026-10-02 的 SERV `qualification/native_eqy17`：同一配方在容器原生临时目录证明用 64 秒，完整归档后 70.04 秒；原 Windows 挂载目录为 381 秒。大量 EQY 小文件的跨文件系统 I/O 是此环境的实测瓶颈，不推断所有平台都有同样加速。生产路径保留全部文件在 `equivalence/result.tar.gz`，并复制真实 PASS/FAIL/UNKNOWN 标记及 logfile.txt 到原证据位置；最终仍要求真实返回 0、PASS、未超时且最终使用的候选未变。`netlist2/qualification/native18_positive` 通过，`native18_negative` 的错误数据连接仍 NOT_PROVEN、Scan 未启动。不要通过删除失败证据、减少证明深度或增加输入假设来缩短时间。

## 实施边界

使用 Liberty 的实际功能模型，不能用 `read_liberty -lib` 把所有单元变黑盒后声称证明功能。当前执行路径不允许模型编写 EQY 配置、不固定输入、修改只能精确命中给定网表工作副本。要求检查设计完整性并拒绝使用未建模单元。

等价的组合布尔改写不代表工具能处理所有异步、多时钟和带状态修复。证明失败可能是电路不等价，也可能是当前证明策略能力不足；必须保留日志区分，不能把两者都改成通过。

## C-E03 不需要修结构的对照，可信度 C

N02_reset_reconvergence 初始被错误标为六个缺陷。实际 clear_n 正确释放为 1 后，两路 OR 都输出 1，后续复位 AND 可控；frozen18 仅修五项配置即完成，没有修改网表、没有 EQY。它不是“漏报结构问题”，也不计为网表修复通过。原始错误 oracle 不改，审查说明在 `benchmarks/netlist2/fixture_review.md`。不能将时钟重汇合的结论不加分析地套到复位网络。

## C-E04 模型端到端结果，可信度 C

capability19：`benchmarks/netlist2/runs/repair19_diagnosis/N01_clock_reconvergence` 与 `benchmarks/netlist_holdout/runs/frozen19/N03_dual_clock` 分别完成 32/128 FF 的真实模型诊断、原电路等价改写、EQY PASS、Scan DRC 0 和独立连线/产物验收。各六个真实根因人工核对一致，启动器耗时 205.80/206.47 秒，均 R2。

N03 初始计划额外声称应改变 internal_clocks，最终核验否决这项无任务依据的诊断；不能把中间计划当作最终诊断成绩。两例是有关联的合成布尔控制结构，不代表工业级任意网表修复或异步电路时序等价。

## C-E05 等价配置必须接受正例并拒绝错误连接，可信度 C

来源：2026-10-01 十小时阶段 H，UART 隔离改写与 N01 错误数据连接负例。改变实例内部引脚语义时，直接按物理实例/引脚名匹配可能产生不正确的切点。隐藏实例名能避免这类切点，但**单独这样做不够**：d5 的 SAT 配置曾错误接受 data[0]→data[1]，因此不能采信该配置的 PASS。

当前处理保留功能网/端口名，并用 clk2fflogic 显式建模时钟状态后运行 EQY。已见错误连接得到 NOT_PROVEN 且未启动 Scan；N01 等价时钟化简、UART 带使能 FF 的 mux+DFF 改写得到 PASS。仅说明这些验证结果，不能推断所有多时钟/异步电路都可证明；保持原时限，不添加任意输入假设。UART 实际存在兼容 sedfxtp 单元，应优先正确映射，不能因这里记录了改写就给它增加不必要的网表修改。

新限制案例 `P06_uart_plain` 明确不允许该兼容扫描类型，不能以默认优选覆盖当前任务。d30 `runs/plain30` 的直接映射仍丢 DE，被运行检查拒绝；模型虽拿到全部 17 处受影响源声明，却因整体网表为片段视图而拒绝构造改写。后续上下文的 requested_type_coverage 显示已扫描到的类型声明数、完整展示数及 complete：只在所有声明的全部原始行都实际展示时才为 true。它证明的是源声明覆盖，不是功能等价、实例展开计数或修改正确性；before 仍须精确唯一匹配，最终仍须真实 EQY。片段未完整展示时不得用这项元信息猜测缺失代码。

`runs/coverage31` 在 577.51 秒、五个工具候选后仍失败：模型开始改写全部 17 处，但先放入未连接 SCE/SCD 的扫描 FF（EQY 拒绝），后补常量可过 EQY，Scan 却以 VRLG-I-0043/CMD-3025 拒绝 pin 内的行为布尔表达式；最终退回零改写，DE 丢失仍被拒绝。故“Yosys 能读且等价”不等于“安装的 Scan 能读”。需要结构化实现时，用实际库内普通 FF/组合 mux 和声明的连线，不能让 runner 凭文字自动综合。新增 rewrite_cell_candidates 仅在观察到功能输入丢失时提供有限真实库候选与函数，按函数去重；mux 名字只是查找线索，不证明引脚语义，不是完整映射库，也不自动改写电路。`coverage31` 另实际触发一次纯诊断复核，复用了 R5 工具产物并保存独立 review；最终仍失败，不算修复成功。

后续 `runs/structural33/P06_uart_plain` 由模型自行精确改写 17 处为 mux 与普通 DFF，203.66 秒、R2，实际 EQY PASS 后 Scan 成功，19 项独立检查通过、六根因核对正确。已修改系统后回归的开发案例，不是首轮留出成绩；不推广为所有使能/异步寄存器都可按同一种结构改写。

迁移留出 `enabled_hierarchy_v2_20261002/runs/holdout34` 首次运行 274.30 秒、R2，通过真实 EQY 和 18 项独立检查，六根因均正确。12 处 leaf 源声明在两层复用中对应 144 个 FF、12 路独立数据使能；模型只见 input，未使用隔离参考。这说明该次模型改写能保持不同实例的功能绑定，不等于完整工业网表泛化保证。第一份资格题误用保留字 release，被工具拒绝；v2 在任何模型评测前仅更正该实例名，旧失败仍保存。

## C-E06 工具读写网表不自动保证功能，可信度 C

来源：2026-10-02 本地 benchmarks/tool_repro_20261002/constant_alias、constant_alias_binary、constant_alias_buffered。安装的 Scan 202605-bp2，仅 load_netlist/dump_netlist 即把 `{a,b,2'h0}` 和 `{a,b,2'b00}` 错导出为 b→位1、a→位2、位3接0，位0无驱动；标量 `zero=1'h0` 漏连接，`zero=1'b0` 对照正确。SERV standalone09 有同类失败，DRC=0/全位数不能发现它。

极小例用标准 Yosys insbuf -buf sky130_fd_sc_hd__buf_1 A X 展开连接、write_verilog -nohex 后再经 Scan 导出；独立组合等价检查 9/9 成功。原坏导出有 3 项未证明。仅转换十六进制为二进制不够；该缓冲方式目前仅证明此极小例，不代表 SERV 或所有库均已修好。

不可据此自动修改禁止改网表的 Task 1，也不可修改工具导出结果后伪称原始产物。Task 2 的任何工作副本转换仍需必要性、题目许可和原始到实际 Pre-scan 的 EQY。不能把一个工具版本的局部输入表达式限制推广成所有常量/拼接语法都不合法。

SERV 扩大实验 build/serv_buffered02：insbuf 后需 clean 移除仅驱动无用别名的缓冲，check -assert 为零问题；buffered10 的 Scan 导出 18 项独立检查通过，包括真实功能端点、1336 位全覆盖、8×167 串行连接。原始到缓冲版的第一份 EQY 配置 proof **FAIL**（372 秒），部分分区把原先同网的不同别名拆成独立输入；保留失败。

proof_purged 在 clk2fflogic 后以 opt_clean -purge 清理无用别名，381 秒得到 EQY PASS，无输入假设。该清理已加入生产证明配置；iter10h_eqy_purged_negative_20261002_01 故意将 data[0] 接为 data[1]，仍为 NOT_PROVEN（value.0 未证明），Scan 未启动。buffered14 的独立新 SERV 输入随后由 Luna 在 67.28 秒 R1 完成，18 项独立检查通过、零诊断零修改。这个输入是在模型运行前准备的，**不是智能体自动修好了原始 SERV 的工具表达式问题**；原始 P03_serv_clean 失败不改。

同版本极小例 `constant_alias/runs/flags17_correct_top` 加 `dump_netlist -write_const_net -write_isolate_net`，以及再加 `-rtl`，仍导出错位连接和悬空 zero。不能把这些官方选项当成已验证的修复；这也不证明工具所有导出方式均失败。

运行时的 `constant_output_integrity` 仅检查原本固定 0/1 的顶层输出位：实际 Liberty 的常量/缓冲/反相器、连续赋值与别名由 Yosys 解释，不展开所有功能逻辑。原 SERV `qualification/guard21_raw_space` 的三个常量输出位均未保留，被拒绝；buffered SERV `guard21_buffered_space` 的三个位保留，整体通过。极小正例五个位保留。必须区分 `checked=false`（未覆盖/不能检查）与 `checked=true, passed=true`，不能将前者称作检查通过；该范围不替代完整功能等价。实际专用 wrapper 位会改变测试模式下的输出，当前跳过该检查，不据此保证 wrapper 功能。大型公开网表只读重审测得 31.318 秒、进程峰值 38,232 KiB，62 个常量位均保留；这些开销是本机实测，不是通用上限。

`runs/exact23/P03_serv_clean` 首次由模型自动改写原始 SERV：将实际输出赋值中的宽常量拆成两个单比特二进制常量，标量零输出改用二进制常量；197.38 秒、R2，原始→实际工作副本 EQY PASS，Scan 后 18 项独立检查通过（1336 FF、8×167、全部功能引脚/原端口端点/串行链）。不是所有拼接都应这样改写，也不是只改进制就能普遍解决。d21 曾重复返回无变更后失败；d22 的 before 多一个空格，精确编辑校验失败；均保留。现在精确编辑检查提前复用到已有一次诊断纠正，不模糊替换或增加重试。

原 SERV 最初被命名为 clean，但独立检查其 R1 导出证实功能失真，不能强求“零修改”来阻止必要修复。无故障对照需由实际原始产物建立；后处理后的 buffered 输入仍属于独立无故障对照，不能与原始题混算。禁止覆盖输入/改变功能逻辑不自动等于禁止经 EQY 证明的工作副本表示变化；题目若明确禁止改网表则仍不得改。
