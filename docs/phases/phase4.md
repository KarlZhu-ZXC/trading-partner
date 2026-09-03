# Phase 4 — Trading Journal, Performance, and Behavior Loop

状态：**Phase 4A–4D v1 已实现并通过全量、Console、migration 与隔离 wheel 验收**

当前 migration head：`0071_external_note_reviews`

产品入口：Console `Journal`

公共 MCP 表面：`mcp_vnext_shadow` **30 个工具**

## 1. 产品决定

Phase 4 把 Trading Partner 从“研究、监控、组合读取和周期复盘的功能集合”，推进为一套围绕
实际操作持续学习的个人投资系统。核心流程固定为：

```text
Decision / NO_ACTION
        ↓
Order Intent / Order Result
        ↓
Broker Transaction / Fill
        ↓
Trade Cycle
        ↓
Performance + Behavior Review
        ↓
Next Decision Discipline
```

这条链必须证明：当时知道什么、决定做什么或不做什么、实际发生什么、结果如何、下一次应
改变什么。

Phase 4 不增加新的市场、券商或交易权限。它把现有 Research Subject、Thesis、Trade Plan、
Decision Record、Broker Receipt、Account Transaction、Portfolio Performance、Trade Retro、
Scorecard 和 Review Queue 组织成一条稳定主线。

### 1.1 Reuse-first 约束

Phase 4 不为流程中的每个名词创建新模块。实现优先级固定为：

1. 复用 `/decision-workbench` 兼容路由承载 Console `Journal` 流程壳；
2. 复用现有 Decision Record 保存 Decision/NO_ACTION；
3. 复用 Monitor + Catalyst 作为 `Observe` 组件，不创建第二套 Evidence Hub；
4. 复用 Portfolio Positions/Transactions/Performance 作为 `Execute` 和绩效组件；
5. 复用 Trade Retro + Judgment Scorecard 作为 `Review` 组件，不创建新的复盘/评分页面；
6. 只有现有领域确实无法表达的 `Trade Cycle` 和日频账户权益才新增持久化对象。

独立 Agenda、Scorecard 和 Retro 路由在兼容期保留专业下钻能力，但不继续占用一级导航，也不要求
用户按模块逐页完成同一条流程。

### 1.2 当前实现

- `/decision-workbench` 是兼容路由，用户界面名称统一为 `Journal`；主流程是
  `Decide → Observe → Execute → Review`。
- Decision/NO_ACTION、exact Plan version、review due、Timeline、Trade Cycle、Daily Equity、
  TWR/MWR/XIRR/drawdown、behavior cohort、immutable review 和 external Observation 均已实现。
- 页面读取 durable state，不因打开页面隐式刷新 Broker；Notes 正文仅在 Notes 页签按需读取。
- exact durable link 缺失时保持未关联/不可用；Instrument 和时间接近不能证明事前 Decision。
- 公共 MCP 当前为 30 个工具，当前 migration head 为
  `0071_external_note_reviews`。

## 2. 用户要能直接回答的问题

- 我为什么做了这次操作？当时对应哪个 Thesis 和 Trade Plan？
- 我原本准备在上涨、横盘、回调和失效情景下分别怎么做？
- 我实际提交了什么订单？Provider 接受、拒绝、取消或不确定的结果是什么？
- 最终成交多少、均价多少、费用多少？
- 这是首次建仓、加仓、减仓、退出，还是卖出后的重新进入？
- 从首次建仓到完全退出，这个完整交易周期赚了多少、承担了多少风险？
- 账户收益来自价格、已实现利润、股息、利息，还是外部现金流？
- TWR、MWR/XIRR、最大回撤和净值曲线是否具有完整数据基础？
- 最常出现的是追高、无计划交易、扩大风险、周期漂移，还是失效后不退出？
- 上一次复盘形成的行动项，在下一批同类交易中是否真的改善？

成功不以增加统计卡片为标准，而以闭环成立为标准：

- 支持的 Broker 活动全部进入统一 Timeline，重复记录为零；
- 每个未关联 Trade Fill 都进入可关闭的 Review Queue；
- 每个关闭的 Trade Cycle 都有明确收益覆盖状态；
- 每个行为比例都能下钻到 exact Cycle/Event；
- 用户无需复制 opaque ID 即可关联 Research Subject、Thesis、Trade Plan 和交易；
- 复盘结论进入下一轮可追踪行动，但不自动修改 Thesis、Trade Plan 或订单。

## 3. 非目标

- 不把 Journal 变成税务、会计或完整券商对账系统；
- 不用估算值填补缺失成交、手续费、公司行动、FX 或账户净值；
- 不把模型总结作为收益、交易分类或纪律违规的事实来源；
- 不因事后盈利把无计划交易改写成正确决策；
- 不因事后亏损把按计划执行的交易自动判定为纪律错误；
- 不计算跨币种总收益，直到 timestamped FX 覆盖和换算政策正式定义；
- 不从持仓变化反推出一定发生买卖；转仓和公司行动保持独立类别；
- 不开放自动确认、自动下单、订单替换、卖空、期权或其他无人值守交易；
- 不删除现有 Portfolio、Trade Retro 或 Research 专业页面；Journal 保留兼容路由。

## 4. 事实边界：不建立第二套交易事实

