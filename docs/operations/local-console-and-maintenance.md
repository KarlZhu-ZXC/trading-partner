# 本地操作控制台与数据维护

## 控制台

控制台完全在本机运行，API 只允许绑定 `127.0.0.1` 或 `localhost`。它不调用 Codex/LLM，
但不是只读看板：用户可以主动运行到期 Monitor、账户/交易/自选同步、收盘后任务、通知、
备份和缓存清理，也可以从 MCP 工作台调用全部 28 个公开工具。它不会在页面加载时隐式
访问 Provider，也不提供订单能力。

终端一：

```bash
uv sync --extra console
uv run trading-partner-console
```

终端二：

```bash
cd console
npm ci
npm run dev
```

打开 `http://localhost:3000`（端口占用时以终端输出为准）。页面包括：总览、Monitor
定义/Run/事件、28 个 MCP 能力、持久化账户与自选、同步/OAuth/通知/数据库/保留策略。

Monitor 页面提供专用编辑器，不需要手写 MCP JSON：可按市场和代码/名称解析规范
`instrument_id`，选择按需、整点间隔或 A 股/美股收盘后 cadence，并添加多条价格、
组合风险或事实比较条件。事实比较覆盖价格、成交量、技术面、基本面、公司事件、宏观、
情绪、Thesis 状态和组合风险；页面会根据事实类型提示 `metric_key`，并在提交前检查
必需标的、阈值、事件比较方式和数据时效。编辑现有 Monitor 会创建新版本，不覆盖历史。
事件流显示真实 `event_type`、严重度、观测值和阈值；用户可在页面填写审计说明后执行
`ACKNOWLEDGE` 或 `RESOLVE`，两者仍通过 `monitor_manage` 的确认与幂等门。
每条 Monitor 规则在创建和更新时必须填写具体释义；规则卡片将释义与机器 `rule_code`、
方向/阈值、严重度、当前观测和状态一起展示。Monitor 卡片分别显示原始创建时间和最近运行时间。
总览及 Monitor 页的最近 Run 会显示本次 observation 实际记录的标的代码；只有 Run 版本
与当前 Monitor 版本一致时才附带当前名称，避免 Monitor 改版后把旧运行误标成新标的。

能力工作台按选定 operation 的 schema 预填必需字段，并把
`technical_render_chart` 返回的 PNG image block 直接显示在结果区。账户页只按原币种汇总
持仓市值和未实现损益，不把持仓市值描述成 NAV，也不隐式做 FX 合计。

外部访问和写入操作要求用户明确点击确认。MCP 工作台仍经过原工具 schema、候选确认、
actor gate、expected version 和 idempotency 校验；前端不能把“点击运行”伪装成 Thesis、
Trade Plan 或研究记录的确认。缓存删除另有二次确认，真实下单仍不存在。

Console 的 MCP 工作台与 Codex MCP 不是两套业务实现：两种 transport 都由同一份
compact-28 Capability Registry 提供 handler、请求 schema 和 effect policy。健康、账户、
自选及 Monitor 等一一对应的前端查询也通过 Registry 调用；`overview`、`monitors` 等路由
只负责把多项读取合并成适合页面的 BFF 响应。收盘任务、通知、备份和缓存维护仍是
Console/CLI 专用 operational capability，不会为了接口对称而扩入公开 MCP。

## 数据维护

查看状态和保留策略：

```bash
uv run trading-partner-maintenance status
```

创建 owner-only SQLite 在线备份：

```bash
uv run trading-partner-maintenance backup
```

缓存清理默认 dry-run，只有显式 `--apply` 才删除超过保留期的已过期 Provider/Reddit 缓存：

```bash
uv run trading-partner-maintenance prune-cache --retention-days 30
uv run trading-partner-maintenance prune-cache --retention-days 30 --apply
```

Monitor Run/observation/event、研究记录、交易和账户快照、QuantConnect 验证产物都不自动
删除。数据库备份也由使用者显式管理，避免静默丢失审计历史。
