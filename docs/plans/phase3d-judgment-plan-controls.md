# Phase 3D — 判断到计划控制链实施规格

> 状态：已完成（2026-07-26）
> 冻结日期：2026-07-26
> 产品边界：只产生版本化计划、确定性计算、风险检查和监控事件；不产生订单、成交、持仓写入或确认权限。
>
> **工具面历史口径：**本文中的“52 个公共工具”及旧工具名记录 Phase 3D
> 实施时的验收基线。后续 MCP surface reduction 已将能力映射到唯一的
> `compact_28` 运行时工具面；历史记录不代表当前可调用工具。

## 1. 目标

Phase 3D 把当前研究判断变成一条可持续复核的确定性控制链：

```text
Investment Case + confirmed Thesis + verified facts
→ versioned Trade Plan
→ deterministic Position Sizing range
→ Risk Engine v2 checks
→ compiled Monitoring v2 rules
→ durable transition events and plan-review state
```

Trade Plan 是研究计划，不是订单。Position Sizing 是计算区间，不是仓位指令。
Monitor 只记录状态转换，不直接修改 Thesis、Trade Plan、Risk Policy、账户或券商状态。

## 2. 公共 MCP 边界

公共工具数保持 **52**，不增加同义工具：

- `research_judgment_propose(request={"operation":"research_state","kind":"trade_plan",...})` 接受候选计划；仍走 Candidate
  Propose → Confirm / Reject / Withdraw；Codex 不得自主决定结果，但必须转交用户在当前
  聊天中对明确候选作出的确认或拒绝；
- `thesis_revision_confirm` 复用既有候选生命周期，用户或获授权外部 Agent 确认后
  才追加 Trade Plan 版本；
- `research_judgment_get(request={"operation":"state",...})` 返回当前 Trade Plan 与版本历史；
- `risk_check` 可按已确认 `trade_plan_id` 计算 Position Sizing，并把假设增加后的
  Risk v2 结果一并返回；
- `monitor_create` / `monitor_update` 可关联一个 Trade Plan 版本，并可从其中的
  machine-evaluable conditions 确定性编译规则；
- `monitor_evaluate` 扩展事实类型，继续只在状态转换时写事件。

## 3. Trade Plan

### 3.1 版本和状态

一个 Investment Case 最多有一个 current Trade Plan identity；所有确认修改均追加版本。
状态为 `DRAFT`、`ACTIVE`、`PAUSED` 或 `ARCHIVED`。确认新版本使用乐观版本号；相同
idempotency key 只返回原结果，不产生重复版本。

每个版本固定记录：

- `plan_id`、`version`、`case_id`、`thesis_id`、`instrument_id`；
- `status`、`valid_from`、可选 `valid_until`；
- `currency`、参考价格和价格时间；
- `target_position_percent`、`max_position_percent`、`risk_budget_percent`；
- 可选 `stop_price`、计划说明和结构化 conditions；
- `confirmed_by`、`created_at`、`idempotency_key`、schema version。

ACTIVE 计划必须关联一个当前、已确认且未归档的 Thesis；公司/标的计划必须有
Instrument。计划的时间、金额和比例使用时区感知时间与 Decimal。

### 3.2 条件

条件阶段为 `ENTRY`、`SCALE`、`EXIT`、`INVALIDATION` 或 `REVIEW`。支持两类：

1. `MANUAL`：保留为明确的人类检查项，不伪装成可自动评估；
2. `MONITORABLE`：包含 closed fact type、metric、comparator、阈值、事实最大年龄和
   可选事件观察起点，可确定性编译为 Monitor rule。

条件事实类型覆盖：

| 能力 | Fact type | 当前事实来源 |
|---|---|---|
| 价格 | `PRICE` | A-share/US/CME/OTC quote |
| 成交量 | `VOLUME` | provider-backed latest daily bar |
| 技术指标 | `TECHNICAL` | Technical Engine v2 disclosed metrics |
| 财务 | `FUNDAMENTAL` | A-share normalized statements / SEC→yfinance→AV |
| SEC/公告/公司事件 | `COMPANY_EVENT` | US filings/insider/company updates；A-share official/report facts where supported |
| 宏观 | `MACRO` | FRED vintage-safe series |
| 情绪/资金关注 | `SENTIMENT` | source-separated US sentiment or A-share deterministic heat/rank facts |
| 当前判断 | `THESIS_STATE` | durable confirmed Thesis and invalidation state |
| 组合风险 | `PORTFOLIO_RISK` | Risk Engine result |

Provider 不可用、字段缺失、事件源不支持或事实过期时必须返回 `NOT_EVALUATED`。

## 4. Position Sizing

V1 sizing 输出是确定性范围，至少同时给出以下上限：

