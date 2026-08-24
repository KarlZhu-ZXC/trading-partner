# MCP Host Decision-Loop Plan

状态：**已完成（PR1–PR6）。2026-08-20 已通过真实 FastMCP stdio 子进程完成
`system_health → investment_case_read/attention → next_read` 只读闭环；2026-08-17
记录中的既有 Grok 会话仍属于历史旧进程证据，不冒充已刷新。**
开始日期：2026-08-17
最近修订：2026-08-17
范围：让外部 stdio MCP 宿主（Grok、Cursor、Claude Desktop、Codex）获得
Console / Shared Agent 已具备的日常决策闭环，同时保持 27 个公开工具。

当前公开面仍是 27 个 `mcp_vnext_shadow` 工具。Console 已有 Review Queue、
Decision Workbench、ReviewItem materialization 和 Shared Agent 的结果压缩 / schema
修复提示。外部宿主只有公开 MCP 能力，不拥有 Console-only ReviewItem transition、
Agent Pending Action、OAuth、Telegram enqueue 或 SGOV scheduler 等私有入口。

本版根据代码审阅纠正四个关键问题：

1. Attention 读不得调用 `ReviewItemService.reconcile` 或产生任何持久化写入。
2. Attention 查询投影与 ReviewItem 持久化 materialization 是两个独立边界。
3. MCP 结果压缩必须覆盖真实 FastMCP stdio 出口，不能只修改 Registry `invoke()`。
4. transport validation 与 grouped closed-variant validation 分层处理，不假设所有错误
   都能进入 capability handler。

关联已完成且不得回退：

- `submitted_via=mcp_chat` 与兼容别名 `codex_chat` 具有相同的当前聊天确认门。
- ReviewItem、Pending Action、SGOV scheduler、Telegram Outbox 仍不是公开 MCP 写入口。
- 公开 MCP tool count 保持精确 27；grouped tool 继续只接收一个必填 `request` 对象。

明确不做：

- 不修改 Grok `permission_mode` 或 `mcp-host-setup.md` 的危险宿主告警。
- 不改 Data Quality 的日历 / 盘后陈旧判定。
- 不在本计划修复 US hourly bars 的 `DATA_CONTRACT_ERROR` 分类。

---

## 1. 产品目标

外部 MCP 宿主的一次日常会话必须能回答四件事，且不靠模型记忆散打工具：

1. 发生了什么：durable fact / durable state，不是模型推断。
2. 影响哪个 Research Subject、Monitor、账户、Agent action 或订单。
3. 用户下一步可以做什么：recommended action code，不是执行授权。
4. 哪个后续事实或明确动作能关闭该事项。

```text
Durable Provider fact / Monitor / Catalyst / workflow state
  -> read-only Attention projection
  -> human review in current chat
  -> existing Propose → Confirm gate（mcp_chat / codex_chat）
  -> durable result observation
  -> later Attention read observes closure
```

宿主负责理解和组织回答。Trading Partner 只提供 durable 投影、确认门和有界结果。
模型不得把空 Agenda、过期快照、partial coverage 或截断 payload 表述为“没有事项”。

## 2. 冻结边界

- 不增加第 28 个工具，不恢复 52-tool profile。
- 不合并 Propose / Confirm，不自动确认，不自动下单，不重试 `UNKNOWN`。
- 不公开 `tp_propose`、ReviewItem transition、Telegram enqueue 或 SGOV submit。
- 不把 Console `href` / route 作为外部宿主必经 UI。
- Application 不 import `interfaces.console`、`interfaces.agent` 或 MCP transport 类型。
- 不把模型输出、Agent 对话记忆或未确认 candidate 当成价格、持仓或成交事实。
- 不重命名兼容线名 `investment_case_*` / `case_id`；用户文案使用 Research Subject。
- `attention` 和 `system_health` 均不得联系 Provider、券商远端或主动刷新状态。

## 3. 关键架构决策

