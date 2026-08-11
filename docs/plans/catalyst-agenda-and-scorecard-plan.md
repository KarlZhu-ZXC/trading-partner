# Catalyst Agenda 与 Judgment Scorecard 实施计划

状态：C0–C3 与 Judgment Scorecard S1 已实现
公共边界：Agenda/Scorecard 复用 grouped tools；当前 vNext Shadow 总计 27 个 MCP 工具，二者均无订单效果

## 1. 产品结果

Catalyst Agenda 回答的不是“最近有什么新闻”，而是：

> 在一个明确的未来窗口内，我持有、关注或已建立研究档案的标的，有哪些已知事件
> 值得提前准备；日期有多确定，来自哪里，上次何时核验，事件发生后需要回看什么判断？

Judgment Scorecard 随后回答：

> 当时写下的假设、失效条件和计划，在可追溯事实到来后分别发生了什么？

两者都只组织事实和校准结果。系统不自动评价用户“好/坏”，不把日程条目当预测，不确认
Thesis，不修改 Trade Plan，也不产生订单。

## 2. 为什么不能直接复用现有 Event

现有 `ResearchEvent.occurred_at`、SEC filing、新闻、公司行动和 Monitor Event 都表示已经发生
或已经观察到的事实。未来财报日、宏观发布日期和用户计划催化剂是“预定/预期事项”，时间
可能变更或取消。把它们写成已发生 Event 会破坏时间语义和 as-of 审计。

因此新增独立 `CatalystAgendaItem`，并在事件实际发生后通过显式关联指向不可变的
`ResearchEvent`/Report/Evidence。计划记录和事实记录不互相覆盖。

## 3. 数据契约

### 3.1 CatalystAgendaItem identity

每个逻辑事项拥有稳定 identity 和 append-only version：

- `agenda_item_id`、`version`、`supersedes_version`；
- `instrument_id` 与 `subject_id` 通常至少有一个；只有 `MACRO_RELEASE`/`POLICY`
  可使用显式 GLOBAL scope；
- `kind`：`EARNINGS`、`FILING`、`DIVIDEND`、`CORPORATE_ACTION`、`INVESTOR_EVENT`、
  `MACRO_RELEASE`、`POLICY`、`INDUSTRY`、`USER_DEFINED`；
- `title`、可选 `fiscal_period` 或上游稳定 event key；
- `window_start`、`window_end`、原始 timezone；点时间用相同起止表示；
- `date_certainty`：`CONFIRMED`、`ESTIMATED`、`RANGE`、`UNKNOWN`；
- `status`：`UPCOMING`、`OCCURRED`、`CANCELLED`、`SUPERSEDED`；
- `source_vendor`、`source_reference`、`source_visible_at`、`last_verified_at`；
- `expected_question`：事件后需要验证的原判断，允许为空但不得由 Provider/LLM 自动生成；
- `linked_event_id`、`linked_report_id`、`linked_evidence_id`：只在事实已落库后设置；
- `outcome_occurred_at` 与 `outcome_note`：区分事实发生时间、人工完成关联时间与修订原因；
- `created_by`、`confirmed_by`、`idempotency_key`、`recorded_at`。

上游改期创建新 version；旧日期保留。不得原地更新，也不得按“symbol + date”去重。优先使用
上游 event id；缺少稳定 id 时使用 `instrument + kind + fiscal_period` 形成逻辑 key，并把
不确定性显式返回。

### 3.2 Scope projection

Agenda 查询在读取时把事项与 durable scope 合并，不把 scope 复制进事项：

- `PORTFOLIO`：最新 durable account snapshot 中存在该 instrument；
- `WATCHLIST`：当前 durable Watchlist membership 中存在该 instrument；
- `SUBJECT`：非归档研究档案以该 instrument 为 primary；
- `EXPLICIT`：调用方明确指定 instrument/subject。

同一事项可同时属于多个 scope，只返回一条并携带全部 `scope_reasons`。普通查询不刷新账户、
Watchlist 或 Provider。

### 3.3 时间和可信度

