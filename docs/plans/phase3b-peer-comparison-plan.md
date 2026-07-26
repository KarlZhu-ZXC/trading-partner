# Phase 3B-T02 — 显式同行比较事实包实施规划

> 状态：Implemented（2026-07-26）
> 产品定位：调用方明确指定同行，Trading Partner 负责取得、规范化和对齐事实；Codex 负责解释
> 公共面约束：继续保持唯一的 `compact_28`，不新增 MCP 工具
> 数据源约束：只复用现有免费 Provider，不新增 key、数据库表或后台同步任务

## 1. 结论

3B-T02 应作为现有 `research_workflow_run` 的第六个 operation：
`peer_comparison`。

它解决的不是“寻找同行”，而是用户已经指定同行后，系统仍需要逐家公司调用、处理不同
财报期间、对齐指标名称、识别币种和累计口径、汇总缺失字段的问题。

核心边界：

- 支持 A 股和美股公司，但单次请求中的公司必须属于同一市场；
- 主公司 1 家，同行 1–5 家，全部必须是 canonical equity Instrument；
- 默认比较最近 3 个可见年报期间，避免把累计中报与单季度或 TTM 混用；
- 输出规模、增长、盈利、现金流质量、资产负债和可用的当前估值事实；
- 不自动选择同行、不评分、不排名、不生成目标价、不确认或修改 Thesis；
- 部分公司或指标失败时返回有界的 Partial 事实包，不用零填充；
- Provider 调用串行执行，复用 Router、缓存、限流和熔断，避免批量研究制造竞争。

## 2. 用户交互

### 2.1 美股示例

```json
{
  "operation": "peer_comparison",
  "idempotency_key": "example",
  "primary_instrument_id": "equity:US:TTWO",
  "peer_instrument_ids": [
    "equity:US:EA",
    "equity:US:RBLX"
  ],
  "period_mode": "annual",
  "periods": 3,
  "include_valuation": true,
  "as_of": "2026-07-26T12:00:00Z"
}
```

### 2.2 A 股示例

```json
{
  "operation": "peer_comparison",
  "idempotency_key": "a-share-game-peer-comparison-20260726",
  "primary_instrument_id": "equity:A_SHARE:002555.SZ",
  "peer_instrument_ids": [
    "equity:A_SHARE:300418.SZ",
    "equity:A_SHARE:002624.SZ"
  ],
  "period_mode": "annual",
  "periods": 3,
  "include_valuation": true,
  "as_of": "2026-07-26T12:00:00Z"
}
```

Codex 可以据此回答“TTWO 相比 EA、RBLX 的现金流质量和估值差异是什么”，但不得把缺失
指标变成劣势，也不得把横向数值直接转成买卖结论。

## 3. 请求契约

新增 `PeerComparisonRunInput`：

| 字段 | 规则 |
|---|---|
| `idempotency_key` | 必填，1–128 字符；沿用 Workflow replay 语义 |
| `primary_instrument_id` | 必填，A 股或美股 equity Instrument |
| `peer_instrument_ids` | 1–5 个，保持调用方顺序，去重且不得包含主公司 |
| `period_mode` | `annual`（默认）或 `latest_reported` |
| `periods` | 1–5，默认 3 |
| `include_valuation` | 默认 `true`；只返回满足 `as_of` 约束的估值事实 |
| `include_operating_metrics` | 默认 `false`；只对 A 股进入第二实施切片 |
| `as_of` | 可选、必须带时区；所有财报和公告必须在该时点可见 |

V1 不接受自由文本公司名或 ticker。Codex 应先调用 `instrument_resolve`，避免 Workflow
内部猜测实体。单次请求不允许 A 股与美股混合，也不做汇率换算。

### 3.1 `period_mode`

`annual` 是安全默认值：