| 决策 | 选择 | 理由 |
|---|---|---|
| Attention 主入口 | `investment_case_read` 增加 `operation=attention` | 27-tool 冻结下的兼容折中；description 明示这是跨域日常决策恢复入口 |
| Attention 查询 | 即时、read-only durable projection | 保持 `investment_case_read` 的 read-only annotation，不让读取产生 materialization 写入 |
| ReviewItem | 作为 Attention 的一个 durable 来源 | 不把所有即时 projection 伪装成已有 ReviewItem；现有 ReviewItem 枚举 / ABI 不变 |
| ReviewItem reconcile | 保持独立 materialization 流程 | 继续由 Console / 内部调度 / 源状态闭环触发，不从公开 read tool 隐式调用 |
| Health 摘要 | 只读已 materialize 的 ReviewItem metrics + Data Quality limitation | 首次探针便宜、无写入；显式披露 materialized basis 和时间 |
| 投影代码 | Application typed query / projector | MCP、Console 可复用；不接受 Console `Request` 或 ToolEnvelope dict |
| 压缩代码 | `interfaces/shared/result_compaction.py` | Agent 与 MCP 共用 transport-neutral 纯函数；Agent 保留兼容 re-export |
| 压缩出口 | Registry HTTP 出口 + `bind_mcp` stdio wrapper | 真实外部宿主必须命中压缩，且不改变工具 schema / annotations |
| MCP JSON 预算 | 15 KiB；最终 TextContent 不超过 16 KiB | 为 FastMCP 内容包装保留余量，低于已观察到的约 20KB 宿主截断线 |
| schema 修复 | transport 与 closed-variant 两层错误 | 外层可能在 handler 前失败；内层才能稳定返回 `TOOL_INPUT_INVALID` envelope |

`investment_case_read/attention` 承担跨域 Attention 是 27-tool 冻结下的明确折中，
不是把 Broker、Agent 或 Data Quality 重新定义成 Research Subject。

## 4. 现状与缺口

### 4.1 已有能力

| 能力 | 现位置 | 外部 MCP 缺口 |
|---|---|---|
| ReviewItem list / metrics | `ReviewItemService` | 没有公开只读入口 |
| Agenda / Retro / Scorecard projection | Console `_workflow_attention_items` 等 | 只在 Console 组装，且使用 transport dict |
| Agent / Broker unresolved projection | Console `_operational_attention_items` | 只在 Console 可见 |
| Candidate / Monitor / Data Quality notices | Console 各自聚合 | 尚未纳入统一 Attention contract |
| operation compaction | Agent `compact_tool_result` | FastMCP stdio 仍返回原始大结果 |
| schema repair hints | Agent Runtime | 外部 MCP 主要收到 ValidationError / JSON-RPC error |

### 4.2 必须正确披露的 limitation

- `CATALYST_AGENDA_SYNC_RECEIPT_MISSING`：空 Agenda 不等于确认没有催化剂。
- account snapshot `degraded` / activity `INCOMPLETE`：只投影，不自动 sync。
- 近期 US `market_ohlcv` failure：引用现有 Data Quality issue，不扩大为“行情全挂”。
- 任一来源读取失败、分页不完整或 materialization 时间未知：返回 coverage / limitation，
  不把缺失来源解释为空集合。

### 4.3 宿主约束

- Grok 已观察到约 20,000 bytes 的工具结果截断；该数字是经验值，不是协议保证。
- compact schema inventory 会因新增 operation / description 合理滚动，但工具数保持 27。
- 结果预算按 UTF-8 最终 MCP TextContent 测量，不只测 Python dict。

## 5. Attention 查询契约

### 5.1 请求

```text
investment_case_read
  request.operation = "attention"
  request.case_id?   # 可选；只返回与该 Research Subject 直接关联的事项
  request.limit?     # 默认 25，最大 100
```

第一版不提供 `include_resolved`。历史 resolved ReviewItem 可继续由 Console 私有页面查看，
避免在日常宿主入口混合当前待办与历史闭环。

Subject scope 规则：