- 默认窗口：从查询 `as_of` 起 30 天；最大 180 天；
- `source_visible_at <= as_of`，否则不得进入 point-in-time 结果；
- current-only 源必须标注 `historical_vintage=false`；
- 已过 `window_end` 但未链接事实的事项返回 `AGENDA_EVENT_OUTCOME_UNVERIFIED`，不得自动改成
  `OCCURRED`；
- 日期过久未核验返回 `AGENDA_DATE_REVERIFY_REQUIRED`；
- 无事项不等于无催化剂。每个 scope instrument 返回 coverage，未覆盖时明确
  `AGENDA_COVERAGE_UNAVAILABLE`。

## 4. MCP 与 CLI 形态

不增加工具数量：

1. `research_memory_get(request={"operation":"agenda", ...})`
   - durable-only；按窗口、scope、instrument、subject、kind、status 查询；
   - 返回 items、每标的 coverage、分页和 limitation；
   - 不隐式访问 Yahoo、FRED、巨潮或券商。
2. `research_memory_append(request={"operation":"agenda_item", ...})`
   - 创建/修订/取消用户确认事项，或 append-only 关联/修订结果事实；沿用 confirmer、
     expected-version、idempotency 和 actor gate；
   - 当前聊天中用户明确授权时可由 Codex 转交，规则与 Journal/Decision 相同；
   - 不用于伪造 Provider 事实。
3. `uv run trading-partner-catalyst-sync`
   - 显式、确定性的 Provider 刷新；可供 launchd/Automation 调用；
   - 先从 durable scope 选标的，再批量/限流获取；
   - 每次产生 sync receipt，记录范围、成功/失败、日期变更和 coverage，不调用 LLM。

## 5. 免费 Provider 顺序

### 5.1 第一批

| 事项 | 免费源 | 口径与限制 |
|---|---|---|
| 美股财报、分红、拆股 | yfinance `Ticker.calendar` / `Calendars` | current-only Yahoo 聚合数据；日期可能是区间或估计值，不是交易所/公司正式日历；必须保留 `date_certainty` 与核验时间 |
| 美国宏观发布日 | FRED `release/dates` | 官方免费 API；请求 future date 时显式使用 `include_release_dates_with_no_data=true`；FRED release 日期不等于所有指标的精确发布时间 |
| 用户已知事件 | confirmed append | 最可靠的意图/准备事项，但来源类型必须是 `USER_CONFIRMED`，不得包装为外部事实 |

yfinance 官方项目文档列出 `Ticker.calendar` 以及 earnings/economic/split calendar；FRED 官方
API 明确说明默认会排除尚无数据的未来 release date，必须显式包含。实现时固定解析 fixture，
禁止依赖 pandas/raw dict 穿越 infrastructure 边界。

### 5.2 后续验证后再接

- A 股预约披露：巨潮公告能证明预约披露日期存在，但尚未找到稳定公开 API 契约；在 live
  fixture、频率和变更语义验证前，C1 只允许用户确认事项，不抓网页拼日期。
- SEC：EDGAR 适合已提交 filing，不提供公司 earnings release 的统一未来日历；不得根据法定
  截止日伪造公司发布日期。
- 韩国市场：Yahoo KR calendar 先做 live contract 验证；在覆盖/时区未通过前保持 limitation。

## 6. 分阶段实现

### S0 — 当前判断基线（已实现）

- 以一条明确 Thesis 的当前最新 revision 为输入，生成 append-only、不可变的维度卡；
- 确定性检查 revision 定义、exact-revision evidence、当前失效状态、Trade Plan/Monitor
  覆盖、行动意图时序和已有 Trade Retro findings；
- 无 assumption-to-evidence 关联或历史失效事件时明确返回 `NOT_EVALUATED`/`PARTIAL`；
- Console 可选择 Research Subject 与 Thesis、生成并浏览历史；
- 复用 `research_workflow_run/judgment_scorecard` 与
  `research_judgment_get/scorecard_history`，不增加公共工具；
