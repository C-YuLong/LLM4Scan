# 原创双时钟能力案例

本目录不是官方案例，也没有复用官方网表或参考脚本。`input/netlist/bridge.v`
是手写的 12 个独立数据位寄存器：tick_a 驱动 a0–a7，tick_b 驱动 b0–b3。
库单元接口使用 SKY130；运行时需自行提供合法的 `sky130.lib`，库不进入仓库。

## 固定预期（仅开发侧可见）

- ingress：tick_a / shift_a，两条长度 4 的链。
- egress：tick_b / shift_b，一条长度 4 的链。
- 12 个原始实例恰好各入链一次，保持所属时钟域；DRC 0。
- Task 2 从 `repair.dofile` 开始，有两个注入操作：将 tick_b 声明替换成
  tick_wrong；将 ingress 链数从 2 改为 1。其他行不是故意注入的缺陷。
- 将“错误 tick_wrong”和“缺少 tick_b”拆为两个问题，按本实验的注入根因口径
  属于重复诊断；这不代表官方评分一定采用相同的合并口径。

Task 1 的输入目录不含 dofile。Task 2 仅额外提供故障脚本；本说明和审计脚本
不挂载到容器，模型看不到开发侧预期。两例共享同一个设计，不算两个独立设计。

## 准备与运行（仓库根目录，PowerShell）

使用新的目标目录，不覆盖已有实验。以下只复制库到被 Git 忽略的运行目录：

```powershell
Copy-Item -LiteralPath dev/cases/dual_clock/input -Destination dev/results/my_dual_input -Recurse
New-Item -ItemType Directory -Path dev/results/my_dual_input/lib
Copy-Item -LiteralPath official/extracted/public_cases/task_1/case4/input/lib/sky130.lib -Destination dev/results/my_dual_input/lib/sky130.lib
Copy-Item -LiteralPath dev/results/my_dual_input -Destination dev/results/my_repair_input -Recurse
Copy-Item -LiteralPath dev/cases/dual_clock/repair.dofile -Destination dev/results/my_repair_input/original.dofile
python -X utf8 dev/run_local.py --input-dir dev/results/my_dual_input --output my_dual_run
python -X utf8 dev/run_local.py --input-dir dev/results/my_repair_input --output my_repair_run
python -X utf8 dev/verify_challenge.py --output dev/results/my_dual_run
python -X utf8 dev/verify_challenge.py --output dev/results/my_repair_run
```

不带 `--image` 使用当前源码；带 `--image TAG` 使用冻结镜像。本阶段源码修复
尚未进入历史 `hardening2` 镜像。不要用老镜像验证新代码。

可单独审计未交付轮次，例如加 `--round R1`；通过只证明该轮满足此处的
报告/CTL 检查，不代表全流程交付或功能等价通过。诊断内容仍需对照上述注入操作人工评审。

## 无需工具的需求改写挑战

```powershell
python -X utf8 dev/verify_challenge.py
```

四组等义中文、共八条任务，使用合成的错误报告，检查约束识别及拒绝能力。
它输出测量结果，不因已知缺口返回失败码；不是八个真实电路，也不是模型能力测试。
目前四条改写均暴露漏检，保留为待解决能力问题，不扩展正则来迎合这些样本。