- 传 `case_id` 时只返回 `subject_id == case_id` 的事项。
- 没有 `subject_id` 的 global Broker / Agent / Data Quality 项不混入 subject scope；
  明确绑定该 Subject 的事项仍可返回。
- 全局调用返回所有 subject-scoped 与 global 项。

### 5.2 响应

```text
AttentionDigest
  generated_at
  mode = "durable_only_read"
  scope = "global" | "subject"
  subject_id?
  case_id?                 # 与 subject_id 同值的兼容字段
  total_count              # 应用 limit 前
  total_count_is_lower_bound # 任一来源 PARTIAL/UNAVAILABLE 时为 true
  returned_count
  truncated
  highest_severity?        # INFO | ATTENTION | ERROR
  limitations[]            # 去重后的 limitation code
  coverage[]
    source
    state                  # COMPLETE | PARTIAL | UNAVAILABLE
    observed_at?
    limitation_codes[]
  items[]
    key                    # 查询投影稳定 key
    tracking_kind          # REVIEW_ITEM | LIVE_PROJECTION
    review_item_id?        # 仅 REVIEW_ITEM
    source_type
    source_ref
    subject_id?
    title
    detail
    severity               # INFO | ATTENTION | ERROR
    recommended_action
    status                 # OPEN | ACKNOWLEDGED；LIVE_PROJECTION 固定 OPEN
    first_seen_at?
    last_seen_at?
    due_at?
    occurrence_count?
    closure_condition
      code
      description
    next_read?
      tool                  # 仅公开 read tool
      request               # 完整 closed request 对象
  metrics
    open_count
    acknowledged_count
    overdue_count
    unknown_execution_count
    by_source{}
```

计数 / 排序规则：

1. metrics、`total_count`、`highest_severity` 基于过滤后、limit 前的完整可见集合。
2. 排序为 `ERROR` → overdue → `ATTENTION` → `INFO`，再按 due / first-seen / key。
3. `truncated=true` 只表示 limit 截断；来源缺失通过 coverage / limitations 表达。
4. `next_read.request` 必须通过目标 operation 的 exact schema；不得返回模糊 `id`。
5. `next_read` 只能指向 read-only operation，不得建议 submit、cancel、sync 或 evaluate。

### 5.3 Attention source vocabulary

Attention 使用独立 query projection vocabulary，不修改现有
`ReviewItemSourceType` wire ABI：

| Attention source_type | 来源 | 推荐动作示例 |
|---|---|---|
| `RESEARCH_CANDIDATE` | durable pending candidate | `CONFIRM_OR_REJECT_CANDIDATE` |
| `CATALYST_AGENDA` | Agenda / existing ReviewItem | `LINK_OUTCOME_OR_REVISE` |
| `TRADE_RETRO` | Retro / existing ReviewItem | `REVIEW_RETRO` / `COMPLETE_RETRO_ACTION` |
| `SCORECARD_GAP` | Scorecard / existing ReviewItem | `REVIEW_SCORECARD_GAP` |
| `MONITOR_BLIND_SPOT` | durable Monitor dashboard / Data Quality | `INSPECT_MONITOR_RUN` |
| `BROKER_ORDER_INTENT` | unresolved durable intent / ReviewItem | `INSPECT_BROKER_STATUS` |
| `AGENT_PENDING_ACTION` | unresolved durable action / ReviewItem | `INSPECT_PENDING_ACTION` |
| `DATA_QUALITY` | durable Data Quality issue | 沿用现有 `recommended_action_code` |

如果一个 live projection 已有相同 `source_key` 的 OPEN / ACKNOWLEDGED ReviewItem，
查询层合并为一个 `tracking_kind=REVIEW_ITEM` 项，不重复展示。

### 5.4 Closure semantics

`recommended_action` 与 `closure_condition` 都不构成执行授权。至少锁定：