| 事实 | 权威来源 |
|---|---|
| Research Subject / Thesis / Trade Plan | 现有 versioned Research 状态 |
| Decision / NO_ACTION | 已确认 Decision Record / Journal Decision Revision |
| Trading Partner 订单意图与结果 | Broker Preview、Submit、Status、Cancel receipt |
| 外部券商成交与现金活动 | `AccountTransaction` |
| 账户和持仓估值 | `AccountSnapshot` / Position snapshot |
| 行情和技术事实 | Provider-backed quote/bar/technical DTO |
| 周度纪律发现 | immutable Trade Retro Run / Finding |
| 人工复核与行动项 | append-only Retro Review / ReviewItem occurrence |

Journal 不复制这些对象并形成冲突副本。它新增统一引用、用户注释、确定性归组、绩效 Run 和
Console read model。

页面必须区分：

- **Source Fact**：Broker、Research、Monitor 或 Provider 持久化事实；
- **Deterministic Derivation**：Trade Cycle、FIFO、TWR、行为规则；
- **Human Interpretation**：操作原因、情景选择、复盘说明和 Finding disposition。

### 4.1 “所有操作”的覆盖边界

Phase 4 的目标是对**所有可观察或主动记录的操作**形成统一账本，不承诺 Broker 未提供的历史
订单意图能够自动恢复。

| 来源 | 自动得到的内容 | 无法自动证明的内容 |
|---|---|---|
| Trading Partner Schwab order | Preview、submit、status、cancel、typed error、exact authorization | 用户在其他终端修改或取消的原因 |
| Schwab transaction sync | Provider 支持窗口内的成交、股息、利息、费用和现金活动 | 超出覆盖窗口的活动、未成交历史意图、缺失公司行动 |
| Moomoo transaction sync | OpenD 可规范化的成交与账户活动 | OpenD 未暴露的历史订单原因或缺失活动类型 |
| Manual CSV | 文件合同明确提供的持仓或活动字段 | 未提供时间、费用、订单状态和事前判断 |
| Research / Agent / Console | 用户明确保存的 Decision、NO_ACTION、Plan 和 Review | 未保存的脑内判断或其他聊天中的模糊意图 |

因此 Timeline 的覆盖分为：

- `FULL_CHAIN`：Decision、Order Intent、Order Result、Fill 和 Review 均有 exact source；
- `EXECUTION_ONLY`：只有 Broker Fill/Activity；
- `INTENT_ONLY`：有 Decision/Order Intent，但尚无 Fill；
- `RETROSPECTIVE_LINK`：Fill 后补录的关联；
- `UNRESOLVED`：来源或分类仍不完整。

外部 Broker Fill 不应被强迫补写一个看似完整的事前理由。用户可以明确标为 `UNPLANNED`，这本身
就是有价值的行为事实。

### 4.2 生命周期连接规则

- Decision 和 NO_ACTION 只记录判断，不授权订单；
- Order Preview 是短期意图，不是提交或成交；
- `SUBMITTING` / `UNKNOWN` 不能自动重试，也不能推断 Fill；
- Order Result 只有 Provider order ID 或后续 exact status 才能成为已接受事实；
- Trade Cycle 只由可证明的 Fill/Corporate Action/Transfer 影响数量；
- Position Snapshot 只用于勾稽，不作为缺失 Fill 的替代；
- Review 可以纠正分类和纪律解释，不能改写交易事实。

## 5. 核心领域模型

### 5.1 Journal Entry

`JournalEntry` 是 append-only 时间线引用，不拥有 Broker 或 Research 原始事实。Phase 4A 首先把
它实现为对现有 Decision、Order Receipt、AccountTransaction 和 Review 的统一 read projection；
只有需要人工 annotation 或稳定 source correlation 时才新增最小持久化记录，不预先复制全部
source rows。

```text
journal_entry_id
occurred_at / observed_at
entry_type / classification
source_type / source_id
account_ref? / instrument_id? / currency?
quantity? / price? / price_basis?
subject_id? / thesis_id? / trade_plan_id? / decision_id?
order_intent_id? / trade_cycle_id?
strategy_id? / scenario? / action?
quality_status / warning_codes[]
created_by / idempotency_key
```

`entry_type` 至少覆盖：

- `DECISION`、`NO_ACTION`；
- `ORDER_PREVIEWED`、`ORDER_SUBMITTING`、`ORDER_ACCEPTED`、`ORDER_REJECTED`；
- `ORDER_CANCEL_REQUESTED`、`ORDER_CANCELLED`、`ORDER_UNKNOWN`；
- `FILL`、`DIVIDEND`、`INTEREST`、`FEE`、`TRANSFER`、`CORPORATE_ACTION`；
- `CYCLE_OPENED`、`CYCLE_REDUCED`、`CYCLE_CLOSED`、`REVIEW_COMPLETED`。

`classification` 至少区分：

- `ACTIVE_TRADE`
- `LONG_TERM_INVESTMENT`
- `HEDGE`
- `CASH_MANAGEMENT`
- `TRANSFER_OR_ADMIN`
- `UNCLASSIFIED`

SGOV scheduler 订单和成交默认是 `CASH_MANAGEMENT`。它们进入账户收益和现金事实，但默认不进入
主动交易胜率、追高、尝试次数和持仓纪律统计。

### 5.2 Journal Annotation

用户可以补充或修正 Research/Plan 关联、分类、Strategy、Scenario、操作原因和复盘说明。
修正形成 `JournalAnnotationRevision`，保留 `expected_version`、actor、authorization note、
idempotency key、创建时间和被替代版本。任何修正都不能改变 Broker 成交价、数量、时间、费用
或订单结果。

