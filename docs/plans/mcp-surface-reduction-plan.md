# Trading Partner — MCP 公共工具面 52 → 28 正式设计

> 状态：Completed；`compact_28` 是唯一运行时工具面，旧 52 工具兼容层已删除
> 日期：2026-07-26
> 历史基线：52 tools；输入 JSON Schema 合计 41,366 bytes；tool descriptions 合计 3,576 bytes
> 最终结果：28 tools（减少 46.2%），保持 application/domain/provider 语义和所有非执行边界

## 1. 决策摘要

采用 **compact v2 = 28 个工具**，不采用极端的 17-tool universal dispatcher。

缩减工具数量不是唯一目标。合并必须同时满足：

1. 属于同一用户任务或聚合；
2. 具有相同的副作用/授权等级；
3. 返回通道兼容（普通 Tool Envelope 与 image content 不混合）；
4. 能用 closed、discriminated request union 表达，不能退化成几十个可选字段；
5. 不隐藏 Provider 刷新、确认门、持久化或事件状态变化。

因此保留独立的确认、显式同步、Monitor evaluate 和 chart render 工具。这样比 17-tool
方案多 11 个入口，但 MCP host 可以按工具名区分 read / manage / sync 权限，模型也不需要在
一个超大 schema 中猜测不相关参数。

## 2. 为什么不是 17 个

17-tool 方案会把 read、confirmed write、upstream sync 和 state transition 混在同一工具内，
产生三个不可接受的问题：

- **授权折叠：** host 只想批准 Watchlist read 时，也会同时批准 add/remove；
- **schema 膨胀：** 当前 `a_share_get_facts` 已有 32 个参数，继续平铺会增加错误字段组合；
- **返回通道冲突：** `technical_render_chart` 返回 text + image blocks，不能与普通 JSON
  snapshot 合成一个稳定返回类型。

52 tools 原样保留也不合适：同一聚合的 CRUD、同一 Provider 的事实读取和五个 workflow
会争夺相近的 tool description，增加选择负担。28 是安全边界下的推荐平衡点。

## 3. Compact v2 精确库存

| # | Compact v2 tool | Closed operations | 由现有工具迁入 |
|---:|---|---|---|
| 1 | `system_health` | — | `system_health` |
| 2 | `instrument_resolve` | — | `instrument_resolve` |
| 3 | `investment_case_read` | `query`, `context` | `investment_case_query`, `research_context_build` |
| 4 | `investment_case_manage` | `create`, `archive` | `investment_case_create`, `investment_case_archive` |
| 5 | `research_judgment_get` | `state`, `thesis_history` | `research_state_get`, `thesis_history_get` |
| 6 | `research_judgment_propose` | `research_state`, `thesis_revision` | `research_state_update`, `thesis_revision_propose` |
| 7 | `research_judgment_confirm` | — | `thesis_revision_confirm` |
| 8 | `research_memory_get` | `search`, `report`, `timeline` | `research_search`, `research_report_get`, `research_timeline_get` |
| 9 | `research_memory_append` | `journal`, `decision` | `journal_append`, `decision_record_append` |
| 10 | `a_share_get_facts` | 现有 9 种 + `research_reports` | `a_share_get_facts`, `research_search_reports` |
| 11 | `market_data_get` | `quote`, `composite`, `bars`, `us_market`, `futures_curve`, `spot_future_basis` | `market_get_snapshot`, `market_get_bars`, `market_get_context` |
| 12 | `technical_get_snapshot` | — | `technical_get_snapshot` |
| 13 | `technical_render_chart` | — | `technical_render_chart` |
| 14 | `us_company_get` | `fundamentals_snapshot`, `fundamental_statements`, `filings`, `insider_activity`, `company_updates`, `events`, `live_news` | `us_get_fundamentals`, `us_get_company_research`, `market_get_live_news` |
| 15 | `us_context_get` | `macro`, `sentiment`, `prediction_market` | `us_get_macro_context`, `us_get_sentiment_snapshot`, `us_get_prediction_market_context` |
| 16 | `account_get` | `positions`（durable only） | `account_get(operation="positions")` |
| 17 | `external_state_sync` | `accounts`, `transactions`, `watchlist` | `account_get(refresh/transactions)`, `watchlist_get(refresh=true)` |
| 18 | `portfolio_analyze` | `exposure`, `simulate_addition` | `portfolio_analyze`, `portfolio_simulate_addition` |
| 19 | `challenge_review_get` | — | `challenge_review_get` |
| 20 | `challenge_review_manage` | `start`, `resolve` | `challenge_review_start`, `challenge_review_resolve` |
| 21 | `research_workflow_run` | `deep_dive`, `catalyst_review`, `a_share_market_review`, `us_market_review`, `portfolio_review`, `peer_comparison`, `historical_validation_prepare`, `historical_validation_import` | 六个 research workflow + 两个 Phase 3C manual bridge operation |
| 22 | `watchlist_get` | `groups`, `items`（durable only） | `watchlist_get` 的 durable read |
| 23 | `watchlist_manage` | `add`, `remove` | `watchlist_add`, `watchlist_remove` |
| 24 | `portfolio_risk_get` | `policy`, `check` | `risk_policy_get`, `risk_check` |
| 25 | `risk_policy_update` | — | `risk_policy_update` |
| 26 | `monitor_read` | `definitions`, `dashboard`, `runs`, `events` | Monitor query/dashboard/run/event adapters |
| 27 | `monitor_manage` | `create`, `update`, `resolve_event` | `monitor_create`, `monitor_update`, `monitor_event_resolve` |
| 28 | `monitor_evaluate` | — | `monitor_evaluate` |

