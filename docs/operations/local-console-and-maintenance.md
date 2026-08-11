# 本地操作控制台与数据维护

## 控制台

控制台完全在本机运行，API 只允许绑定 `127.0.0.1` 或 `localhost`。它不调用 Codex/LLM，
但不是只读看板：用户可以主动运行到期 Monitor、账户/交易同步、收盘后任务、通知、
备份和缓存清理，也可以从 MCP 工作台调用全部 27 个公开工具。它不会在页面加载时隐式
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

打开 `http://localhost:3000`。浏览器前端会自动取得当前 Console 进程的短期会话令牌，
正常页面操作不增加登录、复制令牌或二次配置步骤；API 进程重启后，前端也会自动刷新令牌。
为保持本机授权边界，前端 Origin 固定为 `http://localhost:3000` 或
`http://127.0.0.1:3000`，不要接受开发服务器自动换用其他端口。直接编写脚本调用写接口时，
需先读取 `GET /api/session`，再把返回的令牌放入
`X-Trading-Partner-Console-Token` 请求头；只读 GET 不需要该请求头。

页面包括：总览、全部
研究档案/Thesis、Judgment Scorecard、Catalyst Agenda、Trade Retro、Monitor 定义/Run/事件、27 个 MCP 能力、持久化账户、
同步/OAuth/通知/数据库/保留策略。

Research 页面使用研究标的索引和单个研究档案工作区：默认包含已归档研究档案，并展示所选研究标的的
Thesis、当前版本、假设、失效条件、开放问题、Trade Plan 与待审候选。读取只通过现有
`investment_case_read/query` 与 `research_judgment_get/state` 聚合，不会请求行情 Provider。
用户可以创建、编辑或归档研究档案；研究档案编辑只修改标题、摘要、标签和关联研究档案。Thesis
修改始终先产生候选，再由用户显式确认或拒绝，不能覆盖已确认 revision。单个研究档案的研究
状态读取失败时，该研究档案仍保留并显示局部错误，不会让其他研究档案从页面消失。

Portfolio 页面使用 Holdings、Activity、Performance、Risk 四个稳定标签页。初始加载只聚合
持久化账户快照、交易、暴露、覆盖回执和风险状态，不访问券商或其他上游。账户和交易各有
独立的显式同步按钮；同步失败只影响对应区域。Holdings
按账户展示原币种现金、净资产、购买力、融资、快照时点、警告和持仓；Activity 保留交易与
覆盖缺口；Performance 只做可追溯的 FIFO/券商成本口径计算；Risk 支持当前政策、确定性检查、
手工假设新增或已确认 Trade Plan 试算。Watchlist 暂不在 Portfolio 前端展示，相关 MCP 能力
仍可在 Capabilities 工作台中使用。
所有写入继续经过 compact Registry 的 expected-version、confirmation 与 idempotency 校验，
页面不提供订单、隐含 FX 汇总或后台自动刷新。

Trade Retro 页面读取不可变的历史运行，不会刷新券商。用户应在周期开始前点击
`Prepare next week` 固化当前 Trade Plan 和 Decision Record；周期结束后点击
`Run previous week`，用持久化成交与覆盖回执做确定性纪律审计。可选模型只叙述已计算
Finding，失败不会丢失确定性结果。每个 Run 可展开查看完整摘要、Finding 和交易引用；
`Review` / `Edit review` 会追加一个人工复核版本，可修改整体复核状态、纠正说明、行动项和
逐 Finding 结论。保存需要显式确认，并以 `expected_version` 拒绝陈旧页面覆盖。原始 Run、
模型摘要和 Finding 始终不可改。`Export to Obsidian` 只替换配置目录中周记的 Trading Partner
marker block，并包含最新人工复核，不覆盖手写正文。命令行等价入口为：

```bash
uv run trading-partner-retro prepare \
  --start 2026-08-10 --end 2026-08-17 \
  --idempotency-key retro-plan-2026-w33
uv run trading-partner-retro run \
  --start 2026-08-10 --end 2026-08-17 \
  --idempotency-key retro-run-2026-w33 --export-obsidian
uv run trading-partner-retro history
uv run trading-partner-retro weekly --export-obsidian
```

如果不传日期，CLI 使用上一完整 UTC ISO 周。Obsidian 导出需要配置
`RETRO_OBSIDIAN_JOURNAL_DIR`；不开启 `TRADE_RETRO_LLM_ENABLED` 或模型不可用时，
确定性中文报告仍然可用。面向现有周六定时任务，`weekly` 使用固定的周一 00:00 UTC
至周六 00:00 UTC 窗口，完成审计/可选导出后再固化下一周同口径快照；Automation
不再自行解析周记或重建交易纪律结论。