- A 股只选择 `basis="annual"`；
- 美股只选择 annual frequency，并保留每家公司的 fiscal year、period start/end 和 filing；
- 美股不同财年截止日可以按 fiscal year 展示，但必须标记日期差异，不宣称同日口径。

`latest_reported` 是显式高级选项：

- A 股只有相同 `q1_ytd`、`h1_ytd`、`nine_month_ytd` 或 `annual` 才可进入同一比较行；
- 美股必须具有相同 frequency，并披露 fiscal period 和覆盖天数；
- 找不到共同口径时保留各公司观察值，但整行标记 `NOT_COMPARABLE`；
- 不自行构造 TTM，不用累计中报减法猜单季度。

## 4. 指标范围

### 4.1 Core delivery

Core delivery 只消费已经标准化的财报和当前基本面：

| 分组 | 指标 |
|---|---|
| 规模 | revenue、operating_income、net_income、operating_cash_flow、free_cash_flow、total_assets、stockholders_equity |
| 增长 | revenue_yoy、operating_income_yoy、net_income_yoy、operating_cash_flow_yoy |
| 盈利 | operating_margin、net_margin |
| 现金流质量 | operating_cash_flow_to_net_income、free_cash_flow_margin、capital_expenditure_to_revenue |
| 资产负债 | current_ratio、net_debt、debt_to_equity |
| 当前估值 | market_cap、trailing_pe、price_to_book、price_to_sales；其余只在同口径 Provider 明确提供时返回 |

增长率只在同一公司连续两个同 frequency、同 basis 的期间上计算。比例指标只有所需分子、
分母都存在且单位兼容时才产生。所有派生公式使用版本化 `peer_comparison_v1`，不覆盖
Provider 原始值。

Forward PE、PEG、分析师预期和 estimate revision 不进入默认比较表。它们依赖 current-only
估计口径，容易被误解为财报事实；未来如需加入，必须作为独立 `expectations` 分组。

### 4.2 A 股经营指标扩展

第二实施切片可在 `include_operating_metrics=true` 时调用现有 CNINFO
`company_operating_metrics`：

- 只比较各公司都明确披露、且 `metric_code`、单位、frequency、measurement basis 一致的指标；
- 销量、售价、收入、出栏、能繁母猪、完全成本等均不得仅凭名称相似合并；
- 不同公司定义不一致的指标进入 source-separated appendix，不进入横向表；
- 美股暂不接受该字段为 `true`，避免假装存在统一的公告经营指标 Provider。

## 5. 输出契约

新增 `PeerComparisonFactPackageDTO`：

```text
primary_instrument_id
peer_instrument_ids
market
as_of
period_mode
comparison_rows[]
operating_metric_appendix[]
unavailable_instrument_ids[]
algorithm_version
execution_effect=false
```

每个 cell 自带 Instrument、期间、报告口径、发布时间和 source names；整包另列完全不可用的
同行。V1 不重复返回公司名和行业元数据，也不因估值 snapshot 缺失而补造 profile。

每个 `comparison_rows[]` 包含：

```text
metric_group
metric_code
unit
formula / formula_version
comparability = COMPARABLE | PARTIAL | NOT_COMPARABLE
values[]:
  instrument_id
  value | null
  period_start | null
  period_end
  fiscal_year | null
  basis
  published_at / filed_at
  source_names
  unavailable_reason | null
```

不返回 winner、rank、percentile、score、premium/discount verdict 或 buy/sell label。
Codex 可基于这些事实做解释，但必须保留报告期和缺失字段说明。

## 6. 确定性对齐算法

1. 验证全部 Instrument 为同市场 equity，主公司不在同行列表内。
2. 按“主公司 → 同行输入顺序”串行拉取标准化 statements。
3. 根据 `period_mode` 选择每家公司在 `as_of` 时可见的期间。
4. 对每个期间建立 `period_basis_key`：market、frequency、basis/fiscal period、fiscal year、
   duration days。