### 5.3 Trade Cycle：交易统计的统一分母

一个 `TradeCycle` 表示同一账户、同一 Instrument、同一方向从净头寸为零开始，到再次归零
结束的完整持有周期。

确定性规则：

1. 净头寸由零变为正，创建 Long Cycle；
2. 同方向增加数量记为 `ADD`，仍属于当前 Cycle；
3. 数量下降但未归零记为 `REDUCE`；
4. 净头寸归零，Cycle 进入 `CLOSED`；
5. 归零后再次买入创建新 Cycle，并可关联为 `REENTRY`；
6. 成交跨越零轴时，仅在数量足以证明时拆为旧 Cycle 关闭腿与新 Cycle 开启腿；
7. Transfer、拆股、合并、分拆不创建虚假 Cycle；
8. 无法判断的历史活动进入 `UNRESOLVED`，不猜测。

状态固定为 `OPEN`、`REDUCING`、`CLOSED`、`UNRESOLVED`。自动归组是 rebuildable projection；
人工拆分、合并或重新关联是 append-only override，保留原算法结果和修订历史。

### 5.4 Daily Equity Snapshot

现有 Position Snapshot 足以支持部分盈亏归因，但不自动等于完整账户净值。Phase 4 新增：

```text
account_ref / valuation_at / market_session_date / currency
equity_value / cash_value / gross_position_value
net_external_cash_flow_since_previous
valuation_basis / source_snapshot_id
coverage_status / warning_codes[]
```

只有 Broker 明确权益字段或正式定义、可勾稽构成才能成为 `equity_value`。持仓市值总和不能默认
冒充 NAV；估值时间、原币种和 Broker valuation-only 口径必须保留。

### 5.5 Performance Run

每次收益计算保存或可重建为 versioned Run：

```text
performance_run_id / start / end
account_refs[] / currency / method / algorithm_version
input_snapshot_ids[] / input_activity_ids[]
coverage_status / metrics / warning_codes[] / generated_at
```

相同输入和算法版本必须产生相同结果；新算法不覆盖旧 Run。

### 5.6 Behavior Review Run

`BehaviorReviewRun` 对 exact Cycle 集合执行确定性规则，输出每个比例的 numerator、denominator、
excluded count 和 source refs。可选 LLM 只能解释结果，不能改变规则命中、收益、Cycle 归组或
行动项状态。

### 5.7 权限矩阵

| 操作 | SYSTEM | User / External Agent | LLM |
|---|---:|---:|---:|
| 从 durable source 投影 Journal Entry | 允许，幂等 | 不需要 | 禁止直接写 |
| 保存 Decision / NO_ACTION | 禁止代替用户判断 | 明确 append 授权 | 只能提出或准备 exact 写入 |
| 自动归组 Trade Cycle | 允许，确定性、版本化 | 可读取 | 只能解释 |
| 拆分/合并/重新关联 Cycle | 禁止自主决定 | versioned explicit write | 只能提出或准备 exact 写入 |
| 计算 Performance / Behavior Run | 允许，确定性 | 可显式运行/读取 | 只能解释 |
| 复核 Finding / 关闭行动项 | 仅 source disappearance 的既有安全自动关闭 | explicit review/resolve | 不能自主确认 |
| 提交/取消订单 | 仅已安装 SGOV narrow exception | 保留现有 exact confirmation | 禁止自主执行 |

## 6. Decision / NO_ACTION 合同

### 6.1 NO_ACTION 是正式决策

以下情况可记录 `NO_ACTION`：

- 上涨已完成，没有新结构和风险边界，不追高；
- 横盘方向不清，等待突破回踩或右侧确认；
- 回调只有价格下降，没有完成结构；
- Thesis、结构或 loss boundary 已失效，取消旧计划；
- 数据缺失，只能 `REVIEW`；
- 风险预算、现金或仓位约束不允许执行。

NO_ACTION 不计算假想收益，也不根据后来涨跌改写对错。只统计决策、依据、复核时间以及是否遵守
等待条件。

### 6.2 strategy_v1 快照

每个 material Decision 默认绑定 `strategy_v1` exact version，至少保存：holding horizon、cycle、
direction、structure、entry confirmation、loss boundary、first target、planned risk、可用时的
risk/reward、当前 Scenario 和四情景行动矩阵。

| Scenario | 允许动作 | 必填语义 |
|---|---|---|
| `UPSIDE` | `HOLD` / `REVIEW` / `NO_ACTION`；新确认后可 `ADD` | continuation/exhaustion、追高限制、新结构触发 |
| `SIDEWAYS` | `HOLD` / `NO_ACTION` / `REVIEW` | 区间、结束横盘的触发、复核时间 |
| `PULLBACK` | `NO_ACTION` / `REVIEW`；确认后可 `ADD` | 两段结构或右侧确认、loss boundary |
| `INVALIDATION` | `REDUCE` / `EXIT` / `REVIEW` | 失效事实、退出或重写条件 |

Trade Plan 已具备这些字段时，Quick Capture 自动引用 Plan revision，用户不重复填写。没有 Plan
时允许记录真实 Decision，但显示 `MISSING_PRETRADE_PLAN` 或 `MISSING_LOSS_BOUNDARY`，不伪造
输入，也不阻断事实记录。

### 6.3 三个捕获时点

