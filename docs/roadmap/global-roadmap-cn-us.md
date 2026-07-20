# Trading Partner 总体设计与全局路线图  
> 文档位置：`docs/roadmap/`
## A 股参考 a-stock-data，美股参考 TradingAgents

> 文档版本：Global v7
> 最终交互：Codex 长期会话  
> 研究接口：Trading Partner MCP  
> 执行接口：独立 Execution MCP  
> A 股参考：`simonlin1212/a-stock-data`  
> 美股参考：`TauricResearch/TradingAgents` v0.3.1  
> Phase 1 不包含回测与交易写入

---

# 1. 最终产品愿景

Trading Partner 最终不是：

- 股票数据 MCP 的集合
- 一次性多 Agent 研究工具
- 固定 Workflow Router
- 自动交易机器人
- 独立 Web Dashboard

最终形态是：

> **一个长期存在于 Codex 会话中的个人交易伙伴，能够持续理解投资主线，读取 A 股、美股和真实账户数据，形成并维护研究假设，验证策略，持续监控条件，生成交易计划，并在确定性风控和人工审批下执行有限操作。**

用户始终只面对一个 Codex Thread：

```text
自由研究对话
→ 当前事实查证
→ Thesis 沉淀
→ 历史验证
→ 组合分析
→ 持续监控
→ 交易计划
→ Paper Trading
→ 受控实盘
→ 复盘与学习
```

---

# 2. 两个参考项目的全局定位

## 2.1 A 股：a-stock-data

提供参考：

- A 股市场特色数据
- 多数据源端点
- 行情、财务、研报、资金和筹码
- 公告和互动易
- 涨停生态和短线情绪
- ETF 期权
- 东方财富限流和备用源

全局角色：

```text
A 股 Provider Reference
```

## 2.2 美股：TradingAgents

提供参考：

- Yahoo Finance / Alpha Vantage Vendor Registry
- FRED Macro
- Polymarket
- StockTwits / Reddit
- 数据日期安全
- Stale OHLCV Guard
- Verified Market Snapshot
- Structured Output
- Bull / Bear / Risk / Portfolio Research Workflow
- Checkpoint 和 Decision Log

全局角色：

```text
US Provider + Research Workflow Reference
```

## 2.3 不保留独立 TradingAgents 系统

TradingAgents 的能力被内化为：

```text
US Provider Patterns
Deep Research Workflow
Structured Research Schemas
Checkpoint Pattern
```

不保留：

```text
TradingAgents CLI
独立 TradingAgents Service
模拟交易出口
独立 Portfolio Manager 下单批准
Trader 直接交易语义
```

---

# 3. 最终系统架构

```text
┌─────────────────────────────────────────────────────────────┐
│                 Codex Trading Partner Thread                │
│                                                             │
│ 自由讨论 · 查证 · 研究 · 验证 · 监控 · 审批                 │
└─────────────────────────┬───────────────────────────────────┘
                          │
              ┌───────────┴────────────┐
              │                        │
              ▼                        ▼
    trading-partner-mcp       trading-execution-mcp
    研究、账户只读、回测       独立凭证、订单写入
              │                        │
              └───────────┬────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                 Trading Partner Platform                    │
│                                                             │
│ Instrument / Market / Fundamentals / Events / Sentiment     │
│ Account / Portfolio / Research State / Journal              │
│ Research Workflow / Backtest / Monitoring / Risk            │
│ Paper Broker / Execution / Attribution / Evaluation         │
│                                                             │
│ AShare Provider                 US Provider                  │
│ a-stock-data参考                TradingAgents参考            │
└───────────────┬──────────────────────┬──────────────────────┘
                │                      │
                ▼                      ▼
       Market & Broker APIs       Storage & Workers
                                 SQLite / DuckDB / Parquet
                                 Scheduler / Audit Ledger
```

---

# 4. 全局设计原则

## 4.1 一个会话，多种执行深度

```text
概念讨论
→ 不调用工具

当前事实
→ 原子工具

深度研究
→ Research Workflow

验证假设
→ Backtest Workflow

持续跟踪
→ Monitoring

准备交易
→ Trade Plan + Risk

真实执行
→ 独立 Execution MCP + 人工批准
```

## 4.2 对话、记忆、事实和执行分离

