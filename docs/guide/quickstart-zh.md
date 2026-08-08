# Trading Partner 中文快速开始

本指南用于第一次安装核心 MCP，并把它接入 Codex 或其他支持本地 stdio 的
MCP 客户端。Trading Partner 是研究、事实和长期记忆服务，没有下单接口。

## 1. 安装

先安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，再执行：

```bash
uv tool install --python 3.13 \
  "git+https://github.com/KarlZhu-ZXC/trading-partner.git@v0.5.1"
trading-partner-init --json
```

初始化命令会：

- 创建当前用户专用的运行目录；
- 生成权限为 `0600` 的 `runtime.env`；
- 创建或幂等升级本地 SQLite 数据库；
- 输出 MCP 可执行文件和 `--env-file` 的绝对路径。

它不会请求 API Key、登录券商、同步持仓或访问行情。重复执行不会覆盖已经
存在的 `runtime.env`，只会把数据库升级到当前版本。

## 2. 接入 MCP 客户端

把初始化结果中的 `mcp_command` 与 `mcp_args` 填入客户端：

```json
{
  "mcpServers": {
    "trading-partner": {
      "command": "/绝对路径/trading-partner-mcp",
      "args": [
        "--env-file",
        "/绝对路径/runtime.env"
      ]
    }
  }
}
```

不要使用 `~`，不要复制其他电脑上的路径，也不要把 API Key 直接写入客户端
JSON。Claude Desktop、Cursor 以及源码目录模式的准确位置和重启方式见
[MCP 客户端配置指南](mcp-host-setup.md)。Codex 项目模式也可以采用同样的
command/args 进程契约。

Codex 的项目配置写法是：

```toml
[mcp_servers.trading-partner]
command = "/绝对路径/trading-partner-mcp"
args = ["--env-file", "/绝对路径/runtime.env"]
```

保存后完整重载对应客户端。修改配置文件或升级代码不会自动替换已经运行的
MCP 进程。

## 3. 验证连接

在新会话中输入：

> 调用 Trading Partner 的 `system_health`，告诉我 MCP surface profile、公开
> 工具数量、schema 版本、运行健康状态和 Data Quality 摘要。不要刷新任何
> Provider 或券商。

健康结果证明 MCP、数据库和工具 schema 已经可用，但不代表所有可选数据源
都配置完成。系统会把“仅完成配置检查”和“实际联网探测”分开显示。

第一个安全研究问题可以是：

> 解析 TSLA，获取最新可用行情，并列出数据源、观察时间、freshness 和所有
> warning。不要新建研究标的，不要刷新账户。

## 4. 可选数据源与账户

核心 MCP 启动不要求任何密钥。只有使用对应能力时，才在生成的
`runtime.env` 中增加配置：

- Alpha Vantage、FRED、SEC、百炼等属于可选 Provider；
- Schwab、Moomoo OpenD 和手动 CSV 属于可选账户来源；
- Telegram 属于可选通知出口；
- 未配置、限流、延迟或不可达必须以 warning 或 typed error 暴露，不能用其他
  数字静默替代。

券商刷新不是普通持仓问答的隐含步骤。只有显式调用
`external_state_sync(request={"operation":"accounts"})` 才会读取上游并持久化；
普通账户问题读取最近一次本地快照。

## 5. 研究标的与确认边界

Research Subject（研究标的/研究档案）保存长期研究对象或研究问题；Thesis
保存投资判断；Trade Plan 保存进入、退出、仓位和风险计划。客户端不能把交易
计划直接复制成研究标的标题。

新建研究标的、确认 Thesis 或 Trade Plan 等持久化动作都需要明确的用户授权。
Trading Partner 不提供真实订单、模拟订单或自动成交接口。

## 6. 升级与卸载

升级到新 Tag 后再次运行初始化，以应用随 wheel 发布的数据库迁移：

```bash
uv tool install --python 3.13 --force \
  "git+https://github.com/KarlZhu-ZXC/trading-partner.git@新版本Tag"
trading-partner-init
```

卸载程序不会删除数据库：

```bash
uv tool uninstall trading-partner
```

如需删除个人数据，先从 `trading-partner-init --json` 查看 `runtime_home`，完成
备份后再手工处理。不要在升级过程中自动删除这个目录。