1. **事前记录**：Research、Monitor、Portfolio 或 Journal 点击 `Record Decision`；
2. **订单链自动捕获**：Trading Partner preview/submit/status/cancel receipt 自动进入 Timeline；
3. **事后事实保留**：盘后外部 Broker Fill 进入 durable Timeline；缺少 exact Decision/Plan
   link 保持可见，但不再生成 Review Queue 工作项。

产品不要求每次下单前填写长表单。它通过 Plan 自动填充、盘后补关联和明确质量缺口降低摩擦。

## 7. 收益与绩效口径

### 7.1 账户级

| 指标 | 口径 | 完整条件 |
|---|---|---|
| Net P/L | realized + unrealized + dividends + interest − known fees | 期初 lot、成交、费用和期末估值可证明 |
| TWR | 按 external cash flow 切分子期间后复合 | 每个必要估值边界和现金流完整 |
| MWR / XIRR | 使现金流和期末权益 NPV 为零的收益率 | 流向、时间、期末权益完整且有唯一可用解 |
| Maximum Drawdown | 从同口径 TWR index 历史峰值计算 | 连续估值满足覆盖要求 |
| Income Return | dividends + interest / 选定资本基数 | 收益活动覆盖完整 |
| Fee Drag | known fees / 选定资本基数 | 费用覆盖完整 |

TWR 不能使用简单“期末减期初再除期初”。股息、利息和费用是投资结果，不是 external cash flow；
入金、出金和账户转移才是 external flow。少于一年默认显示期间收益，不擅自年化。不同币种先
分别显示；没有 timestamped FX coverage 时不提供全组合收益率。

Phase 4C v1 使用精确现金流边界：若 `V(i-1)` 与 `V(i)` 是现金流前后的可用估值，且 `F(i)`
是该子期间 external flow，则：

```text
r(i) = (V(i) - F(i) - V(i-1)) / V(i-1)
TWR = product(1 + r(i)) - 1
Drawdown(t) = Index(t) / max(Index[0..t]) - 1
```

若现金流发生时没有可用估值边界，v1 不静默改用 Modified Dietz。未来若增加近似方法，必须以
独立 `method` 和 warning 发布。XIRR 使用 actual timestamps；现金流没有符号变化、存在多解或
数值求解不收敛时返回 `NOT_COMPUTABLE`。

### 7.2 Trade Cycle

- gross/net realized P&L；
- OPEN Cycle remaining unrealized P&L；
- dividends、interest、fees；
- maximum deployed capital 与 return on maximum deployed capital；
- holding duration、fill/add/reduce count；
- initial planned risk；
- `R-Multiple = net P&L / initial planned risk`；
- MAE/MFE（仅在有完整、同口径 bars 后启用）；
- exit reason、invalidation adherence、horizon drift。

没有 planned risk 时 R-Multiple 为空；没有适合历史价格时 MAE/MFE 为空；OPEN Cycle 不进入
closed-cycle 胜率分母。

### 7.3 行为统计

第一版包括：

- closed Cycle 数量、winning/losing/flat 数量；
- 胜率、平均盈利、平均亏损、payoff ratio；
- 平均/中位持有时间和 turnover；
- 有事前 Decision、exact Plan、pre-fill invalidation 的 Cycle 比例；
- 按失效条件退出比例；
- 同日退出后 re-entry 次数；
- 同一 entry logic attempt count；
- 第三次尝试且没有新 Plan 的次数；
- ADD 前有新确认且 combined risk 未扩大的比例；
- 计划周期与实际持有周期不一致次数；
- 四种 Scenario 下的动作分布；
- NO_ACTION 数量及到期复核完成率。

胜率按完整 CLOSED Cycle 计算，不按 Fill 计算。不创建无法解释的综合纪律总分。

## 8. Console 新形态

### 8.1 顶层导航与边界

现有 `/decision-workbench` 路由和聚合 API 原地演进为一级菜单 `Journal`，不再创建另一套 Journal
路由。迁移期保留专业页面：

- Portfolio：账户快照、持仓、Activity 和原生绩效下钻；
- Trade Retro：immutable 周度 Run 与人工 Review；
- Research：Thesis、Trade Plan 和证据；
- Journal Reviews：跨模块待处理事项；
- Journal：贯穿模块的操作与学习主线，并 deep-link 回专业页面。

Journal 不复制第二套 Portfolio 编辑器或 Retro 写入口。

导航分两步演进：

1. Phase 4A：Workbench 政名 Journal；Agenda、Scorecard、Retro 从一级导航降级为 Journal 中的
   evidence/review 下钻，Research、Monitors、Portfolio 仍保留一级入口；
2. Phase 4D 生产运行至少 30 天且覆盖/一致性验收通过后，再依据实际访问和任务完成数据决定
   Portfolio/Research/Monitors 是否需要进一步收拢，不在实现时直接删除路由。

### 8.2 页面结构

Journal 遵循 [Console Layout Standard](../guide/console-layout.md)。全局 durable-only 筛选包括
Period、Account、Instrument/Research Subject、Strategy、Classification、Data Quality。

Journal 默认以全部账户、全部 Instrument 的 durable 交易事实为范围；Research Subject 是可选筛选和
Decision 上下文，不再是进入页面的前置条件。全局筛选保持紧凑：Period、Account、Instrument、
Quality 常驻，Research Subject、Strategy 和 Classification 收入 More Filters。
Account、Instrument、Research Subject 和 Classification 使用同一个 autosuggest multi-select：
输入只缩小候选范围，必须选择一个已解析候选才会生成筛选 chip；自由文本永远不改变查询。
Period 与 Quality 保持闭集单选。每个筛选支持独立删除，多个选项按集合并集、不同筛选维度按交集应用。