```text
Codex Thread
= 当前对话上下文

Research State
= 长期 Thesis、失效条件和待验证问题

Market / Account Provider
= 当前权威事实

Backtest Engine
= 历史验证

Risk Engine
= 确定性约束

Execution Gateway
= 真实订单唯一出口

Audit Ledger
= 审批与执行记录
```

## 4.3 LLM 与确定性程序职责分离

LLM 负责：

- 理解问题
- 解释证据
- Bull / Bear
- 风险批判
- 催化和预期差
- 机会成本
- 综合结论
- 提出待验证问题

程序负责：

- 数据读取
- 财务和指标计算
- 日期过滤
- 回测撮合
- 仓位计算
- 市场规则
- 风险限制
- 订单状态
- 审计和对账

## 4.4 市场特定 Provider，统一领域模型

```text
AShare Provider
US Provider
Broker Provider
```

向上输出同一套：

- Instrument
- Market Snapshot
- Fundamental Snapshot
- Event
- Evidence
- Portfolio Position
- Thesis

## 4.5 研究与执行永久隔离

真实交易开始后：

```text
trading-partner-mcp
= 研究、回测、账户读取、计划草案

trading-execution-mcp
= 订单预览、提交、撤单、对账
```

研究 MCP 永远不加载订单写入凭证。

---

# 5. 全局能力地图

```text
1. Instrument Master
2. AShare Provider
3. US Provider
4. Account Hub
5. Market and Macro Context
6. Fundamentals and Filings
7. Technical and Market Structure
8. News, Events and Sentiment
9. Research State and Journal
10. Deep Research Workflows
11. Portfolio Analytics
12. Strategy Registry
13. Backtest and Experiment Lab
14. Monitoring and Alerts
15. Trade Planning
16. Position Sizing
17. Deterministic Risk Engine
18. Paper Trading
19. Controlled Execution
20. Performance Attribution
21. Review and Evaluation
```

---

# 6. 阶段总览

| 阶段 | 定位 | 主要能力 | 明确不做 |
|---|---|---|---|
| Phase 0 | 技术骨架 | Codex、MCP、统一模型、Provider Interface | 完整业务 |
| Phase 1 | 只读研究伙伴 | A股、美股、账户、研究、组合、记忆 | 回测、模拟、下单 |
| Phase 2 | Watchlist + Risk + Monitoring + Technical v2 | 自选、只读风险、条件监控、A股/美股专业日周线技术分析 | 回测、模拟和实盘 |
| Phase 3 | 验证、监控扩展与模拟交易伙伴 | 策略、回测、实验、Monitoring 扩展、Trade Plan、Paper | 真实写入 |
| Phase 4 | 受控交易助手 | Execution MCP、人工批准、有限实盘 | 无人自主交易 |
| Phase 5 | 自适应投资系统 | 归因、评估、策略治理、个性化 | 默认自动实盘 |

---

# 7. Phase 0：基础骨架

目标：

- 验证 Codex 是长期交互界面
- 建立所有阶段共享的契约

交付：

- Python 仓库
- Project MCP Config
- AGENTS.md
- Trading Partner Skill
- Tool Envelope
- Typed Errors
- Provider Router
- Instrument Model
- SQLite Migration
- MCP Audit
- 固定两个上游参考版本

退出标准：

- MCP 稳定
- 工具错误明确
- Provider 与业务模块分离
- 状态重启可恢复
- 无交易写入

---

# 8. Phase 1：只读研究伙伴

详细实现见独立 Phase 1 文档。

## 8.1 A 股

参考 `a-stock-data`：

- 行情与盘口
- 财务和 F10
- 研报与一致预期
- 资金筹码和龙虎榜
- 解禁、分红和股东户数
- 公告和互动易
- 涨停生态和舆情
- ETF 期权只读

## 8.2 美股

参考 TradingAgents：

- Explicit Vendor Chain
- yfinance / Alpha Vantage
- FRED
- StockTwits / Reddit
- Polymarket 可选
- Stale Data Guard
- Date Cutoff
- Verified Market Snapshot
- Structured Sentiment
- Bull / Bear Research

Trading Partner 补齐：

- Broker 当前行情
- 真实账户
- SEC EDGAR
- 统一结构化模型
- 真实 Portfolio Analytics

## 8.3 Phase 1 Workflows

