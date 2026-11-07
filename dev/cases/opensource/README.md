# 开源设计迁移检查（2026-10-01）

用于补充手工生成的 Scan32/组合案例。只在本地生成比赛格式的门级输入，不把第三方源码或大网表加入项目 Git，也不把这些公开 IP 当作隐藏题。

| 设计 | 固定来源版本 | 许可证 |
| --- | --- | --- |
| Secworks AES core | https://github.com/secworks/aes ，80dc4718e1dcbbdb4b0dd1bdb393d8f7b98981dc | BSD-2-Clause，保留原 LICENSE |
| Alex Forencich UART | https://github.com/alexforencich/verilog-uart ，1b867e53af738e4a8bc7c839ca2f1c07f40382dc | MIT，保留原 COPYING |
| SERV（含寄存器文件） | https://github.com/olofk/serv ，f200eb2ed7b69ac1c6b8eddd47654522aeee5ce8 | ISC，保留原 LICENSE |
| I2C Wishbone 主控制器 | https://github.com/alexforencich/verilog-i2c ，a65be4045e898a52e791c6ee71f8f79a7cd2e129 | MIT，保留原 COPYING 与 RTL 文件头；上游已弃用，不声称活跃维护 |
| SHA-256 核 | https://github.com/secworks/sha256 ，837c5cc396f001d18f2c765721c585716eb439ae | BSD-2-Clause，保留原 LICENSE 与 RTL 文件头 |

源码位于 benchmarks/opensource_20261001/sources/。本目录的 Yosys 配方只读取对应 RTL，不执行下载仓库的脚本；使用比赛提供的 Sky130 Liberty 生成门级设计。容器挂载 /sources（只读）、/tech（只读）、/recipes（只读）和每例 /work（生成目录），执行 yosys -s /recipes/aes.ys、uart.ys 或 serv.ys。不要把技术库挂载到容器 /lib，它是系统动态库目录。

综合产物及日志保存在 benchmarks/opensource_20261001/build/ 下。这里的 write_json 用于开发侧独立结构审计，不提供给模型；它不是 RTL 到门级的形式等价证明。模型只读取冻结的比赛输入，参考脚本和判分信息隔离在输入目录外。任务书与缺陷仍须在首次模型运行前冻结。

## 冻结任务与结果

- P06_uart_plain：在已知固定 UART 上新增的目标单元限制，不是新 IP 或未见设计。79 FF 中 17 个有数据使能；六项原脚本故障沿用 v2，但最终扫描单元仅允许 sdfxtp_1，不能改用 sedfxtp_1 或丢弃 DE 行为。允许必要的工作副本等价改写，保留 FF 实例身份，禁止输入假设；600 秒，4 条链、最长 20 位。prepare_uart_plain.py 在 d30 冻结后创建，input 与 oracle 隔离。无模型 qualification/plain30 通过真实 EQY 和 19 项独立检查，总工具/证明 23.325 秒；参考实现改写 17 个 enabled FF，Scan 实际使用已证明副本。
  首次模型 plain30 在 251.24 秒失败（未实施必要改写），coverage31 在 577.51 秒失败（不受 Scan 支持的 pin 内行为表达式，后退回直接映射仍丢 DE）；均保留。提供受影响源码完整覆盖状态与真实库候选后，d33 structural33 在 203.66 秒、R2 完成 17 处结构化改写、真实 EQY PASS、Scan 和 19 项独立检查，六根因逐项核对正确。3 响应输入/输出 82,685/9,429，缓存 0；不是账单。本例已为开发案例，不称首次留出通过，也不改变原 v2 正确映射即可通过的事实。

- P05_sha256：新留出，冻结于 d24 之后、首次模型运行之前。sha256.ys 只读取三个指定 RTL，映射为 1033 个异步清零 FF 和 1 个异步置位 FF，同一复位端口控制；严格 Yosys check 零问题，不把它称为 RTL→门级形式等价。prepare_sha256.py 冻结六个问题：丢复位输入的映射、复位释放极性、链数、链长、输出端口命名、缺 CTL。1034 FF，11×94 位，300 秒，禁止改网表。参考资料位于隔离 oracles/，仅工具资格检查可读；模型只读 input/。任何据失败修改系统后须改记为开发案例。
  工具资格 sha256_24 的 17 项检查通过。首次 sha256_holdout24 在 146.95 秒、R2 失败：运行验收与六条诊断均通过，核验模型却因看不到上级目录已有 diff/决策记录而否决总体；后续返回相同方案后正确停止重跑，未发布。d26 补齐真实 runner 证据上下文，sha256_bookkeeping26 开始回归；**本例现已转为开发案例**，首个留出成绩仍是失败。
  sha256_bookkeeping26 已完成：119.26 秒、R2，17 项独立检查通过，六根因与原始故障逐项核对一致。三次响应输入/输出 93,820/4,985，缓存输入 1,792 tokens；不是账单。没有修改输入、降低题限或改写首次失败。