六个 Tab：

#### Overview

顺序固定：`Data Confidence` → `Results` → `Holding Patterns / Contributors` →
`Latest Changes / Needs Review`。收益卡不能位于 Data Confidence 之前；INCOMPLETE 必须就近
显示缺失原因，完整 Cycle 与 Notes 详情分别进入对应 Tab。

#### Trade Cycles

使用 master-detail `EntityBrowser`。列表显示 Instrument、状态、时间、净 P/L、质量；详情展示
Decision → Orders → Fills → Position Path → Exit → Review，并显示计划与实际差异。人工拆分、
合并、重新关联需要影响预览和 append-only revision。
Cycle 页容量只由浏览器 viewport 决定，与右侧详情内容和当前结果数量无关：紧凑窗口显示 4 项，
中等高度显示 6 项，较高窗口显示 8 项，超高窗口显示 10 项；窄于 700px 时固定 4 项。左侧使用
固定行高和固定分页区，右侧详情在自己的区域滚动，任何一侧的内容都不能反向改变另一侧高度。
Cycle 生命周期、质量和分类不得共用无语义的默认灰色：`OPEN` 使用绿色表示仍有持仓数量，
`CLOSED` 使用中性文本表示已回到零仓位，`UNRESOLVED` 使用红色表示无法重建；`COMPLETE`
使用绿色、`INCOMPLETE` 使用琥珀色；`UNCLASSIFIED` 也使用琥珀色但必须保留 Classification
标签。Section Header 必须显示 `Data Quality · Complete/Incomplete` 和具体计数，不能只放一个
脱离语境的 `INCOMPLETE` Badge。

#### Behavior

- 不显示单一纪律分；
- 首屏只放 4–6 个可行动指标；
- 其余按 Technique、Risk、Behavior、Sustainability 分组；
- 每项可下钻 exact Cycle；
- 支持 Strategy、Scenario、Instrument class、holding horizon cohort；
- 样本不足只显示样本数，不生成趋势结论；
- 展示上一期行动项和本期是否复发。

#### Notes

- 以 provider-neutral External Observation Revision 为唯一来源，Moomoo 只是来源标识；
- 使用 Source Notes → selected Note master-detail，显示 What Changed、四情景 Current View、
  Position & Cycles、Attribution 和 Revision History；
- `SUMMARY_ONLY` 不进入模型，也不显示为可采纳正文；
- `Review as Decision` 只预填现有 Decision 对话框，并保存 exact Observation Revision 引用；
- Note、模型解释或时间接近都不能自动改写 Thesis、Trade Plan、Activity annotation 或订单。

#### Reviews

- 聚合 weekly、monthly、quarterly Review；
- 复用 Trade Retro Run/Review；
- 支持 Cycle 与 period cohort；
- action item 自动进入 Review Queue；
- 区分 `NEW`、`PERSISTENT`、`RESOLVED`、`RECURRED`；
- immutable findings 与人工 Review Revision 在 Journal 内完成，不再跳转另一套编辑页。
- `Create Weekly Review` 使用 canonical weekly windows：生成上一完整周期的 deterministic
  findings、记录 Behavior Review，并预备下一周期的 point-in-time snapshot；用户不再手动执行
  Prepare → Run 两步。

#### Timeline

- 按交易日分组 Decision、Order、Fill、Cash Activity、Review；
- 默认折叠普通现金活动，突出 Decision、订单异常和 Fill；
- 每项只显示一个主时间、主状态和必要金额；
- 详情显示 source、Plan、质量警告和 revision；
- 支持 `Unlinked Only`、`Exceptions Only`；
- 主列表不重复完整 opaque ID。

旧 `/retro` 只保留为兼容入口并重定向到 `Journal#reviews`；Trade Retro 的 immutable Run、Finding、
Review Revision、CLI 和 Obsidian export 继续作为底层审计能力存在。

### 8.3 Quick Capture

`Record Decision` 可从 Journal、Research Subject、Monitor Event 和 Portfolio Position 进入。
默认带入当前 Subject、Instrument、live Thesis、ACTIVE Plan revision、Strategy、四情景矩阵和
页面上下文。

使用已有 Plan 时，用户最少只需：Action、当前 Scenario、一句 Reason，以及 NO_ACTION/REVIEW
时的 Review Time。无 Plan 才展开 horizon、confirmation、invalidation、risk。保存按钮本身就是
一次明确 append 授权，不再出现内容相同的二次确认；version、actor、authorization 和
idempotency 仍保留。

当前 Quick Capture 自动绑定 `strategy_v1` 和可用的 exact Trade Plan version，Review Date 默认
为七天后并允许调整。`INITIATE_INTENT` / `ADD_INTENT` 没有 exact current Plan 时 fail closed；
`INVALIDATION` 不允许 INITIATE、ADD 或 HOLD。

### 8.4 Unlinked Activity durable read

`portfolio_analyze/unlinked_activity` 仍可按需读取未关联 Fill，显式调用方可执行：

- `Link Existing Decision / Plan`
- `Mark As Unplanned`
- `Classify As Cash Management`
- `Classify As Transfer / Corporate Action`
- `Resolve Duplicate / Provider Correction`