5. 只有单位、币种、期间 basis 和公式输入满足 gate 时才形成 `COMPARABLE` 行。
6. 缺少个别公司时形成 `PARTIAL`；所有公司无法同口径时为 `NOT_COMPARABLE`。
7. `include_valuation=true` 时再串行取得当前 snapshot；历史 `as_of` 无 cutoff-safe 估值时
   返回缺失原因，绝不回填今天的数据。
8. 汇总 Provider source、freshness、warnings 和每家公司 coverage，生成一个有界事实包。

同市场不等于同币种。任何报告币种不同的绝对金额行均为 `NOT_COMPARABLE`；ratio 可以在
公式和期间口径一致时比较。V1 不引入 FX Provider。

## 7. 错误与 warning 语义

输入 schema / DTO 直接拒绝以下 cross-field rule；它们是 JSON-RPC validation
details，不伪装成业务 Tool Envelope error：

- `peer_market_mismatch`
- `peer_asset_type_unsupported`
- `primary_in_peer_set`
- `duplicate_peer_instrument`
- `peer_count_out_of_range`

业务事实使用 warning，不把整次 Workflow 伪装成失败：

- `PEER_PERIOD_BASIS_MISMATCH`
- `PEER_CURRENCY_MISMATCH`
- `PEER_METRIC_UNAVAILABLE`
- `PEER_VALUATION_AS_OF_UNAVAILABLE`
- `PEER_OPERATING_METRIC_NOT_COMPARABLE`
- `PEER_PROVIDER_PARTIAL`

主公司 statements 完全不可用时 Workflow 为 `FAILED`；某个同行不可用时为 `PARTIAL`，仍返回
其他公司的事实。绝不因同行缺失而删除主公司结果。

## 8. 代码边界与命名

```text
src/
├── domain/company_comparison/
│   ├── __init__.py
│   ├── enums.py
│   ├── models.py
│   └── calculator.py
├── application/
│   ├── dto/peer_comparison.py
│   └── services/peer_comparison_service.py
└── interfaces/mcp/tools/workflows.py
```

冻结名称：

- `PeerComparisonPeriodMode`
- `PeerComparisonStatus`
- `PeerComparisonCell`
- `PeerComparisonRow`
- `PeerComparisonFactPackage`
- `PeerComparisonCalculator`
- `PeerComparisonRunInput`
- `PeerComparisonFactPackageDTO`
- `PeerComparisonService.compare()`
- `ResearchWorkflowOrchestrator.run_peer_comparison()`
- MCP adapter：`research_run_peer_comparison()`

Domain 只处理已经规范化的值、期间和来源引用，不导入 DTO、Provider 或 MCP。Application
service 复用现有 AShare/US coordinators；不得从 application 直接 import infrastructure。

不新增 Infrastructure Provider。Core delivery 不新增 migration：Workflow 继续使用现有
run/report 持久化和 idempotent replay。

## 9. MCP 与 schema 预算

`research_workflow_run` 增加：

```text
operation = "peer_comparison"
```

公共工具数必须保持 28。新 variant 只暴露第 3 节的 8 个字段，不把内部 metric enum 或
Provider 参数展开到 MCP schema。目标是新增 input schema 不超过 2.5 KB，并保持全部 28 个
工具 input schema 合计低于旧 52-tool 基线的 41,366 bytes。

Workflow synthesis contract 新增以下必答部分：

```text
comparison_basis
material_differences
cash_flow_quality
balance_sheet_resilience
valuation_basis_and_gaps
peer_data_limitations
```

候选更新仍必须通过公开的 `research_judgment_propose`，Workflow 自身不修改 Case、Thesis、
Trade Plan、Monitor、Watchlist 或账户。

## 10. 精简 TDD

不做市场 × Provider × 指标 × 缺失字段的全排列。

### Domain / DTO