- 计划最大仓位上限；
- 风险预算 / `abs(reference_price - stop_price)` 上限；
- Risk Policy 单一持仓上限；
- 可用账户 NAV / 现金上限；
- A 股 100 股整数倍与 T+1 披露；美股可支持碎股；
- 可获得成交额时的流动性参与率上限；
- ATR 或目标波动率法的可选上限。

最终 `recommended_min_quantity` / `recommended_max_quantity` 只在所有必需输入完整、
同币种且参考价格未过期时返回。缺少 NAV、现金、FX、价格时间、stop 或波动率时，相关
方法为 `NOT_EVALUATED`，不能用零或模型猜测替代。输出固定
`execution_effect=false`、`historically_validated=false`。

## 5. Risk Engine v2

在 v1 的账户年龄、价格年龄、单一持仓、gross/NAV、现金、margin 和跨账户重复持仓上，
追加：

- 计划风险金额与风险预算；
- 主题/行业集中度（只有 Instrument metadata 可验证时评估）；
- 组合及单标的 drawdown（只有可追溯历史和高水位时评估）；
- 流动性参与率；
- 财报/公告等事件窗口；
- A 股 T+1、涨跌停、停牌和 100 股整数倍；
- stale facts、相关持仓和重复计划/重复订单意图检查。

Trading Partner 不拥有订单，因此“重复订单”只能检查 durable Trade Plan / Decision intent；
不能声称检查了券商未成交订单。任何缺失事实保持 `NOT_EVALUATED`，overall 为
`INCOMPLETE`。

## 6. Monitoring v2

Monitoring v2 复用既有 append-only Monitor identity/version/state/event 模型：

- 增加 Trade Plan identity/version 关联；
- rule 保存通用 fact type、metric、comparator、阈值与事件观察窗口；
- 同一 run 内按事实请求键缓存，避免重复 Provider 请求；
- `TRIGGERED`、`RECOVERED`、`NOT_EVALUATED` 仍只在状态转换时持久化；
- closed-session latest-known 可以按交易日语义使用，但真实 observation time 必须保留；
- `valid_until` 仍是报警生命周期，不是事实 freshness；无截止时间表示持续追踪。

计划更新不会静默改写已有 Monitor。只有显式创建/更新 Monitor 时才编译指定 Trade Plan
版本，保证历史事件可追溯到原计划版本。

## 7. 持久化和审计

- 新表使用 append-only identity/version/condition 结构；
- Trade Plan 确认和 Candidate 状态变更在同一 Research Unit of Work 中提交；
- 所有 id、版本、case/thesis/instrument 外键和 idempotency 冲突均为 typed error；
- 原始 Provider payload、账户号、token、API key 和券商订单不得进入计划或事件表；
- migration downgrade 必须精确删除 Phase 3D 新对象，不影响 Phase 1–3B 数据。

## 8. 完成定义

Phase 3D 只有在下列证据全部成立时才完成：

- [x] Trade Plan 可提出、确认、查询、追加版本、暂停和归档；Codex 不得自主确认，用户的
  明确聊天授权可由 Codex 原样转交并审计；
- [x] ACTIVE 计划的 case/thesis/instrument/version/时间和比例不变量由领域层强制；
- [x] `portfolio_risk_get(request={"operation":"check","trade_plan_id":...})` 返回确定性 sizing 与 Risk v2，缺失输入不假装通过；
- [x] A 股和美股至少各有一个 sizing 验收，覆盖股数规则和 stale/missing facts；
- [x] Trade Plan 条件可编译成关联版本的 Monitor v2 rules；MANUAL 条件显式跳过；
- [x] 价格、成交量、技术、财务、公司事件、宏观、情绪、Thesis、组合风险类别均有
      成功或 typed `NOT_EVALUATED` 的确定性 resolver 验收；
- [x] Monitor 重复运行不重复报警，恢复/不可评估转换正确，过期计划/监控不访问 Provider；
- [x] Risk/Monitor/Plan 所有结果 `execution_effect=false`，不存在订单或持仓写接口；
- [x] 公共 MCP 工具仍精确为 52，schema/eval/skill/能力边界文档同步；
- [x] Alembic upgrade、Ruff、mypy、pytest 和 wheel smoke 全部通过。

## 9. 交付验证记录

2026-07-26 完成交付审计并在全项目瘦身后复验：Alembic
`upgrade head → downgrade -1 → upgrade head` 往返通过；Ruff 通过；mypy 对 396 个源码
文件通过；pytest 全量 1,819 项通过；隔离 wheel 安装/启动烟测通过；公共 MCP 工具库存
测试确认仍为 52。测试数下降来自删除已退役 mock snapshot 子系统的重复专用测试，不是
缩减 Phase 3D 验收覆盖。