该读取不再出现在 Journal Timeline，也不生成或关闭 ReviewItem。已有 Annotation 保留，
Broker 交易事实不因是否关联而改变。

### 8.5 日常使用流程

#### 开盘前 / 每日第一次打开

- 默认进入 Journal Overview；
- 先看 Data Confidence 和 Needs Attention；
- 展示到期 NO_ACTION/REVIEW、OPEN Cycle 和 UNKNOWN order；
- 不自动刷新 Broker，不用陈旧数据伪装当日状态。

#### 形成操作判断时

- 用户在 Research、Monitor 或 Position 上点击 `Record Decision`；
- Quick Capture 复用 active Plan 和 strategy_v1 四情景；
- 保存后回到原上下文，不强迫进入 Journal；
- Decision 本身不触发下单。

#### 下单与成交后

- Trading Partner 发出的订单自动串入 Decision；
- 外部终端成交在下一次 transaction sync 后进入 Timeline；
- 能确定关联时自动链接，不能确定时进入 Unlinked Inbox；
- 不因 Position 已变化而倒推出订单细节。

#### 盘后两分钟收尾

- Journal 只展示当日新增和需要人工选择的项目；
- 用户完成 Plan 关联、UNPLANNED/CASH_MANAGEMENT 分类和必要的一句说明；
- 已完整且无异常的普通活动默认折叠；
- 全部清空不是目标，明确 ACKNOWLEDGED 和 due time 也算完成收尾。

#### 周末 / 月末

- Reviews 预先选择 exact period cohort；
- 先看收益覆盖，再看结果，再看纪律；
- 用户只处理 New/Persistent/Recurring findings；
- 形成的 action item 自动进入下一周期 Workbench；
- 下次复盘显示 action item 是否在可比 cohort 中复发。

## 9. 跨页面联动

| 页面 | Phase 4 联动 |
|---|---|
| Research | 最新 Decision、OPEN Cycle、Record Decision；不复制完整绩效 |
| Monitor | Event 带上下文进入 Quick Capture；Monitor 不创建交易结论 |
| Portfolio | Position/Activity 跳转 Cycle；保留账户级专业绩效 |
| Journal | 汇总主线、统计和关联；不复制专业写操作 |
| Trade Retro | Finding 引用 exact Cycle；行动项回 Journal/Workbench |
| Scorecards | 使用行为事实作为纪律证据；盈利不替代判断质量 |
| Journal Reviews | 聚合未关联 Fill、低覆盖绩效、逾期 Review、复发行为 |
| Agent Rail | 读取 Timeline/Cycle/Behavior；写入经过 exact append/confirm contract |

## 10. 系统架构

优先复用的现有模块：

```text
Journal aggregate / ReviewItem
Decision Record / Research Journal
AccountTransaction / AccountSnapshot
Portfolio Performance Attribution
Trade Retro / Judgment Scorecard
Monitor Dashboard / Catalyst Agenda
```

只有 Phase 4B/4C 无法由现有对象表达的边界才新增：

```text
domain/journal/trade_cycle.py
application/services/trade_cycle_service.py
application/services/performance_series_service.py
infrastructure/persistence/trade_cycle_repository.py
```

边界：

- Journal projection 只引用 durable source，不联网；
- Cycle 使用确定性、版本化算法；
- Performance 只消费持久化 Activity、Cash Flow、Equity Snapshot；
- 经过 owner 验证的 Broker Statement / position-import basis checkpoint 只能在精确时间
  重建未平仓 lot；它不创建成交或现金流，并保留 source reference、账单 SHA-256 和被替代的
  zero-cash import activity。之后的交易、DRIP 与分红继续按原事实计算；
- Behavior 只消费 exact Cycle/Decision/Plan；
- Console BFF 调用相同 application service，但不套 MCP 15 KiB projection；
- Console/Agent 不直接写数据库；
- rebuildable projection 与 business source of truth 分表；
- 大批量 materialization 使用 Operational Job claim/lease/receipt；
- 自动 projection 以 `(source_type, source_id, algorithm_version)` 幂等。

原始 Broker/Research 写入成功后，projection 可同步生成或由 job 补齐；projection 失败不回滚原始
事实。Data Quality Center 显示 lag/failure stage。Cycle 重建先产生 shadow comparison，再原子
切换 projection version。UNKNOWN 订单永不推断为 Fill，也不因持仓变化自动重试。

## 11. MCP 与 Agent

不新增第 28 个工具，在现有 grouped tools 增加 closed variants：

- `portfolio_analyze/journal_timeline`
- `portfolio_analyze/trade_cycles`
- `portfolio_analyze/performance_series`
- `portfolio_analyze/behavior_summary`
- `research_memory_append/decision|journal` 增加 Strategy、Scenario、Action、source-link 字段；
- `research_workflow_run/trade_retro` 支持 exact `trade_cycle_ids` 或 period cohort。

`account_get/transactions` 继续 durable-only；upstream 仍只通过明确
`external_state_sync/transactions` 或已安装 operational scheduler 刷新。Journal `Refresh` 只刷新
durable projection，`Sync Activity` 才访问 Broker。

Agent 必须区分 source fact、derived metric 和 human review。Agent 可以提出 Journal append/关联，
但不能为历史 Fill 编造 Decision，也不能自动确认订单。

## 12. 自动化

### Post-market

现有 account/transaction sync 成功后：

