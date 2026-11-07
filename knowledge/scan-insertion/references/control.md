# 控制路径：先看实际连线
检索词：DFTR1|DFTR3|DFTR8|DFTR9|clock|门控|复位|初始化|mux|latch|selector|off_state

## B-C01 工具说明，可信度 B

来源：Scan_User_Manual，PDF 页 17（印刷页 12）、53（48）、58～59（53～54）。

`associated_internal_clocks` 显式指定的内部引脚波形与源时钟相同；这不是“看到门就必须加”的通用修复。DFTR1 检查时钟不可控，DFTR8 检查时钟是否控制时序元件，DFTR9 检查开启时的有效性。必须结合实际单元函数和连接定位，不能只复述规则名。

## C-C02 本地实测，可信度 C

来源：`benchmarks/control6/runs/frozen14/H04_hierarchical_mux/`（失败）、`repair16_mux/H04_hierarchical_mux/`（通过）；`dev/控制拓扑与在线验收阶段.md`。

H04 的实际根因是 mux 常量选错。初始计划错加内部时钟关联，后来改正常量仍保留关联，12 轮后超时；去掉未经证实的关联并按 Liberty 函数选对输入后通过。适用：实际片段与单元函数已确认的普通组合 mux，不代表所有层级时钟都不需要关联。

2026-10-02 d35 公开 T1/C2 的普通 Liberty ICG 已配置全局测试使能，但模型额外关联其层级 ENCLK 输出，使 RX 分区变成 3 链而非要求的 2 链；各分区 internal_clocks 已为 none。`iter10h_icg_association_probe_20261002_01` 仅撤去该关联，保留其他配置，真实 Scan 与 21 项独立检查通过，2,229 个 FF 全覆盖。这是无模型反事实，不是模型成绩。普通 ICG 含 CE 闩锁或被 Verilog 模块包裹，不足以证明需要显式关联；特殊时钟缓冲器示例不可直接套用。

H03/H06 低相锁存门控有通过记录；H02 证明门控 enable 可与数据 FF 的扫描 enable 不同，需各自按实际反相/组合逻辑判断极性。不要凭信号名字或单元名推断极性。

2026-10-02 tv80_polarity_probe_20261002_01 只将 d13 已修脚本的 clk off_state 恢复原来的 1，保留闩锁关联和其他修复；真实 DRC 0，13 项独立检查仍通过。该题原文没有指定此值；不能把“候选改成 0 且通过”当作原值 1 有缺陷的证据。d13 核验已剔除此项误报。这只是该任务的反事实工具实验，不意味着所有时钟极性可互换。

2026-10-02 `iter10h_no_latch_exclusion_probe_20261002_01` 保留 d34 C3 的已通过配置，仅移除时钟路径闩锁的 set_scan_element false：Scan 实际仍有一个该闩锁的 DFTR10，故关联与排除是本例的两个独立必要动作。d34 模型修复了两者，却只报告关联，属于漏诊，不称诊断满分。不能推广为任意 latch 都要排除；题目、实际 clock-on-data 连接和 DRC 证据共同限定本结论。

## D-C03 未解决，可信度 D

来源：`benchmarks/control6_preflight/`、`benchmarks/control6_preflight2/` 的 H05。两个被排除的控制 FF 传播 mode，设置 `clock_gating_init_cycles 4` 后仍有 1,024 个 DFTR9；尚无有效参考流程。不能把组合八级门控成功称为八级状态初始化成功，也不能因本次失败断言工具不支持所有此类设计。

`control_context.py` 只提取有限结构上下文，遇 FF 数据路径停在状态边界；不等于完整网表展开、时序仿真或可控性证明。

后续探索 `benchmarks/control6/qualification/state_association18/H05_state_pipeline` 将实际门输出关联到源时钟后，数据 FF 插链但仍有一个源时钟 DFTR8，仍未通过；不能将减少违规数称为解决。

2026-10-02 复核 reused_hierarchy：旧 qualified17/history20 使用 inv_tick/Y（实际为源 tick_b 的反相）作为 associated_internal_clocks，虽然旧结构检查放行，128 个 a3 FF 的报告边沿却为 (+)，不符合原始真实路径。用户手册 PDF p.17 明确关联引脚被赋予与源相同的波形和关闭电平，该参考完整资格撤回。source42 模型在 300 秒原预算内超时，真实源行定位有效，但派生域仍未解决；另一个仅关联双反相后同相控制引脚的无模型 same_wave43 定向试验仍有 128 DFTR1 和 2 DFTR8。暂未找到遵守全部原要求的合法解法，不用改题或错误波形声明刷通过。

2026-10-01 定向诊断 `benchmarks/control6/qualification/state_learning21/H05_state_pipeline`：初始化 4/2 均仍有 1,024 个 DFTR9；仅在诊断时把 node51/node52 标为可扫描也未消除违规（未插链，未更改原网表）。`examine_scan_drc_violation -violation_id DFTR9-1` 显示 r0/CLK 在该检查中仍为 0，连接 node54/X。该实验排除了“只需取消这两个排除标记”的简单解释，尚未证明工具不支持所有 FF 初始化。

## B-C04 镜像随附命令说明，可信度 B

来源：`/opt/dftexp_scan/share/md/cmd/set_scan_drc_cfg.md`（官方基础镜像内）。`-clock_gating_init_cycles` 为设计学习增加时钟脉冲，允许值 **0、1、2、3、4**，默认 0，用于级联时序门控。不得根据门数猜任意大值；该说明本身不保证 H05 的具体控制 FF 排除方式可行。

## B-C05 可自动生成的扫描使能，可信度 B

来源：官方镜像 `/opt/dftexp_scan/share/md/cmd/set_scan_signal.md`。clock/reset/constant 按实际需要指定；scan_enable 等信号未指定时工具可以自动创建。没有任务指定端口/usage 或实际门控失效证据时，缺一条显式 scan_enable 声明本身不是原始缺陷。显式声明可以是修复方案的一部分，但不能仅凭加上后成功反证原来有错。真实 ICG 的测试使能仍需结合其连接判断，不能把该自动行为推广为所有门控均已正确打开。

实测边界（可信度 C）：d29 全公开 T1/C2 的局部分区 scan-enable 配置全部符合，但未配置全局 ICG 控制，R1 留下 1,653 个 DFTR9，随后模型修复请求超时。自动创建端口不是“所有门控已有可控测试通路”的证明。生成这种真实 TE 被关断的门控设计时，应在默认分区/创建自定义分区前配置独立全局 clock_gating 或 all 使能，必要时采用工具支持的连接阶段；保留局部分区 usage=scan 的数据使能绑定，不将二者互相替代。