```text
deep_research
catalyst_review
a_share_market_review
us_market_review
portfolio_review
```

## 8.4 Phase 1 退出门槛

- A 股和美股 Provider 契约稳定
- 当前数据带时间、来源和新鲜度
- 历史日期研究无 Future Leakage
- SEC 和 A 股正式公告可追溯
- Optional Data 降级明确
- 账户只读
- 至少 80 条对话 Eval
- 无回测和交易写入代码

---

# 9. Phase 2：Watchlist Hub + Risk + Monitoring + Technical Engine v2

> 实施状态：已于 2026-07-18 完成；当前规范见 `docs/phases/phase2.md`。

## 9.1 产品目标

把“想继续观察、但未必值得建立 Investment Case”的标的变成稳定、可对话管理的研究入口。

典型交互：

```text
列出我的 Moomoo 半导体自选。
把 NVDA 加到 Moomoo 的 MAG 分组。
从 CSV 的“等待回调”分组删除这个标的。
这个自选为什么被加入？它是否已经值得建立 Investment Case？
```

## 9.2 三层职责

```text
Active Source（Moomoo 或 Manual CSV）
= 当前成员关系与分组的唯一上游管理入口

Trading Partner Watchlist Store
= 数据库中的完整分组、成员、来源、同步时间与生命周期历史

Trading Partner WatchlistItem
= thesis hint、触发条件、状态与 Investment Case 关联
```

删除 Moomoo/CSV 成员不得删除项目内的研究历史；项目内 WatchlistItem 归档也不得暗中修改
外部自选。Moomoo 与 Manual CSV 是二选一的上游来源，不做两个来源之间的聚合、差异比较或
双向同步。无论选择哪个来源，成功读取或写入后的 Watchlist 都必须持久化到项目数据库；数据库
同时保存外部成员关系和独立研究 metadata，使 Codex/App 重启或上游暂时不可用时仍可恢复最近状态。

## 9.3 Provider 集成

### Moomoo OpenD

- 读取自选分组与成员
- 向 `Favorites` 或自定义分组添加/删除标的
- 使用 Quote Context，不要求交易解锁
- 保留 OpenD 返回的代码、名称、类型与 provider time
- 系统派生分组只读；可写分组由明确策略限制

### Manual CSV

- 严格版本化 `watchlist.v1.csv`
- 最小字段：`format_version, group_name, market, asset_type, symbol, display_name`
- 唯一键：`group_name + market + asset_type + symbol`
- 允许用户直接编辑，也允许 MCP 添加/删除
- MCP 写入必须使用文件锁、临时文件和原子替换，不留下半写文件
- CSV 解析失败时 fail closed，不静默跳过坏行

## 9.4 统一语义

- 配置 `watchlist_source=MOOMOO | MANUAL_CSV`，同一运行实例只启用一个上游来源
- 数据库始终保存 `watchlist_groups` 与 `watchlist_memberships`；成员删除使用 inactive/removed time
  保留历史，不物理删除研究记录
- 普通读取从数据库恢复；显式 refresh 从当前 source 拉取并原子更新数据库，source 不可用时可返回
  最近一次持久化状态并明确 stale/degraded
- 添加/删除先写当前 source，成功后立即更新数据库；若外部成功而本地持久化失败，返回明确的
  partial failure，并允许后续 refresh 自愈
- 切换 source 不自动迁移数据；Manual CSV 是无法或不希望连接 Moomoo 时的替代入口，首次显式
  import/refresh 后写入数据库
- 添加/删除必须要求用户明确授权
- 操作必须幂等并保存脱敏审计回执
- 美股/A 股成员进入 Instrument Master 前 local-first 动态验证
- 暂不支持研究的 Moomoo 市场仍可列出，但必须标记 `research_supported=false`
- Watchlist 成员可以显式建立/关联 Investment Case，但不得自动启用长期跟踪

## 9.5 MCP

```text
watchlist_get  # operation=groups|items
watchlist_add
watchlist_remove
```

现有 `research_state_update/get` 继续管理项目内研究型 WatchlistItem；上述三个工具管理统一
Watchlist Hub，不复制 Candidate/Confirm 状态机。

## 9.6 出口门槛