1. materialize 当日 Equity Snapshot；
2. 投影新增 Broker Activity；
3. 更新 OPEN Cycle；
4. 为无法关联的 Fill 创建/更新 ReviewItem；
5. 计算当日 coverage；
6. 同步一次 Moomoo Observation（`analyze=true`），分析新出现的 FULL 修订，并仅在归一化
   USER 文本发生变化时建立待复核项；记录 notes/revision/FULL/summary 覆盖；
7. 不自动生成行为结论、笔记模型草稿或修改 Trade Plan。

同步失败时保留 `SOURCE_SYNC_UNAVAILABLE`，不能把无新增记录描述为“今天没有操作”。

### Weekly / Monthly / Quarterly

- weekly 复用 `trading-partner-retro weekly` 和上一完整周 exact Cycle cohort；
- monthly/quarterly 使用完整自然期间，只比较可比 Strategy/Horizon/Instrument cohort；
- 样本不足只展示分布；
- Review action item 进入统一 Review Queue；
- 全部流程不执行订单。

## 13. 历史回补与 Activation Epoch

首次启用保存 `journal_activation_at`：

- Activation 后持续捕获 Decision、Order、Fill、Snapshot、Review；
- Activation 前尽力回补 Transactions 和确定性 Cycle；
- 找不到事前 Decision/Plan 时保留 `HISTORICAL_INTENT_UNAVAILABLE`；
- 找不到连续账户净值时不生成完整 TWR/Drawdown；
- 后写 note 不冒充事前决策；
- 补录同时保存 `recorded_at`、`effective_at` 和 `RETROSPECTIVE_ENTRY`。

历史归组先生成 shadow report：Cycle 数、跨零未解决活动、Corporate Action 缺口、账户覆盖。
确认回补范围后再持久化 projection，不改写 Broker 事实。

## 14. Data Confidence

Overview 至少显示：Transaction、Order lifecycle、Starting lot、Daily equity、External cash flow、
Fee、Corporate action、Decision-before-fill、Plan-link、Cycle grouping coverage。

状态固定 `COMPLETE`、`PARTIAL`、`INCOMPLETE`、`UNAVAILABLE`。COMPLETE 只在所选指标要求的全部
输入可证明时使用；不能把局部完整简化成一个模糊绿色灯。

## 15. 已实现组件与不变量

| Track | 范围 | 状态 |
|---|---|---|
| Phase 4A | Journal foundation、Timeline、Decision/NO_ACTION、source links | Implemented |
| Phase 4B | Trade Cycle、activity annotation revision、Unlinked Inbox、split/merge/relink、cross-page links | Implemented |
| Phase 4C | Durable equity projection、TWR、MWR/XIRR、drawdown、coverage | Implemented; unsupported Cycle R-multiple remains unavailable |
| Phase 4D | Behavior analytics、strategy_v1 cohorts、period Reviews、action recurrence、Console consolidation | Implemented |

### Projection 与回补安全

rebuildable projection 保留前一 active version，切换失败时回到旧 projection；原始 Transaction、
Decision、Order Receipt、Snapshot 和 Review 从不回滚或删除。任何 backfill 都先输出数量、覆盖、
冲突和 wall-clock，再请求用户确认持久化范围。

### 4A — Capture

当前合同：Journal、Decision Record Quick Capture、Positions/Transactions 聚合、source
projection、Activation 和 coverage report。Journal 不建立第二套交易事实。

验收：六张模块卡收敛为 Decide/Observe/Execute/Review 四个流程组件；source 重放不重复；
NO_ACTION 不创建订单；自动/人工记录可区分；页面不访问 Provider；无需复制 ID；一次明确保存
没有重复确认。

### 4B — Trade unit

当前合同：Cycle grouping v1、scale-in/out/close/re-entry、manual split/merge/link revision、Unlinked
ReviewItem、Research/Portfolio/Retro deep links。

实现复用 AccountTransaction 与现有 FIFO 口径，提供无持久化、可重建的
`portfolio_analyze/trade_cycles`：按 account + Instrument + native currency 归组；0→BUY 开启、BUY
加仓、partial SELL 减仓、归零关闭、后续 BUY 新建 re-entry Cycle。SELL-without-open、oversell、
缺 price/fee 和覆盖不足 fail closed/降级；Transfer、Corporate Action 和其他非 TRADE 不制造 Cycle。
Journal Execute 组件显示 latest Cycle 和 Cycle count，不创建第二套交易页面。
SGOV Cycle 确定性标为 `CASH_MANAGEMENT`，其他 Cycle 暂为 `UNCLASSIFIED`，避免在 Behavior
阶段把现金管理污染成主动交易，也不提前猜测用户意图。Portfolio Activity 复用共享 Paginator，
每页只显示 6 个 Cycle；真实账户当前可重建 72 个 Cycle，不一次性拉成长页面。

验收：Fill 不作为胜率分母；Activity 不进入两个 active Cycle；归零后新建 Cycle；Transfer 和
Corporate Action 不制造交易；revision 不删除算法结果；读取失败不关闭 ReviewItem。

### 4C — Trustworthy returns

当前合同：Daily Equity、native-currency TWR/MWR/XIRR/drawdown、Cycle P/L/return/R-Multiple、coverage
drill-down、Portfolio 与 Journal 联动。

验收：external flow 不计入收益；缺估值边界时 TWR unavailable/partial；无唯一 XIRR 不返回数字；
无 planned risk 不返回 R-Multiple；多币种不隐式相加；每项列出 input IDs 和算法版本。

