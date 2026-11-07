# API 使用限制（用户最新指令）

- 用户最新明确授权切换到 DeepSeek Flash，使用本地 DeepSeek 官方 API，并确认 Key 在本地环境变量中。已核对 `DEEPSEEK_API_KEY` 和官方模型列表，当前 Flash 模型名为 `deepseek-flash`。可直接读取该变量并仅发送至 `https://api.deepseek.com`，不打印、不写入仓库，也不发送到其他服务。
- 此前 gproxy 授权继续有效：“调用 gproxy 的 api 是随便调用的，只要不用我的其他的 key 就行”。gproxy 测试仍使用 Luna；本轮新增的 DeepSeek 官方授权不扩大到其他密钥、配置文件或历史提交包中的凭据。
- 仅匹配 Codex 配置中名为 `gproxy` 的 provider；当前已核对为 `https://gproxy.lin.pub/codex/v1`、Responses 协议、凭据变量名 `CODEX_KEY`。可为已授权的调用读取该凭据，但不得打印、写入仓库或发送到其他服务；不得自行改用 `gproxy-deepseek`、`wza` 或其他 provider。
- 百炼及其他未授权服务的凭据仍禁止读取、复制、打包、上传或调用。gproxy 和 DeepSeek 官方的授权均不等于授权回退到百炼，不得跨服务发送 Key。
- 平台规则的只读查询已获本次任务授权。2026-10-01 重新查询赛题 38：当前公开打榜阶段明确禁止其他模型，且会忽略 `.env` 中自定义的 URL/model。因此未读取 gproxy Key、未调用模型、未提交；不能绕过平台限制上传 Luna 包。
- 本地测试无需逐次确认。`dev/run_local.py` 默认选择 gproxy/Luna，在读取 Key 前核对上述配置；显式 `--provider deepseek` 固定使用官方地址、`DEEPSEEK_API_KEY` 和 `deepseek-flash`，采用 Responses 协议、关闭推理，不读取 gproxy 配置或凭据。`--competition-api` 直接拒绝，没有服务回退。凭据打包和平台登录入口保持阻断；平台指定模型限制独立有效，不得用旧镜像或旧 ZIP 绕过。
- 可以继续本地代码开发、gproxy 测试、离线回放和不联网的检查。不要修改或删除全局环境变量、既有凭据及历史证据。
- 历史提交包和平台结果包可能含凭据，继续保密；已有成功调用和费用不会因本地停用而撤销。
