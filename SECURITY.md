# Security Policy

## Shared Agent Runtime

Shared Agent Runtime 默认关闭；自动工具调用只能通过受控 capability gateway 读取 Trading Partner 能力。模型、工具和
网页返回的文本均视为不可信数据；模型不能访问数据库、文件系统、券商或任意 HTTP 地址。
持久化 receipt 不保存 API key、Authorization/header、完整异常正文或无限制 Provider payload。
Telegram Agent 只接受配置中的数字 `TELEGRAM_CHAT_ID`；陌生 chat 静默忽略，缺少 token/chat
只禁用 Telegram Agent，不影响 Console、MCP、Monitor 或通知 Outbox。每个入站 `update_id`
按 channel 持久化，assistant marker 在发送前写入；重启重放会跳过模型和重复发送，因此
这是 at-most-once 边界（发送前崩溃可能漏发，不能声称 exactly-once）。
Console/Telegram 写入必须使用 exact arguments hash、principal、channel、expiry 和 CAS
状态门禁。Telegram callback 还要求 `callback_query.message.chat.id` 与 allowlist chat 匹配，
`callback_query.from.id` 与 durable `TELEGRAM_AGENT_USER_ID`（正数私聊默认 chat id）匹配；
群组未配置用户 allowlist 时 Telegram Agent 保持 unavailable。callback_data 仅携带
`c:<opaque-token>` 或 `r:<opaque-token>`，原始 token 不写入消息、receipt、日志；重复点击
只返回已处理，不会重复执行。确认 token 由 Agent-D gateway 一次性消费，模型仍不能直接
写入研究状态或执行订单。assistant marker 先于回答/动作卡发送落盘；崩溃可能漏发回复或卡片，
但重放不会再次调用模型或重放确认动作。

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting feature for this repository.
Do not open a public issue containing credentials, account identifiers, portfolio
data, OAuth tokens, exploit details, or other sensitive information.

Include the affected version, a minimal reproduction, expected impact, and any
suggested mitigation. Maintainers will acknowledge a complete report as soon as
practical and coordinate disclosure after a fix is available.

## Secret handling

- Never commit `.env`, broker exports, account databases, logs, or OAuth tokens.
- Static secrets belong only in the gitignored project-root `.env`.
- Provider-managed rotating tokens belong only in `data/secrets/` with owner-only
  permissions.
- Use `.env.example` for key names and safe, non-secret defaults.

If a credential may have been exposed, revoke or rotate it immediately. Removing
it from Git history is not a substitute for rotation.

## Supported versions

Until the first stable release, security fixes are made on the default branch only.