这张映射覆盖全部 52 个旧工具。`account_get` 和 `watchlist_get` 是有意拆分旧工具内部的
operation：普通读取不再暗含 broker/OpenD 访问，所有显式上游刷新集中到
`external_state_sync`。

迁移期曾使用机器可校验的 ownership 映射冻结旧工具到 compact operation 的归属；兼容层
退役后该 fixture 与旧 public inventory 一并删除，本文表格保留为历史设计记录。

## 4. Schema 设计

### 4.1 禁止巨型 flat optional schema

合并工具使用一个 required `request` 参数，其类型是以 `operation` 为 discriminator 的 closed
Pydantic union。每个 variant `extra="forbid"`，只暴露该 operation 的有效字段。

```python
class MarketQuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["quote"]
    instrument_id: str
    as_of: datetime | None = None


class MarketBarsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["bars"]
    instrument_id: str
    start: datetime
    end: datetime
    interval: str = "1d"


MarketDataRequest = Annotated[
    MarketQuoteRequest | MarketBarsRequest | MarketContextRequest,
    Field(discriminator="operation"),
]


@server.tool(name="market_data_get")
async def market_data_get(request: MarketDataRequest) -> dict[str, Any]: ...
```

FastMCP 继续从 Pydantic `arg_model.model_json_schema()` 生成 schema。禁止用无类型
`dict[str, Any]` payload、开放字符串 operation、运行时猜字段，或把所有 variant 字段平铺到
一个函数签名。

### 4.2 Operation 命名

- operation 使用 lower_snake_case closed literals；
- 一个 operation 在一个 compact tool 中只有一个 owner；
- 旧 DTO 继续作为 application 输入，compact request 只做 interface-level adaptation；
- schema validation 失败仍是 JSON-RPC error，业务失败仍返回 `ok=false` Tool Envelope；
- response envelope、warning/error code、source/freshness/as_of 语义不变。

### 4.3 Schema 上限

- 单一 compact tool 最多 10 个 operations（`a_share_get_facts` 正好 10 个）；
- 单一 operation request 不超过 16 个业务字段；
- 单工具 input schema 不超过 8 KiB；
- compact `tools/list` 序列化大小不得高于 legacy 基线超过 5%；
- descriptions 必须列出 operation 与副作用，不复制整份用户指南。

## 5. 副作用与权限边界

| 等级 | 工具 | 要求 |
|---|---|---|
| Durable/provider read | `*_get`, `portfolio_analyze`, technical snapshot | 不改变投资判断、仓位或策略；Provider warning 必须保留 |
| Explicit upstream sync | `external_state_sync` | 只有用户明确要求刷新/同步时调用；不得由“当前持仓”自动触发 |
| Confirmed append/manage | case/judgment/memory/challenge/watchlist/risk-policy/monitor manage | 保留 confirmer、idempotency、expected-version 和 ActorContext mismatch gate |
| State evaluation | `monitor_evaluate` | 可持久化 rule state/event transition；仍 `execution_effect=false` |
| Local artifact | `technical_render_chart` | 单独保留 image content 返回和 permission-restricted artifact |

所有 compact tools 都声明 MCP `ToolAnnotations`：

- durable/provider reads：`readOnlyHint=true`；
- sync/manage/evaluate/artifact：`readOnlyHint=false`；
- archive/remove 类 manage tool：`destructiveHint=true`；
- 具有持久化 idempotency 的 manage tool：`idempotentHint=true`；
- 会访问外部 Provider 的工具：`openWorldHint=true`。

`instrument_resolve` 虽然用户语义是 lookup，但 local miss 会写 Instrument Master cache，因此
annotation 不得谎称完全 read-only。

## 6. Workflow 收口规则

`research_workflow_run` 合并研究 workflow 和 Phase 3C 手工验证桥接，但不允许借合并扩大权限：

- `deep_dive` 默认仍只复用唯一 Draft；创建新 Case 必须先通过
  `investment_case_manage(operation="create")`，compact workflow 不接受 `create_case=true`；
- `portfolio_review` 不接受 `refresh_accounts=true`；需要刷新时先显式调用
  `external_state_sync(operation="accounts")`；
- workflow 仍只返回 fact packages/receipts，Codex 负责 synthesis；
- workflow 不确认 Thesis、Trade Plan，不改变 Risk Policy/Monitor，不执行订单。
- historical-validation 只解析/落盘代码并导入用户下载的 JSON；不会调用付费 API、
  登录 QuantConnect 或在本地/远端启动回测。

这两处是有意消除隐藏写入/刷新，即使会在显式创建或同步场景多一个调用步骤。