- P01_uart_enabled_v2：Task 2，79 FF（17 个带 DE），6 个原始脚本缺陷，600 秒。prepare_uart.py 一次性冻结独立可执行的原脚本及隔离的参考改写，不覆盖已有文件。库中实际存在 sedfxtp_1，直接正确映射即可保留 DE，**不要求改写网表**；mux+DFF 方案仅作等价流程的独立资格检查，不给模型读取。
- P02_aes：Task 1，2476 FF，16 条链，每条不超过 155，300 秒。指定 sdfrtp_1/sdfstp_2 类型，不能用功能类似但题目未允许的另一单元代替。
- P03_serv_clean：1336 FF、8 条链、每条 167，原计划为 300 秒无故障对照，但 standalone09 暴露工具对常量拼接的错误导入/导出，实际并非无故障。d21 original21、d22 working_copy22 均失败并保留；d23 exact23 首次自动修改两处原始赋值，在 197.38 秒内完成真实 EQY PASS、Scan 和 18 项独立检查，R2。它没有读取 buffered 输入或隔离参考来生成改写。响应输入/输出 81,215/5,165、缓存输入 14,080 tokens；不是账单。另有 4 个合法的非首级移位 FF，独立审计以实际串行连接识别，与真实输出失真分开处理。原始失败和最初错误的 clean 命名均保留，不改写历史；详见十小时记录阶段 K/U。
- P03_serv_buffered：与 clean 隔离的新输入，使用 serv_buffered.ys 的标准缓冲展开、无用单元清理和二进制常量输出。buffered10 预检的 18 项结构检查通过，仍有 1336 FF；原始到缓冲输入的第一轮 EQY **FAIL**，失败保存在 build/serv_buffered02/proof。proof_purged 在清理无用别名后得到 EQY PASS（381 秒）。d14 的 buffered14 模型结果：67.28 秒、R1、零诊断零修改、18 项独立检查通过。它不是运行时自动改写原始 SERV 输入，原题失败仍保留。独立审计通过实际 D/Q 连线支持非首级移位 FF，并比较功能端点，避免错误连接仍共享旧网名时漏验。
- P04_i2c_fifo：i2c.ys 仅加载三个指定 RTL，三个 FIFO 深度均为 8，生成 462 FF（354 个带 DE、108 个普通 FF）。标准缓冲表达在冻结前生成，pre/post 连接需独立核对；不是 RTL 到门级形式证明。prepare_i2c.py 固定六个脚本缺陷：错误带使能映射、SE 极性、链数、链长、边沿混合、缺 SCANDEF；300 秒，7 条链、每条至多 72 位。输入冻结后不改。frozen15：d15 模型 156.88 秒、R3，16 项独立产物检查通过；六个最终根因与预设逐项人工核对一致、无新增根因。但 R2 把原本正确的 rpt_insertion_info 改为不存在的 rpt_scan_insertion_info，工具报 CMD-0074 后 R3 修好；第一条诊断把原始报告称为 final report，叙述仍需改善，不能称所有诊断文字完美。输入/输出 tokens 220209/6609，5 次有用量响应，缓存 0。
- d5 模型结果：runs/first05/P01_uart_enabled，85.30 秒、R2；runs/mapping05/P02_aes，75.04 秒、R1。两者分别通过 16/17 项独立产物检查，均未改网表。UART 旧题目的 original.dofile 缺少路径前置设置，额外引入了非预期故障；虽然最终产物正确，6 条诊断只覆盖了预设六项，不能算完整诊断通过。原案例和结果不修改，v2 仅补齐独立脚本路径设置并在重新测试前冻结。
- AES 的 first01 保留失败：模型最初用缩写直接查库失败，后续删去映射导致实际类型不符。新上下文提供库内真实扫描单元名，指导修正标识符而不是放弃要求。

独立检查入口 dev/verify_opensource.py（Linux/Yosys）：用 --input、--output、--case uart|aes|serv|i2c|sha256|uart_plain 指定已完成的模型结果。它检查全部功能引脚/端口、实例集合、所有原始 FF 的唯一扫描覆盖、真实串行连线、CTL/DEF 成员及明确配置；工作副本改写另外要求真实 EQY。审计目录只新建一次，保留原始结果，不悄悄改分。

qualification/equivalent03 使用过不完整的 EQY 时钟处理；后续负例发现其配置可误接受错误数据连接，因此该证明记录**不可作为可靠通过证据**。clocked04 使用显式时钟状态建模后重新证明；参见十小时记录。两个模型通过结果都未使用这条网表改写路径。