| source_type | closure_condition |
|---|---|
| `RESEARCH_CANDIDATE` | exact candidate 被 Confirm / Reject / Withdraw |
| `CATALYST_AGENDA` | outcome linked，或 item revise / cancel 后不再 overdue |
| `TRADE_RETRO` | review / action durable state 满足原 projection 条件 |
| `SCORECARD_GAP` | 后续 Scorecard 不再出现相同 gap，或现有 review 流程明确处置 |
| `MONITOR_BLIND_SPOT` | 后续成功 Run / observation 恢复 coverage |
| `BROKER_ORDER_INTENT` | 新 durable Broker observation 完成 reconciliation；UNKNOWN 不自动重试 |
| `AGENT_PENDING_ACTION` | pending action 得到 durable terminal result |
| `DATA_QUALITY` | 后续 durable fact / receipt 消除对应 issue |

不能公开关闭的事项不得声称可通过 ReviewItem transition 关闭。候选确认继续走现有
grouped write 和 `submitted_via=mcp_chat`；订单 reconciliation 不等于订单授权。

### 5.5 Read-only guarantee

`attention` 调用：

- 不调用 `ReviewItemService.reconcile` / repository write。
- 不更新 `last_seen_at`、occurrence、status 或 materialization timestamp。
- 不调用 Provider adapter、broker remote client、Watchlist sync、Monitor evaluate。
- 可以读取 durable repositories / Application query services。
- 任一来源失败只降低该来源 coverage，不清空其他来源，不关闭历史事项。

## 6. `system_health` 摘要

Health 不计算完整 live projection，也不调用 reconcile。只增加：

```text
data.attention_summary
  generated_at
  basis = "materialized_review_items"
  live_projections_not_included = true
  materialized_at?
  open_review_item_count
  acknowledged_review_item_count
  highest_severity?
  catalyst_sync_receipt_missing
  coverage_status          # COMPLETE | PARTIAL | UNKNOWN
```

语义：

- 计数仅代表已 materialize ReviewItems，不冒充完整 AttentionDigest。
- `live_projections_not_included` 固定为 `true`。COMPLETE + 0 不能表示
  “没有待办”，因为 Candidate / Monitor / DQ 等 live projection 不在该摘要里。
- ReviewItem repository 增加只读 `latest_observed_at()` 聚合（不新增 migration）；
  定义为全部 ReviewItem（含 RESOLVED）的 `max(last_seen_at)`。没有任何
  materialization 记录时 `materialized_at=null`、`coverage_status=UNKNOWN`。
- Catalyst receipt limitation 复用 Data Quality 已有 code / typed state。
- Attention summary 构建失败时基础 health 仍成功，并附加
  `ATTENTION_SUMMARY_UNAVAILABLE` limitation；不得伪造零计数。

## 7. Application 架构

```text
application/dto/attention.py
  └─ AttentionDigestDTO / AttentionItemDTO / CoverageDTO / ClosureConditionDTO

application/services/attention_projection.py
  └─ typed pure projectors；不接收 Request、ToolEnvelope 或 Console href

application/services/attention_query_service.py
  ├─ read ReviewItem list / metrics
  ├─ read pending candidates
  ├─ read Agenda / Retro / Scorecard durable DTOs
  ├─ read unresolved Agent / Broker durable records
  ├─ read Monitor dashboard / Data Quality durable DTOs
  ├─ merge by stable source key
  └─ sort / scope / limit / coverage

Console api.py
  ├─ materialization flow 继续显式调用 ReviewItem reconcile
  └─ 展示投影改为复用 typed projector / DTO serializer

MCP research adapter
  └─ attention -> AttentionQueryService.list_digest(...)
```

Application service 依赖 typed collaborators / ports，由 composition root 注入。不得把
`_durable_console_call`、FastAPI `Request`、MCP adapter 或 raw envelope dict 搬进 Application。

现有 ReviewItem source enum 保持不变。是否未来把 Candidate / Monitor / DQ materialize
为 ReviewItem 是独立产品决策，不是本计划的隐藏迁移。

## 8. MCP 结果压缩

### 8.1 共享实现

把纯 compaction 代码移到 `interfaces/shared/result_compaction.py`：

