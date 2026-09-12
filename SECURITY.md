# Security Policy

## Shared Agent Runtime

Shared Agent Runtime 默认关闭；自动工具调用只能通过受控 capability gateway 读取 Trading Partner 能力。模型、工具和
网页返回的文本均视为不可信数据；模型不能访问数据库、文件系统、券商或任意 HTTP 地址。
持久化 receipt 不保存 API key、Authorization/header、完整异常正文或无限制 Provider payload。
Console 是唯一内置 Agent 入口；Telegram 只发送通知，不接受聊天、回调确认或会话转接。
Console 写入必须使用 exact arguments hash、principal、channel、expiry 和 CAS 状态门禁。
确认 token 由 Agent-D gateway 一次性消费，原始 token 不写入消息、receipt 或日志；模型
不能自行确认研究状态或执行订单。历史 Telegram channel 值、游标和转接表仅用于数据库
兼容，不提供可调用的 Agent 入口。

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