- 同市场、唯一同行、数量上限和 aware `as_of`；
- A 股 annual 与 h1_ytd 不混合；
- 美股不同 fiscal year-end 可披露但不冒充同日；
- currency mismatch 阻止绝对金额比较，ratio 保持可用；
- growth 只由同 basis 的连续期间产生；
- 缺失输入不生成派生指标。

### Application

- 一个 A 股三公司成功样本；
- 一个美股三公司成功样本；
- 一个同行 Provider 失败返回 `PARTIAL`；
- 一个 historical `as_of` 禁止当前估值回填；
- 验证 Provider 串行调用和 terminal replay 不重复请求。

### MCP / Eval

- `research_workflow_run.peer_comparison` closed-union schema；
- 28-tool inventory 不变且 schema budget 通过；
- A 股、美股各增加 1 个 eval 对话；
- 一个 stdio 代表性调用即可，不复制所有 unit 组合。

## 11. 实施切片

### 3B-T02-0 — 契约冻结

- Domain enums/models、input/output DTO；
- metric registry、period/currency gate；
- MCP schema golden 和预算测试。

### 3B-T02-1 — 财报比较核心

- A 股/美股 statements 串行采集；
- annual/latest_reported 对齐；
- 规模、增长、盈利、现金流质量和资产负债比较；
- Partial、coverage、provenance 和 replay。

### 3B-T02-2 — 当前估值事实

- A 股 quote 与美股 fundamentals snapshot；
- historical `as_of` fail-closed；
- current-only / provider basis 明确披露。

### 3B-T02-3 — A 股经营指标扩展

- caller opt-in；
- exact metric/unit/frequency/basis intersection；
- 不可比事实进入 appendix。

### 3B-T02-4 — 交付收口

- Skill、能力指南、Phase 3 文档与 release notes；
- 两个 eval、代表性 stdio、Ruff、mypy、focused pytest、wheel smoke；
- 仅在最终合并前跑一次全量 pytest。

## 11.1 实施验收记录（2026-07-26）

- 已作为 `research_workflow_run.peer_comparison` 第六个 operation 接入，公共工具仍为 28 个；
- A 股与美股复用现有 normalized statements Coordinator，按主公司及调用方同行顺序串行；
- annual/latest_reported、1–5 个期间、current valuation 和 A 股经营指标 opt-in 已接入；
- historical `as_of` 不回填今天的估值，同行 Provider 失败保留 Partial 事实包；
- 金额币种不一致、期间缺失、指标缺失和经营口径不一致均返回明确 warning；
- Workflow terminal replay 不重复 Provider 请求；没有 Case、Thesis、Trade Plan 或账户副作用；
- compact 输入 schema 合计 38,395 bytes，低于 40 KiB 守卫，单工具也低于 8 KiB；
- 精简测试覆盖输入 gate、A/美股路径、币种/期间对齐、派生公式和幂等 replay。

## 12. 完成定义

- 用户明确指定 1 家主公司和 1–5 家同行即可获得一个有界比较事实包；
- A 股和美股均有真实 Provider 路径，单次请求不跨市场；
- 年报默认安全，累计期间、财年截止日、币种和估值时点不会被混淆；
- Provider/指标缺失产生 Partial/Not Comparable，不产生零值或隐式排名；
- 无新 Provider、secret、migration、后台任务或公共 MCP 工具；
- Workflow replay 不重复 Provider 请求，结果 `execution_effect=false`；
- compact inventory、schema budget、eval、docs 和 Skill 一致；
- 精简质量门通过。

## 13. 明确不做

- 自动发现或推荐同行；
- 跨 A 股/美股直接比较绝对金额；
- 自动 FX 换算；
- 自动排名、综合评分、目标价或估值 verdict；
- 预测财务、分析师一致预期历史和 forward estimate 比较；
- 构造 TTM 或猜测单季度；
- 自动修改 Investment Case、Thesis、Trade Plan 或 Monitor；
- 为每个行业增加专用比较工具或 Provider。