- Agent gateway 从 shared module 导入，并保留原 import path 兼容 re-export。
- MCP Registry / binding 从 shared module 导入。
- Application / Domain 不依赖该模块。

### 8.2 出口

同一个 postprocessor 必须覆盖：

1. `CompactCapabilityRegistry.invoke()` 的 HTTP / Console-compatible 返回。
2. `bind_mcp()` 注册给 FastMCP 的 wrapper 返回。

wrapper 必须保留原工具 input schema、exact validation、annotations、description、
confirmation policy 和 `technical_render_chart` ImageContent / artifact 行为。

不得只测 Registry Python 调用；必须通过 FastMCP `call_tool` / stdio-compatible 路径。

### 8.3 Envelope 保留底线

超过预算时，任意 ToolEnvelope 至少保留：

- `ok`, `request_id`, `as_of`, `fetched_at`, `freshness`, `degraded`
- bounded `warnings`, `errors`, `sources`
- `_truncated=true`
- `compaction="<capability>_<operation>_v1"`
- `size_bytes` 与对 secret-safe canonical projection 计算的 SHA-256

specialty compactor 可以保留更多 operation-specific data，但不能低于该底线。非 envelope
值才允许退化为 digest-only marker。

第一批 specialty：

- `monitor_read/dashboard|runs`
- `investment_case_read/context|attention`
- `research_workflow_run/deep_dive|catalyst_review|portfolio_review`
- `a_share_get_facts/financials|industry_cycle|company_operating_metrics`
- `us_company_get/filings|live_news|company_updates`
- 已有 `market_data_get/quotes`、`portfolio_analyze/exposure`、
  `research_memory_get/timeline|search|agenda`

不要把所有 operation 一次性塞进同一个 generic list trimmer；每个 specialty 用 fixture
锁定仍能回答的核心问题和 provenance。

### 8.4 字节验收

- canonical JSON 投影预算：15 KiB。
- 最终 MCP TextContent UTF-8：不超过 16 KiB。
- 测试记录原始、canonical、最终 content 三个 byte count。
- 图表 PNG / base64 不进入 JSON compaction；artifact markdown / 权限路径保持原行为。

## 9. Schema 修复提示

### 9.1 Transport 层

缺失必填 `request`、`request` 非对象，以及由公开 flattened model 提前拒绝的
缺失 `operation`，都可能在 FastMCP handler 前发生：

- 保持标准 MCP `isError` / invalid-params 语义。
- transport-level 测试确保没有 traceback、路径、exception repr 或 secret。
- 不承诺这类错误一定变成 ToolEnvelope。

### 9.2 Closed-variant 层

已通过公开 request model 并进入 grouped handler 后，未知 operation、跨 operation 字段
和 closed-variant 字段值错误返回：

```text
ok = false
errors[].code = TOOL_INPUT_INVALID
errors[].details = {
  tool,
  operation?,
  missing_fields[],
  unexpected_fields[],
  invalid_fields[{name, reason_code}]
}
```

要求：

- 字段名有界、脱敏、排序稳定；reason 使用 closed code，不返回 Pydantic 原文。
- 无 Python traceback / 文件路径 / request payload echo。
- validation 失败不得调用 Application service。
- `direct` 通道携带 `authorization_note` 等错误继续保留现有确认语义。

## 10. 描述与宿主用语

- `investment_case_read/manage` description 使用 Research Subject，并说明
  `investment_case_*` / `case_id` 是 legacy transport names。
- `attention` description 明示它是跨域、durable-only、read-only 的 decision inbox。
- AGENTS / skill：日常恢复先 `system_health`，再始终调用
  `investment_case_read/attention`。摘要不能代替 inbox。
- 仅在用户当前聊天明确决定后传 `reviewed_by=user`、`submitted_via=mcp_chat` 和
  有界 `authorization_note`；`codex_chat` 保持兼容。
- Attention / ReviewItem / Thesis 确认永不等于订单授权。

## 11. 分阶段实施

