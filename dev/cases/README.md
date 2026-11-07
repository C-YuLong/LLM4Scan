# 自建能力案例

这些是开发侧原创测试，不是官方评分集。没有参考答案、oracle 或正确脚本被挂载给智能体。
库仍需开发者自行取得；只复制到被 Git 忽略的 `dev/results/`。

## 初始八例与一例补充验证

| 分组 | 案例 | 功能 | 注入根因数 |
|---|---|---|---:|
| 开发 | d_clock_count | 双时钟分区、错误时钟选择器与错误链数 | 2 |
| 开发 | d_reset | 低有效异步复位、错误复位 inactive level | 1 |
| 开发 | d_clean | 双时钟正确脚本，无故障对照 | 0 |
| 开发 | d_generate | OR/AND 组合门控，从任务生成插链脚本 | 不适用 |
| 留出 | h_three | 三时钟、24 FF，时钟选择器与链数故障 | 2 |
| 留出 | h_reset | 12 FF、3 链，复位极性与链数组合故障 | 2 |
| 留出 | h_clean | 组合门控正确脚本，保留合法默认选项 | 0 |
| 留出 | h_generate | 两域 21 FF，不同分区大小与链长 | 不适用 |
| 补充 | h_prose | 不提供分区表，以中文描述 3 条链、每链上限 4、全局使能 | 不适用 |

这是初始 8 个配置/任务加 1 个纯文字补充任务，来自多时钟、异步复位、组合门控三类模板，
不是 9 个独立大型 IP。原始留出案例在开发前生成，冻结镜像后首次执行；初测发现门控误报，
用于后续修复后不再视为未见案例。h_prose 在 capability4 冻结后添加，使用已研究的中文改写形式，
验证其真实工具闭环，随后发现量纲分类错误并用于修复，所以也不是未见样本。
它们由同一作者/生成器构建，不是第三方隐藏集。

## 评分规则（先于留出执行固定）

- 产物：交付来源与引用正确；分区/时钟/使能/实际链长满足 oracle；CTL 包含全部原始 FF 且无重复；
  DRC 0；输出网表功能引脚和扫描使能保持正确；所有串联扫描连接与 CTL 相符。所有项满足才算该例通过。
- 诊断：按注入操作匹配原始脚本引用，一根因至多计一次 TP；重复或未匹配记录为 FP；未找到为 FN。
  `F1 = 2TP/(2TP+FP+FN)`；无故障且无误报时为 1。引用匹配不证明根因文字正确，最终需人工复核。
- 无故障任务必须无误报；其 F1 不纳入故障案例的微平均，避免用空任务稀释错误。
- 不把单元测试、未完成交付的中间产物计入真实工具通过率，不从多次尝试中只挑最好的一次作为批次成绩。

本阶段目标：最终开发配置全部通过，留出 4/4 产物通过、故障诊断微平均 F1 ≥ 0.9，
无故障对照无误报。不是官方评分公式。小型网表的直接引脚检查不是通用 LEC，也不覆盖任意复杂 Verilog。

## 运行

```powershell
python -X utf8 dev/challenge_suite.py prepare --suite my_capability_inputs
python -X utf8 dev/challenge_suite.py run --suite my_capability_inputs --split dev --prefix my_development
python -X utf8 dev/challenge_suite.py run --suite my_capability_inputs --split holdout --prefix my_holdout --image localhost/scanscan:capability6
```

生成器保存 `input/` 和与它并列的 `oracle.json`，后者包含正确脚本及注入信息；runner 只挂载 `input/`。
每次运行用新 prefix；评分写入 `dev/results/<prefix>_score.json`，每例原始输出完整保留。
`--cases` 可选一小组做针对性验证；`score` 操作只读已有运行并重算开发评分，不调用模型/Scan。

新合作者没有本地结果/库时，部分审计测试无法运行；不自动下载库，也不随 Git 分发。
当前生成器默认生成 9 例，holdout 选择器包含原 4 例及 h_prose。历史 capability_inputs01 只有 8 例，
对该目录使用历史的显式 --cases 列表；h_prose 本次单独生成于 capability_prose_input01。
较早双时钟探索案例见 [dual_clock](dual_clock/README.md)。
