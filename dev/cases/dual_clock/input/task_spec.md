# 双时钟插链任务

读取 `lib/sky130.lib` 与 `netlist/bridge.v`，顶层为 `bridge`。
将全部 12 个 sky130_fd_sc_hd__dfxtp_1 替换成 sky130_fd_sc_hd__sdfxtp_1 并插链。
不得修改输入网表，不得排除寄存器，不得忽略或降级 DRC 规则。
顶层时钟端口 `tick_a`、`tick_b` 均配置为时钟信号，off_state 为 0。
不得跨时钟域，不允许不同时钟边沿混合。DRC 检查无违例。
以下分区分别只包含对应时钟域的寄存器；创建新的扫描使能端口，低电平不使能。

| 分区 | 时钟 | chain_count | max_length | scan_enable |
|---|---|---|---|---|
| ingress | tick_a | 2 | 4 | shift_a |
| egress | tick_b | 1 | 4 | shift_b |

## 输出要求

- 插链后网表 `post_scan.v` 和 CTL 文件 `post_scan.ctl`。
- DRC 报告 `drc.rpt`。
- 扫描链报告 `scan_chain.rpt`，扫描链上单元明细报告 `scan_cell.rpt`。
- 插链配置报告 `scan_cfg.rpt`、插链信号报告 `scan_signal.rpt`。
- 插链分区报告 `scan_partition.rpt`，插链统计报告 `insertion_info.rpt`。