## 7. 最终运行时策略

### 7.1 单一工具面

- 进程无 profile 配置开关，只注册 compact 28 inventory；
- 旧 profile、FastMCP registrar、public inventory 和迁移 fixture 已删除；
- 旧工具名返回普通 `method not found`，没有 runtime alias 或静默回退。

`system_health` 增加非敏感字段 `mcp_surface_profile`、`public_tool_count` 和
`surface_schema_version`，便于 Skill/host 检测不匹配。它不泄露配置或账户信息。

### 7.2 不做 runtime alias

Compact profile 收到旧工具名时返回普通 `method not found`。server 内不翻译旧名，Skill、
AGENTS、automation 和示例只使用 compact operation。旧 workflow 曾经隐藏的 Case 创建与账户
刷新必须继续拆成显式 compact 调用。

### 7.3 无数据库迁移

工具面缩减是 interfaces 层改造。除非另行批准新的 sync request idempotency receipt，本设计
不修改 domain/application schema，不新增 Alembic migration，不改变 Provider routing/cache。

## 8. 实施工作包

| 包 | 内容 | 退出条件 |
|---:|---|---|
| R1 | 冻结 legacy schema、operation mapping、ToolAnnotations | 52 个旧工具全部且只映射一次 |
| R2 | 新建 compact request unions 和 capability registrars | focused schema/validation tests 通过；未注册 compact |
| R3 | 增加 mutually-exclusive profile，compact opt-in | legacy=52、compact=28；两个 isolated wheel smoke 均通过 |
| R4 | 更新 Trading Partner Skill、AGENTS、docs、evals | compact 对话不再产生旧工具名 |
| R5 | 运行 selection/safety evaluation，切 compact 默认 | 达到第 9 节门槛 |
| R6 | 删除旧 profile、registrar、inventory、mapping 和专属测试 | 已完成；无 runtime alias 或 legacy public inventory dependency |
| R7 | 删除内部 handler registry、补齐 eval、共享重复 schema | 已完成；28 tools 直接持有 capability adapters，89 eval 覆盖全部工具，输入 schema ≤36 KiB |

Compact routing 只存在于 `interfaces/mcp/tools/`，不下沉到 domain/application；`server.py`
保持 lifecycle-only。Capability 模块导出普通 operation adapter factory，`compact.py` 直接持有
callable 并组装 28 个工具，不创建第二个 FastMCP、不读取私有 `ToolManager`，也不存在按旧工具
名查找的 HandlerRegistry。52 个业务 operation 仍是 closed union 的真实分支，但不再伪装成
52 个内部工具 handler。

公开 schema 最小化删除非验证性的 title/default、可由 `oneOf` literal 重建的 discriminator
mapping，缩短 `$defs` 名称并共享重复属性 schema；服务端 Pydantic 默认值和验证行为不变。
所有 `$ref` 必须在同一工具 schema 内可解析。`compact-v4` 输入 schema 合计 35,882 bytes，
门禁固定为 ≤36 KiB。

## 9. 验收门

### 9.1 静态与 contract

- compact `PUBLIC_TOOL_NAMES` 精确为 28；运行时不存在第二套 inventory；
- 每个 operation 有 schema golden、success/degraded/failure contract；
- 所有本地 `$ref` 可解析，28 个输入 schema 合计不超过 36 KiB；
- forbidden/retired/order surfaces 不可见；
- `technical_render_chart` 仍返回 text envelope + artifact text + PNG image；
- `external_state_sync` 以外的普通 account/watchlist read 不访问 broker/OpenD；
- 完整 Ruff、MyPy、Pytest、Alembic、isolated wheel 通过。

### 9.2 Agent selection evaluation

在覆盖研究、市场、账户、确认、同步、风险和 Monitoring 的固定对话集上：

- 89 个 declarative eval 必须覆盖全部 28 个公共工具；分组工具场景可声明预期 operation；

- compact tool + operation 首选准确率 ≥ 95%，且比 legacy 提升至少 5 个百分点；
- read-only 请求误选 manage/sync 的次数必须为 0；
- 首次 schema validation 成功率 ≥ 97%；
- 平均 tool calls 不高于 legacy；显式 create/sync 两步流程单独统计，不算回归；
- 不得出现自动 broker refresh、自动 Thesis/Trade Plan confirmation 或任何 order surface。

## 10. 明确不在本次范围

- 不把 MCP tools 改成一个 `dispatch(action, payload)`；
- 不把 durable report/case 改成 MCP Resource template（可作为后续 28→24 的独立实验）；
- 不改变 Investment Case、Thesis、Trade Plan、Risk、Monitor 业务模型；
- 不增加 backtest、orders、fills 或 runtime LLM；
- 不为减少数字而合并 JSON envelope 与 image block 返回通道。

## 11. 后续 28 → 24 的条件

只有当目标 host 已证明能稳定发现和读取 MCP Resource templates，才评估把以下纯读取入口迁移
为 resources：immutable report、case context、persisted challenge review、monitor event page。
这属于第二阶段协议设计，不是 compact v2 的验收条件。