Catalyst Agenda 页面只在用户点击时运行免费 Provider 同步；普通加载读取持久化 scope、
coverage、事项版本和 sync receipt，不刷新行情、账户或 Watchlist。用户可创建、修订、取消
事项，或把已发生事项链接到同研究范围的 Event/Report/Evidence；每次写入要求确认、幂等键
和 expected version。结果表单可从 durable timeline/search 选择候选事实，也可直接输入 ID；
OCCURRED 结果的补充或纠正会追加新 version。Yahoo/yfinance 日期是 current-only，FRED release date 不保证精确发布
时刻，失败和日期漂移不会被解释为“无催化剂”。页面可预览或入队一条移动端 Agenda
Telegram 摘要，发送仍复用 generic durable Outbox。

CLI 同样可在一次显式同步后入队并尝试发送摘要：

```bash
uv run trading-partner-catalyst-sync sync --window-days 30 --notify --flush
```

Judgment Scorecard 页面选择 Research Subject 与明确 Thesis 后生成不可变校准 run，并浏览
历史。S1 展示 revision 定义、evidence、失效条件、Trade Plan/Monitor、行动时序、Trade
Retro 和 Catalyst outcome 九个维度；它不生成总分，不回写 Thesis/Plan，也不调用 Provider
或 LLM。历史 run 按其原 algorithm contract 原样读取，不用当前事实重算过去。

已有历史周记可做一次性迁移：

```bash
uv run trading-partner-retro import-markdown \
  --path /absolute/path/to/WeekNN.md \
  --start YYYY-MM-DD --end YYYY-MM-DD \
  --idempotency-key legacy-retro-YYYY-wNN
```

该命令只提取 `## 2. Retro` 到下一个二级标题之间的原文，保存为明确标注的
`trade-retro-legacy-markdown-import-v1` 不可变 Run。它不会把旧文字伪造成结构化 Finding，
也不会宣称重新验证了成交覆盖；后续修订仍通过 Console 的 append-only Review 完成。

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
失败 observation 的详情会直接展示结构化 Provider 诊断链：Provider、请求阶段、typed error、
HTTP 状态、attempt 和 retryability。诊断是为定位“解析失败、主源失败还是 fallback 失败”而
设计的，不保存 URL、代理地址、请求头、响应正文或异常原文。迁移 `0036` 之前的 Run 没有
诊断 sidecar 时，页面明确显示缺失，不能从顶层 warning 猜测具体失败环节。

能力工作台按选定 operation 的 schema 预填必需字段，并把
`technical_render_chart` 返回的 PNG image block 直接显示在结果区。账户页只按原币种汇总
持仓市值和未实现损益，不把持仓市值描述成 NAV，也不隐式做 FX 合计。

外部访问和写入操作要求用户明确点击确认。MCP 工作台仍经过原工具 schema、候选确认、
actor gate、expected version 和 idempotency 校验；前端不能把“点击运行”伪装成 Thesis、
Trade Plan 或研究记录的确认。缓存删除另有二次确认；Console 仍不提供真实下单，
确认门禁的 Schwab 下单只存在于 `broker_order_manage` MCP。

Console 的 MCP 工作台与 Codex MCP 不是两套业务实现：两种 transport 都由同一份
27-tool Capability Registry 提供 handler、请求 schema 和 effect policy。健康、账户、
自选、Research 及 Monitor 等一一对应的前端查询也通过 Registry 调用；`overview`、
`research`、`monitors` 等路由
只负责把多项读取合并成适合页面的 BFF 响应。收盘任务、通知、备份和缓存维护仍是
Console/CLI 专用 operational capability，不会为了接口对称而扩入公开 MCP。

通知同样保持在 operational CLI 边界：`trading-partner-notifications` 提供
`status`、`test`、`flush`，以及从 stdin 读取 UTF-8 正文的显式授权
`enqueue`；旧的 `trading-partner-monitor-notifications` 仍是别名。MANUAL
enqueue 必须带 `title`、幂等键、`user`/`external_agent` 确认者和授权说明，
JSON 回执不会回显正文或授权说明，也不会产生订单或其他交易状态效果。
内部确定性生产者使用封闭的 `SYSTEM` source；`MANUAL` 仅用于显式授权的调用者写入。

Schwab SGOV Shadow 计划也属于 operational capability，不增加 MCP 工具：

```bash
# 立即刷新 Schwab 并在终端显示所有账户的购买计划表（不通知）
uv run trading-partner-sgov-plan preview

# 安装/检查每天的 token-free launchd 调度
uv run trading-partner-sgov-plan-scheduler install
uv run trading-partner-sgov-plan-scheduler status
```

普通交易日的实际到期时间是 15:45 America/New_York；官方提前收盘日使用收盘前 15 分钟。
launchd 每小时 `:45` 只做一次本地到期判断，休市日和其他时段不访问 Schwab。到期后只刷新
Schwab、读取 SGOV bid/ask、按每账户 `$2,000 + $200 + active BUY reserve` 计算整股计划，
并通过 SYSTEM Outbox 发送一条移动端纵向摘要。该流程不会调用 Codex/LLM，也没有下单方法。

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