- 不接受任意历史 `as_of`，未来校准只比较已经持久化的不可变 Scorecard run。

### C0 — 契约与迁移（已实现）

- domain enums/models、append-only migration、repository/UoW；
- owner/actor/idempotency/as-of invariant；
- `research_memory_get/append` 增加 closed operation，不增加工具；
- Console 暂不做新页面。

退出：手工事项可在对话中确认创建，未来窗口可 durable-only 查询，改期保留历史。

### C1 — Scope 与 coverage（已实现）

- 合并最新 durable Portfolio、Watchlist、active 研究档案；
- 去重 `scope_reasons`，返回无覆盖标的；
- 已过期未核验事项显式报警；
- Console 首页只增加未来 7 天摘要和 coverage 缺口。

退出：即使没有 Provider，系统也能诚实说明“哪些标的有日程、哪些没有可靠日程”。

### C2 — 免费同步（已实现）

- yfinance 美股 calendar adapter 复用 Provider Router 的 `CORPORATE_ACTIONS` admission/cache；
- FRED 日历复用既有 `MACRO` admission/cache；不新增专用 Provider category/TTL；
- FRED release date adapter；
- `trading-partner-catalyst-sync`、sync receipt、失败类型和 Data Quality Center 汇总；
- 不把查询失败解释为“无事件”。

退出：一条显式 CLI 可刷新未来 30 天的美股财报与选定宏观发布，并能审计日期漂移。

### C3 — 事实闭环与通知（已实现）

- 发生后显式链接 ResearchEvent/Report/Evidence，并保存发生时间与人工 outcome note；
- OCCURRED 结果允许以新 version 修订关联，不覆盖历史版本；
- 可选 Telegram 每日 agenda 摘要使用 durable Outbox；改期/取消只在状态变化时通知；
- Console 从 durable timeline/search 提供候选事实，也保留直接 ID 输入；
- 验证 A 股预约披露源后再接入；无稳定契约则继续保留 limitation。

退出：计划事项、实际事实和复盘材料可追溯，且不重复 Monitor 价格规则通知。

### S1 — Judgment Scorecard 事件校准升级（已实现）

S0 已经提供当前可证明的纪律基线；下列事件结果能力必须晚于 C3：

- 输入锁定为当时可见的 Thesis/Trade Plan/Monitor/Agenda version；
- 只统计可机器核验项目：假设得到支持/反证/未评估、失效规则触发与恢复、证据更新时间、
  计划条件是否在行动意图前存在；
- 原始事实缺失时返回 `NOT_EVALUATED`；
- 不生成单一“投资水平分数”，首版只输出维度卡和 calibration history。

## 7. 精简 TDD

不建立 Provider × 市场 × 状态的全排列：

1. domain invariant：版本、时间窗、source visibility、状态转换；
2. repository：append/idempotency/改期历史/迁移 roundtrip；
3. projection：scope 去重、as-of、coverage、过期未核验；
4. 每个 Provider 各 2–3 个 frozen contract fixture：成功、日期区间/改期、typed failure；
5. compact schema/eval：新 operation 可发现、跨 operation 字段被拒绝、工具总数仍为 28；
6. 一个真实只读 smoke，只记录 event 数量、warning code 和来源，不打印个人 scope。

最终 Agenda、Provider sync、notification 与 Scorecard 四个聚焦文件合计 31 个测试；Router、
admission、cache 与 generic Outbox 的既有矩阵均直接复用，没有复制 Provider × 状态全排列。

## 8. 明确不做

- 把新闻搜索结果自动变成未来事件；
- 根据历史财报间隔猜一个“确定日期”；
- runtime LLM 自动生成 `expected_question`；
- 自动创建/确认 Thesis、Trade Plan 或 Monitor；
- 新增公共 MCP 工具；
- 在没有稳定免费契约时抓取 A 股网页或引入付费日历。

## 9. 外部参考

- [yfinance Ticker calendar](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.html)
- [yfinance Calendars](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Calendars.html)
- [FRED release dates](https://fred.stlouisfed.org/docs/api/fred/release_dates.html)