- Moomoo 与 CSV adapter 分别满足同一读取、添加和删除契约
- 任一 source 的分组和成员均完整持久化到数据库，不丢 provider、group、时间和原始代码
- 写入必须显式授权、幂等且可审计
- CSV 手工编辑和 MCP 写入可互操作且不会损坏文件
- 外部删除不会级联删除 Research State 或 Investment Case
- Codex/App 重启或 source 暂时不可用后仍能恢复最近 Watchlist 与本地研究关联
- 不引入自动监控、回测、Paper Trading 或订单写入

## 9.7 Portfolio Risk Engine v1

把不依赖策略回测的确定性风险约束提前到 Phase 2B：版本化 Risk Policy、账户/价格时效、
按原币种单标的集中度、同币种且 NAV 可用时的 Gross Exposure/NAV、账户现金和融资比例，
以及跨账户重复持仓提示。支持基于当前持仓或一个假设新增仓位执行只读检查。

缺少 NAV、价格时间或 FX 事实时必须返回 `NOT_EVALUATED`/`INCOMPLETE`，不得通过隐式汇率
或持仓市值替代账户净值。系统默认阈值在用户确认前保持明确 warning。该引擎只有
`risk_policy_get`、`risk_policy_update`、`risk_check` 三个 MCP 工具，永远没有订单副作用。

## 9.8 Monitoring v1

> 实施状态：已于 2026-07-20 完成。

提供 append-only Monitor 版本、最新规则状态、状态变化事件、显式事件 resolution 和运行回执。
V1 规则限制为 A 股/美股价格上穿/下穿与 Portfolio Risk 总体状态阈值；事实过期或 Provider
失败为 `NOT_EVALUATED`。按需 MCP 与 `trading-partner-monitor-run` CLI 共用同一评估服务，
连续相同状态不会重复建事件，所有结果均无交易副作用。

## 9.9 Technical Engine v2

> 实施状态：已于 2026-07-20 完成。

把原有美股基础指标升级为 A 股/美股共用的日线与周线技术事实层。标准指标使用 TA-Lib，
结构位与状态分类由项目自有版本化算法完成；同一批调整后日线派生日线/周线，避免重复
Provider 请求。`technical_get_snapshot` 返回结构化事实，`technical_render_chart` 返回审计
envelope 与 PNG K线/量能/RSI 图。所有结果固定 `historically_validated=false`，不构成策略、
回测、交易信号或执行授权。

---

# 10. Phase 3：假设验证、监控与 Paper Trading

## 10.1 目标

把会话中的 Thesis 转为可复现的历史实验，再从一次性研究升级为持续跟踪和模拟执行。
Phase 2 已能保留但不能研究的 Crypto、Forex/贵金属与期货 Watchlist 标的，统一在本阶段
扩展数据、Instrument 与研究能力；这属于产品覆盖扩展，不作为 Phase 2 Watchlist 缺陷。

## 10.2 策略、历史数据与回测

新增 `strategy_registry`、`historical_data`、`backtest`、`experiments`、`metrics`、
`bias_checks` 和 `artifact_store`。

A 股回测必须覆盖 T+1、100 股整数倍、涨跌停、停牌、ST、新股、板块差异、除权除息、
流动性、佣金与印花税。美股回测必须覆盖交易时段、碎股配置、Cash/Margin、PDT、拆股、
分红、美元现金、费用、滑点与流动性。

实验支持单/多标的、参数扫描、Walk-forward、Out-of-sample、Market Regime、Event Study、
Monte Carlo、Benchmark 和成本敏感性。自动检查 Look-ahead、Survivorship、Data Snooping、
Future Function、Filing/News/Adjusted Price Leakage、不合理成交、样本不足与过拟合。

## 10.3 Monitoring extensions

在 Phase 2C v1 基础上扩展：

- 技术位
- 成交量
- 财务与 SEC
- A 股公告
- 研报预期
- Insider
- 资金和筹码
- 宏观
- Sentiment
- Thesis 失效条件

## 10.4 Trade Plan

包含：

- Thesis
- 时间周期
- 入场条件
- 分批规则
- 最大风险
- 止损和失效
- 退出条件
- 有效期
- 目标仓位区间
- 需要监控的数据

## 10.5 Position Sizing

- 固定比例
- 单笔风险预算
- ATR
- Volatility Targeting
- 分批建仓
- 网格草案
- 主题上限
- 相关持仓约束

## 10.6 Risk Engine extensions