### P0 — 冻结 Attention contract 与 typed DTO

- 新增 Attention source / coverage / closure DTO 与枚举。
- 锁定 scope、排序、计数、dedupe、`next_read` exact request。
- 不改 MCP 行为。

验收：DTO / projector 单测；无 Infrastructure / Interfaces import。

### P1 — Application read-only projection

- 抽取 typed pure projectors。
- 实现 `AttentionQueryService`，读取 durable collaborators，不 reconcile。
- ReviewItem repository 增加只读 `latest_observed_at()` aggregate，供 Health 披露
  materialization freshness。
- Console 继续使用原 materialization gate，但投影展示复用 shared typed projector。

验收：

- Console Review Queue / Decision Workbench 行为不变。
- read-only query 前后数据库 row count / version / timestamp 完全不变。
- 单来源失败只产生该来源 limitation。
- 当前 Console source key / recommended action 等价测试。

### P2 — Attention MCP operation 与 Health summary

- `investment_case_read` 增加 `attention` closed variant。
- `system_health` 增加 materialized-basis summary。
- 更新 tool description、AGENTS、skill、capability boundary、unreleased notes。

验收：

- global / subject scope。
- Catalyst receipt missing limitation。
- Candidate / Monitor / DQ live projection 与 existing ReviewItem dedupe。
- 不调用 Provider / broker remote / sync / evaluate。
- 公开工具数仍为 27；记录 schema bytes 差值来源。

### P3 — Shared compactor 与真实 MCP 出口

- 抽 shared compactor并保持 Agent compatibility import。
- 同时接 Registry invoke 与 FastMCP binding wrapper。
- 分 operation 增加 specialty projection。

验收：

- Registry 与真实 MCP call 返回等价 compact envelope。
- final TextContent <= 16 KiB。
- warnings / errors / degraded / provenance 保留。
- chart ImageContent 行为不变。

### P4 — Schema repair

- closed-variant validation 映射 `TOOL_INPUT_INVALID`。
- transport validation 保持标准 MCP error 并验证脱敏。
- 锁 Application service 未被调用。

### P5 — 外部宿主只读烟测

对 Grok 或 Cursor：

1. `system_health` 读取 materialized summary / coverage。
2. `investment_case_read/attention` 读取当前事项与 limitation。
3. 对一个 Candidate 只读 `research_judgment_get`，不确认。
4. 调用大 dashboard / timeline，确认最终结果未被宿主截成半段。

烟测不下单、不 sync、不 evaluate、不执行 Candidate confirmation。

## 12. 测试与文档门

最低测试集：

- 新 `tests/unit/test_attention_projection.py`
- 新 `tests/unit/test_attention_query_service.py`
- `tests/unit/test_mcp_compact_surface.py`
- Console Review Queue / Decision Workbench 回归
- `tests/test_architecture_boundaries.py`
- `tests/integration/test_research_mcp_tools.py`
- FastMCP MCP-call transport 测试
- Agent compaction compatibility 测试

关键负面断言：

- Attention / Health 调用不改变数据库。
- 不调用任何 Provider adapter 或 broker remote client。
- partial / unavailable source 不产生假零值，不 auto-resolve ReviewItem。
- invalid request 不进入 Application。
- compressed envelope 不丢 warning/error code。

文档同步：

- `docs/guide/mcp-capability-boundary.md`
- `AGENTS.md`
- `.agents/skills/trading-partner/SKILL.md`
- `docs/releases/unreleased.md`
- 本文状态 / 量化验收收据

不修改历史 release notes，不删除 `codex_chat` 兼容值。

## 13. 完成定义

全部满足才算完成：

1. 公开 MCP 工具数精确为 27。
2. Attention 是严格 read-only；前后数据库无变化。
3. 一次 Attention 调用可返回 Candidate、Agenda、Retro、Scorecard、Monitor blind spot、
   unresolved Broker / Agent 和 Data Quality 的清单或明确 coverage limitation。
