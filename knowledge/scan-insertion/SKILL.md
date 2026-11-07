---
name: scan-insertion
description: Operate and diagnose the competition's Semitronix Scan Insertion flow using official rules and scoped experimental evidence. Use for scan scripts, DRC diagnosis, working-copy netlist repair and EQY evidence in this project.
---

# Scan Insertion 实作经验

先读 [比赛规则](references/rules.md)。当前 case 的任务、输入网表、Liberty 和实际报告优先于历史案例类比。不要读取该 case 的 golden/preset/oracle 来生成或修复参赛结果。

按问题选读，不一次加载所有历史：

精确命令或消息语义还可查运行镜像内的 `/opt/dftexp_scan/share/md/cmd/<command>.md` 与 `share/md/log/<message-id>.md`；这些是工具随附原文，不把项目历史猜测升级为官方说明。

- 时钟、门控、复位、初始化、DFTR1/3/8/9：[控制路径](references/control.md)。
- 漏诊、误诊、对象路径、证据引用：[诊断记录](references/diagnosis.md)。
- 必须改原电路、工作副本、功能等价：[网表修复与 EQY](references/equivalence.md)。
- CTL、黑盒、Wrapper、Segment、扫描覆盖：[扫描结构验收](references/structures.md)。

条目标记 A/B/C/D 是来源性质，不是可忽略任务要求的优先级。C 级结论只在记录范围内成立；D 级只能指导调查，不能作为修复已成功的依据。

仅修改授权任务范围。保留每轮输入/修改、工具完整日志及失败；不要把参考脚本执行计为模型通过。提交代码可改输出中的网表工作副本，但 Task 1 不得改网表；Task 2 必須有必要性、不得违背当前题目，并通过指定 EQY 证明原始与最终采用的 Pre-scan 等价。没有证明就不能交付为成功。

正式验收必须依据实际工具产物；模型意见、脚本退出码、单条报告都不能单独证明全任务完成。到当前题目的时限停止，保留尚未完成的真实状态。不要因这份技能自行提交平台、推送仓库或修改服务配置。