在 Phase 2B v1 基础上增加需要历史数据、交易计划或订单状态的检查：

- 主题上限
- 回撤
- 流动性
- 财报和事件
- A 股 T+1
- 涨跌停和停牌
- 数据过期
- 重复订单

## 10.7 Paper Trading

- 模拟账户
- 订单状态
- 部分成交
- 费用和滑点
- 组合净值
- Shadow Portfolio
- 策略、Agent 和实际账户比较

## 10.8 Attribution

- 标的选择
- 择时
- 仓位
- Beta
- 行业和主题
- 汇率
- 成本
- 主观干预
- 计划遵守率

## 10.9 MCP

```text
strategy_create_from_thesis
strategy_get
backtest_run
backtest_compare
backtest_explain_trades
experiment_get
experiment_list
experiment_promote_result
monitor_create
monitor_query
trade_plan_create
trade_plan_validate
position_size_calculate
risk_check
paper_order_preview
paper_order_submit
paper_portfolio_get
performance_attribute
review_run_weekly
```

## 10.10 出口门槛

- 回测可复现、数据版本可追踪且无明显历史泄漏
- A 股和美股规则、费用与滑点正确
- 至少 20 个基准策略验证
- Paper 连续运行 8–12 周
- 账户和订单对账稳定
- Risk Tests 完整
- 监控具备重试和去重
- 无 Critical 风控绕过
- 研究、模拟和真实账户清楚区分

---

# 11. Phase 4：受控实盘

## 11.1 独立 Execution MCP

```text
trading_execution
```

特征：

- 独立进程
- 独立凭证
- 独立配置
- 独立审计
- 默认关闭

## 11.2 状态机

```text
DRAFT
→ PREVIEWED
→ RISK_CHECKED
→ AWAITING_APPROVAL
→ APPROVED
→ SUBMITTED
→ PARTIALLY_FILLED / FILLED / CANCELLED / REJECTED
```

## 11.3 强制控制

- Fresh Account
- Fresh Quote
- Trade Plan
- Risk Pass
- Order Preview
- Explicit Approval
- One-time Token
- Preview Expiry
- Idempotent Client Order ID
- 金额上限
- 仓位上限
- 报价偏离保护
- Broker Health
- Kill Switch
- Reconciliation

## 11.4 初期支持

- 白名单账户
- 白名单标的
- 小额限价单
- 正常交易时段
- 逐笔批准
- 撤单

暂不支持：

- 市价单
- 期权
- 卖空
- 杠杆
- 自动批量
- 无人值守

## 11.5 MCP

```text
order_preview
order_submit_approved
order_cancel
order_get_status
execution_reconcile
execution_kill_switch
```

---

# 12. Phase 5：自适应个人投资系统

目标不是自动下单，而是持续提高研究和决策质量。

能力：

- 每日、每周和每月复盘
- Thesis 命中率
- Agent 节点评分
- 数据源质量评分
- 策略适用环境
- 用户行为偏差
- 计划遵守率
- 风险预算建议
- 自动归档失效研究
- 主动提出待验证问题
- 多账户统一视图
- 工作流版本治理

自动化允许：

- 数据同步
- 快照
- 条件检查
- 报告
- 风险告警
- Paper Trading
- 候选状态更新

默认不允许：

- 自动真实下单
- 修改风险限制
- 扩大额度
- 绕过人工批准

---

# 13. 架构演进

## Phase 0–1

```text
Codex
→ trading-partner-mcp
→ 单 Python 进程
→ SQLite
→ Providers
```

## Phase 2

```text
Codex
→ trading-partner-mcp
→ Research + Watchlist Hub + Risk + Monitoring + Technical Engine
→ SQLite + Moomoo OpenD + Manual CSV
```

## Phase 3

```text
Codex
→ trading-partner-mcp
→ Research + Backtest + Paper
→ Scheduler Worker
```

## Phase 4–5

```text
Codex
├── trading-partner-mcp
└── trading-execution-mcp

Shared:
Storage
Scheduler
Audit Ledger
Provider Health
Monitoring
```

Phase 4 前不提前拆微服务。

---

# 14. 存储演进