4. Health summary 明示 materialized basis / timestamp，不冒充完整 AttentionDigest。
5. MCP stdio 大结果保留 envelope / provenance，最终 TextContent <= 16 KiB。
6. closed-variant invalid request 返回 `TOOL_INPUT_INVALID`；transport error 脱敏。
7. Research Subject 用户文案正确，legacy wire names 保持兼容。
8. `mcp_chat` / `codex_chat`、订单 preview-submit、SGOV 例外均未放松。
9. Console Review Queue / Decision Workbench 行为不变。
10. 外部宿主只读烟测通过并记录最终字节数。

## 14. PR 划分

| PR | 标题 | 主要内容 | 依赖 |
|---|---|---|---|
| PR1 | Define typed read-only Attention contract | DTO、projection vocabulary、pure projector tests | 无 |
| PR2 | Build read-only Attention query service | typed collaborators、Console projection reuse、no-write tests | PR1 |
| PR3 | Add Attention MCP operation and Health summary | research/system adapter、compact registration、docs | PR2 |
| PR4 | Share result compaction across Agent and MCP transports | shared compactor、Registry + FastMCP wrapper、byte tests | 无；合入 PR3 后补 attention fixture |
| PR5 | Add closed-variant schema repair | validation mapper、transport redaction tests、description cleanup | PR3 |
| PR6 | Record external-host read-only smoke | operations verification note、final receipts | PR3–PR5 |

建议顺序：PR1 → PR2 → PR3；PR4 可与 PR1 / PR2 并行，但必须在 PR6 前与 PR3
集成。每个 PR 都必须保持现有工具数、确认门和独立测试绿色。

## 15. 相关但不在范围内

### 15.1 Catalyst sync 仍是显式 CLI

`uv run trading-partner-catalyst-sync sync --window-days 30` 才写 receipt。Attention
只读“从未同步” limitation，不新增 MCP sync 写入口。

### 15.2 US hourly bars `DATA_CONTRACT_ERROR`

独立后续工作：

1. 休市 / 空 hourly 序列标为 `NO_MARKET_DATA` 或 `CLOSED_SESSION`。
2. XNYS 关闭时跳过或降级美股 hourly 拉取。
3. 预期休市缺口不记为 `PROVIDER_ROUTE_FAILURES_RECENT`。

Monitor judgment 现有 hourly failure → daily fallback 行为保持。

### 15.3 Data Quality 盘后陈旧

Attention 只复制已有 issue / `recommended_action_code`，不发明“必须立即同步”。

## 16. 风险与回滚

| 风险 | 缓解 |
|---|---|
| Attention 查询过重 | 各来源 durable-only、并发有界；每源 coverage；Health 不跑完整 projection |
| 查询意外写库 | no-write repository spy + 数据库前后 snapshot 测试 |
| live projection 与 ReviewItem 重复 | stable source key 合并；ReviewItem 状态优先 |
| source enum 被误当 ReviewItem ABI | 使用独立 Attention vocabulary，现有 ReviewItem enum 不迁移 |
| MCP wrapper 改坏 schema / ImageContent | list_tools 精确 inventory + FastMCP call + chart artifact contract |
| 压缩丢反方证据 / warning | envelope floor + operation fixtures；generic fallback 也保留 codes |
| 宿主把 recommended action 当授权 | skill / description / closure contract 明示非授权 |

每个 PR 可独立 revert。公开工具始终为 27，不存在删除第 28 个工具的回滚路径。

## 17. 完成后的宿主用法

```text
1. system_health
2. 始终再调用 investment_case_read request.operation=attention
   Health 摘要只披露 materialized ReviewItem 基数 / freshness，
   不能代替 Attention inbox，也不能用 COMPLETE+0 跳过第 2 步。
3. 按 item.next_read 的 exact read request 获取细节
4. 仅当用户在当前聊天给出明确决定时，调用既有确认工具并传：
     reviewed_by=user
     submitted_via=mcp_chat
     authorization_note=<用户原话>
5. 不把 Attention、ReviewItem、Thesis 确认当成订单授权
```
