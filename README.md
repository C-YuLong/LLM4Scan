# LLM4Scan

面向 Scan Insertion 任务的模型驱动脚本生成、执行、核验与反馈修复系统。`development_zwy` 分支新增 `Ourmethod/` 核心实现；原有实现保留作参考。

## 仓库结构

```text
Ourmethod/                  当前独立实现
  agent_system              命令行入口
  scan_agent.py             生成、执行、核验与修复主循环
  generator/                Task 1 生成与 Task 2 修复提示及上下文
  verifier/                 证据工具、核验控制器和结构化检查
    structural/             网表、库、报告解析及 FF/ICG 检查
  configuration.py          组件、模型和运行配额配置
  config*.toml              运行配置示例（不含凭据）
  Dockerfile                基于 Scan 环境构建独立运行镜像
submission/                 原有 Agent 实现
Dockerfile                  原有实现的镜像入口
CONTRIBUTING.md             原有开发说明
```

`dev/` 等历史文件沿用远端主分支；本次提交不添加实验脚本、测试数据或运行记录。官方案例、模型凭据、许可证和镜像需自行准备。

## 当前状态

- `Ourmethod` 包含 generator → Scan → Verify → 反馈修复流程；默认配置仍选 `legacy` 核验器。
- Task 1 可选 `direct_agent` 或 `guided_agent` 模型核验；guided 将要求分配给独立上下文 worker，默认并发 3，并保存子任务状态、事件和证据。
- guided 规划只填写要求 ID 和描述，检查范围由具体工具参数表达。结构化工具提供 FF 映射、ICG 重连、连接与扫描结构等检查；证据不足保留 UNKNOWN。
- 这是开发中的核心代码快照。已有局部核验通过，但完整任务仍存在请求异常、调用配额和收尾问题，不代表全案例验收完成。

运行依赖和配置方式见 [Ourmethod/README.md](Ourmethod/README.md)。本分支上传范围不包含实验及测试目录，无法直接复现本地历史实验。