| 阶段 | 存储 | 用途 |
|---|---|---|
| 0–1 | SQLite | 状态、缓存、快照、日志 |
| 2 | SQLite + Manual CSV | Watchlist 研究关联、分组和可迁移成员关系 |
| 3 | DuckDB + Parquet + Scheduler State | 历史、回测、实验、监控、Paper、归因 |
| 4 | Append-only Audit Ledger | 批准、订单、成交、对账 |
| 5 | 可选 PostgreSQL | 多进程和多账户 |

---

# 15. 仓库演进

Phase 0–3：

```text
trading-partner/
```

单仓库和主要 Python 包。

Phase 4：

```text
trading-execution/
```

单独拆分，原因：

- 凭证隔离
- 权限隔离
- 部署隔离
- 审计隔离
- 更小攻击面

---

# 16. 全局工作流

## Phase 1

```text
deep_research
catalyst_review
a_share_market_review
us_market_review
portfolio_review
```

## Phase 2

```text
review_watchlist
promote_watchlist_item_to_case
monitor_conditions
review_monitor_events
review_technical_snapshot
render_technical_chart
```

## Phase 3

```text
hypothesis_to_strategy
backtest_hypothesis
compare_experiments
stress_test_strategy
build_trade_plan
monitor_thesis
paper_execute_plan
daily_position_review
weekly_portfolio_review
post_trade_review
```

## Phase 4

```text
prepare_live_order
approve_and_submit_order
reconcile_execution
```

## Phase 5

```text
monthly_strategy_governance
agent_quality_review
behavior_bias_review
portfolio_risk_budget_review
```

工作流始终是后台能力，不是用户主要交互形式。

---

# 17. 阶段晋级门槛

```text
Phase 1 → Phase 2
数据、研究连续性、日期安全和记忆可靠

Phase 2 → Phase 3
Moomoo/CSV Watchlist 可恢复、可审计，外部成员关系与研究历史边界可靠

Phase 3 → Phase 4
回测可复现且无明显泄漏；Paper 8–12 周稳定，Risk 和 Reconciliation 可靠

Phase 4 → Phase 5
小额实盘稳定，无严重安全事件
```

未达到门槛不得提前晋级。

---

# 18. 周期建议

| 阶段 | 预计周期 |
|---|---:|
| Phase 0 | 2–4 天 |
| Phase 1 | 6–8 周 |
| Phase 2 | 1–2 周 |
| Phase 3 | 8–12 周开发 + 8–12 周观察 |
| Phase 4 | 3–5 周开发 + 灰度期 |
| Phase 5 | 持续演进 |

Phase 1 因加入：

- A 股全栈 Provider
- 美股 SEC
- TradingAgents 数据安全模式
- 多源 Sentiment
- 跨市场组合

周期从原 5–7 周调整为约 6–8 周。

---

# 19. 全局成功标准

1. 用户只需要一个 Codex Trading Partner Thread。
2. 系统能延续主线，而不是重复模板。
3. A 股和美股均有市场特定 Provider。
4. 当前事实可追溯到时间和来源。
5. 正式公告分别追溯到 A 股官方源和 SEC。
6. 历史研究无 Future Leakage。
7. 精确技术数值由 Verified Snapshot 提供。
8. Sentiment 与事实证据分离。
9. Thesis、回测、计划和结果串联。
10. A 股和美股规则不会混淆。
11. 回测可复现并识别过拟合。
12. Risk Engine 不可被 LLM 绕过。
13. Paper Trading 暴露真实执行问题。
14. 实盘逐笔人工批准并完整审计。
15. 系统可以发现用户长期决策偏差。
16. 自动化提高效率但不削弱控制权。

---

# 20. 许可与上游治理

`a-stock-data` 和 TradingAgents 均使用 Apache License 2.0。

仓库保存：

```text
references/
├── a-stock-data/
└── tradingagents/
```

每个参考目录包含：

- LICENSE
- VERSION
- UPSTREAM.md
- REFERENCE_NOTES.md

同步流程：

```text
检查上游
→ 阅读变更
→ 固定 commit
→ 隔离分支
→ Contract Tests
→ Date / Stale / Fallback Tests
→ 更新 Reference Notes
→ 合并
```

TradingAgents 只同步有价值的模式：

- Dataflows
- Vendor Errors
- Date Filtering
- Stale Guard
- Verified Snapshot
- Sentiment
- Structured Output
- Checkpoint

不机械同步整个项目。