### 4D — Behavior change

当前合同：Behavior rules、strategy_v1 cohorts、Behavior/Reviews tabs、weekly/monthly/quarterly Review、
persistent/recurring action、Agent read/exact append。

验收：无综合纪律分；每个比例有分子/分母/排除项；盈利不覆盖纪律缺口；亏损不自动判违规；
新/持续/解决/复发可区分；行动项可追踪到后续 cohort。

### 4E — External Observations

Journal 通过 provider-neutral Observation Source adapter 读取 Moomoo 本机
私有 living note，并允许未来 TradingView 或本机 Capture Bridge 输出同一 canonical full-text
snapshot。所有来源保存不可变 revision；无署名内容确定性归属本人，明确署名内容保持外部观点；OpenCode Go
`qwen3.8-flash`（`max`）只生成四情景与版本变化草稿。`Review as Decision`
预填现有 Decision 对话框，仍需用户编辑并显式保存，不自动创建 Thesis、Plan、Monitor 或订单。
完整外部输入合同见
[`observation-source-v1.schema.json`](../contracts/observation-source-v1.schema.json)；运行与隐私
边界见 [本地操作控制台与数据维护](../operations/local-console-and-maintenance.md)。

### 当前质量合同

- CI 必须通过 Ruff、strict Mypy、覆盖率门槛、Console build/unit/E2E、依赖审计、SBOM、
  forward-only migration 幂等与隔离 Wheel smoke。
- 公共 MCP inventory 固定为 27，compact schema 保持在仓库测试预算内。
- 盘后 job 的各步骤保留 durable receipt、幂等键和失败隔离；已完成步骤不会因后续
  Observation/Watchlist 失败回滚。
- Decision → Order → Fill → Cycle → Daily Equity/Returns → Behavior Review 的验收不得产生
  实盘副作用。

仍然不伪装为可计算的事实：账户级跨币种收益、没有 planned risk 时的 R-Multiple、OPEN Cycle
unrealized，以及没有完整 bars 时的 MAE/MFE。它们保持 `UNAVAILABLE` 或 `NOT_SUPPORTED`，
不会用估算填值；recurrence trend 只有积累出多个可比 period Run 后才展示真实历史。

## 16. Console 可用性预算

- 从 Research/Monitor/Portfolio 打开 Quick Capture：1 次动作；
- 有 Plan 时只需 Action、Scenario、Reason、保存；
- NO_ACTION 不超过 4 个必需输入；
- Fill 关联 existing Plan 不超过 3 次主要交互；
- Cycle 定位 source transaction：1 次展开或 deep link；
- 不手工输入 account_ref、subject_id、plan_id、transaction_id；
- 长 Timeline 虚拟化或分页；
- 窄屏优先时间、动作、标的、状态、金额，来源/质量进入详情。

## 17. 测试与性能边界

聚焦 source idempotency、Cycle zero-crossing/scale/transfer/corporate action、TWR cash-flow boundary、
XIRR 无解、FX 缺失、annotation revision/stale writer、授权门、ReviewItem 分页/失败、Console
durable-only、deep link、Quick Capture、migration 和 rebuild switch。

- 单元测试保持确定性，不访问真实 Provider 或私人运行数据；
- job、migration、数据库并发和真实路由使用 integration test；
- Console 变更同时覆盖 TypeScript、ESLint、production build 和关键 Playwright 流程；
- 性能回归使用同一命令、数据量和环境比较，不把旧测试计数或一次 wall clock 固化为规格。

## 18. 端到端验收场景

1. `SIDEWAYS / NO_ACTION` 无订单，到复核时间进入 Attention；
2. preview/submit 已接受但未成交，不创建 Fill；
3. Order `UNKNOWN` 进入人工 reconciliation，绝不自动重试；
4. 外部买入同步后无 Plan，创建 Unlinked ReviewItem；
5. Fill 关联 existing Decision/Plan，Cycle 变 OPEN；
6. 两次加仓、一次减仓、最终退出，形成一个 CLOSED Cycle 和正确净 P/L；
7. 完全退出后同日重新买入，形成第二 Cycle 和 REENTRY；
8. SGOV 自动买入进入 CASH_MANAGEMENT，不进入主动交易胜率；
9. 入金时 MWR 反映现金时间，TWR 排除 external flow；
10. 缺期初估值时收益率 INCOMPLETE；
11. 历史 Fill 补录 Decision 标记 RETROSPECTIVE；
12. 周度 Review 发现第三次重复尝试，行动项可在后续 cohort 验证是否复发。

## 19. Definition of Done

- Decision/NO_ACTION 到 Review 的 exact source chain 可下钻；
- 支持的数据源活动不重复、不静默遗漏；
- Trade Cycle 成为统一交易统计分母；
- 收益率具备现金流、估值、币种和覆盖口径；
- strategy_v1 四情景与实际动作可比较；
- Journal、Portfolio、Research、Retro 不存在冲突写入口；
- Console 主要流程无需复制 ID 或翻找多个页面；
- Agent 与 Console 使用同一 application contract；
- 公共 MCP 当前为 30 个工具；
- 订单权限、确认门和 SGOV 唯一 unattended exception 不变；
- README、capability guide、Console layout、roadmap、release note 和 Skill 同步更新。

Phase 4 的最终产品不是一个更漂亮的收益页面，而是一个能够持续证明“判断—执行—结果—改进”
关系的系统。
