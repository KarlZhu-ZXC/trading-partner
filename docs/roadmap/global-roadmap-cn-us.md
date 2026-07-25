# Trading Partner 总体设计与全局路线图  
> 文档位置：`docs/roadmap/`
## A 股参考 a-stock-data，美股参考 TradingAgents

> 文档版本：Global v8
> 最终交互：Codex 长期会话  
> 研究接口：Trading Partner MCP  
> 执行接口：独立 Execution MCP  
> A 股参考：`simonlin1212/a-stock-data`  
> 美股参考：`TauricResearch/TradingAgents` v0.3.1  
> Phase 1 不包含回测与交易写入

---

# 0. 当前执行目标（2026-07-21）

以下事项记录当前优先队列、已完成基础和明确放弃项：

- [ ] **Phase 3A 正式期货与跨资产行情**
  - [x] 免费连续金属期货 foundation：Yahoo `GC=F`、`MGC=F`、`SI=F`、
    `HG=F`、`PL=F`、`PA=F` 支持 quote、1m–1mo bars 与日/周技术分析；新浪仅提供
    GC/SI/HG 带时间戳 quote fallback，东方财富仅提供六种金属日线及周/月聚合。
  - [ ] 正式期货统一接入：具体合约、到期日、乘数、交易时段、结算、持仓量、合约链、
    期限结构与受控换月；金属期货和 DCE 生猪期货使用同一模型。
  - [ ] OTC/LME：XAUUSD、XAGUSD、铜现货、LME Cash/LME 3M 与期现基差；不得由
    `GC=F`、`SI=F` 或 `HG=F` 冒充。数据必须披露 bid/ask/mid、单位、币种、时间戳、
    session 和 venue/aggregate basis。
  Alpha Vantage 免费额度和 close-only 历史不足以担任主源，只保留为合规的低频
  supplemental/fallback；真实 key 只保存在本地 `.env`。
- [x] **Phase 3B 跨市场公司财务/经营事实与可选行业数据集**
  A 股/美股三表、确定性财务质量指标、巨潮公司经营披露、全国畜牧总站猪周期数据与历史
  发布版本已接入。股票 Deep Dive 自动组合标准化财报；行业数据集按需显式选择，不作为
  所有行业的通用前提。长期官方行业历史按 best effort 延伸，不设固定 20 年 gate。
- **StockTwits 已退出当前路线图（2026-07-25）**：不再把正式接入作为 Phase 3
  Action Item、发布目标或退出门槛。现有默认关闭的 adapter 可暂时保留以避免在规划整理中
  混入代码删除；若未来没有受支持的官方访问方式，可在独立 breaking cleanup 中移除。
- [x] **Moomoo OpenD 美股 Hot List**
  已内化到现有 `market_get_context`，不新增 MCP 工具或独立 Skill；输出交易、搜索、新闻和
  综合热度，固定披露“注意力不等于多空情绪”，复用统一 OpenD 限流并按 15 分钟缓存。
  本机已迁移至由 macOS launchd 守护的 command-line OpenD 10.9.6908，并与 Python SDK
  同版本；Hot List、Watchlist 和只读账户目录均通过 live smoke。旧版仍以
  `MOOMOO_OPEND_VERSION_UNSUPPORTED` 局部降级。
- [x] **Moomoo 评论流固定 Provider**
  已内化进现有 `us_get_sentiment_snapshot`，不新增 MCP 工具或独立 Skill。固定 Provider
  调用当前 `stock_feed`，对语义检索结果执行精确 ticker 相关性过滤、HTML 清洗、去重、
  低质量过滤和 `moomoo_rules_v1` 中英确定性分类，并按标的缓存 15 分钟。当前响应缺少可靠
  互动量时保留 `null`，且明确披露仅为近期快照。MCP 运行时不调用 Skill 或 LLM，Codex 等
  外部交互层继续负责观点综合。NVDA live smoke 已验证相关性过滤、样本归一化和 warning。

作为 A 股情绪源参考，`a-stock-data` 当前优先巨潮互动易、同花顺热榜、东方财富人气榜及
东方财富个股概念命中；它未推荐匿名抓取雪球/股吧帖子作为稳定主源，且明确标注雪球深度数据
需要 token。Trading Partner 继续优先这些可审计的热榜/互动数据，而不是把社区帖子热度
伪装成方向性情绪。

阶段归属在实现设计冻结时确定；本节只记录当前执行优先级，不代表扩大交易或订单权限。

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
| Phase 3 | 跨资产、历史验证与模拟评估 | 正式期货/现货、公司财务经营、可选行业数据、历史验证、计划控制链、Paper 与归因 | 真实写入 |
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

# 10. Phase 3：跨资产、历史验证与模拟评估

## 10.1 合并后的能力域

Phase 3 不再按零散工具或单一品种拆分，而按共享领域模型与依赖关系分为五组：

| 能力域 | 范围 | 当前状态 |
|---|---|---|
| 3A 正式期货与跨资产行情 | 连续代理、正式合约、现货、基差、期限结构 | 连续金属期货基础已完成；正式合约与现货待实施 |
| 3B 公司财务/经营与可选行业数据 | A股/美股财报、财务质量、公司公告经营指标、按需行业数据、Deep Dive 组合 | 财报与猪周期主链路已完成，行业长期历史 best effort |
| 3C 历史验证平台 | 历史库、Strategy、Backtest、Experiments、Bias Checks、Artifacts | 待实施 |
| 3D 判断到计划控制链 | Monitoring v2、Trade Plan、Position Sizing、Risk v2 | Phase 2 基础已完成，扩展待实施 |
| 3E Paper 模拟与持续评估 | Paper Ledger、Attribution、Scheduler State、周期复盘 | 待实施 |

## 10.2 Phase 3A：正式期货与跨资产行情

当前 Yahoo 连续金属期货以及新浪 quote/东方财富日线 fallback 只是 foundation，固定披露
非现货与换月风险。下一步正式期货接入统一覆盖：

- 交易所、具体合约月份、到期/最后交易日、乘数、最小跳动、交易时段和结算口径；
- 成交量、持仓量、合约链、期限结构、主力判定和可控换月；
- 原始合约与连续序列的可追踪构造；
- 金属期货和大商所生猪期货使用同一领域模型与 Provider 接口。

生猪期货不再作为猪周期数据集的专用子模块。DCE 生猪只是正式期货接入的首批中国期货
品种之一；Phase 3B 通过标准期货事实消费它，不重复实现行情或期限结构。

同一能力域还包含 XAUUSD/XAGUSD/铜现货、LME Cash/LME 3M 和期现基差。Crypto 与普通
Forex 可以复用该模型，但在出现明确产品用例前不作为 Phase 3 退出门槛。

## 10.3 Phase 3B：公司财务/经营与可选行业数据

公司财务是通用核心：A 股通过 `a_share_get_facts(operation="financials")`，美股通过
`us_get_fundamentals(operation="statements")` 返回标准化三表与缺失输入安全的确定性质量
指标；股票 Deep Dive 自动组合该财务包。SEC 支持 latest/vintages，yfinance/Alpha Vantage
只作为 current-only fallback；A 股中报和季报明确保留累计/YTD 口径。

公司公告经营指标用于补充销量、售价、产能、成本等非标准化披露。猪周期只是首个按需行业
数据集，已经包含全国价格/饲料/猪粮比/周期性产能、历史发布版本和显式 Deep Dive 组合，
并不要求所有行业建立周期模型。剩余工作合并为两类：

- 同行比较与周期估值；
- 官方长期历史按 best effort 补充并持续披露缺口。

DCE 生猪期限结构属于 3A，不在此重复列为独立 Provider。

## 10.4 Phase 3C：历史验证平台

`historical_data`、`strategy_registry`、`backtest`、`experiments`、`metrics`、
`bias_checks` 和 `artifact_store` 合并建设，共享同一 point-in-time 与数据版本契约。
存储目标为 DuckDB + Parquet + dataset manifest。

A 股回测必须覆盖 T+1、100 股整数倍、涨跌停、停牌、ST、新股、板块差异、除权除息、
流动性、佣金与印花税；美股覆盖交易时段、碎股、Cash/Margin、PDT、拆股、分红、费用、
滑点与流动性。实验支持 Walk-forward、OOS、Event Study、Monte Carlo、Benchmark 与成本
敏感性，并检查 Look-ahead、Survivorship、Data Snooping、Filing/News/Adjusted Price
Leakage、样本不足和过拟合。

Strategy 是版本化实验说明，不是订单或 Thesis 确认。

## 10.5 Phase 3D：判断到计划控制链

Monitoring v2、Trade Plan、Position Sizing 与 Risk Engine v2 合并为一条确定性控制链：

```text
Current Thesis + verified facts
→ versioned Trade Plan
→ deterministic sizing range
→ expanded risk checks
→ monitorable conditions and invalidations
```

统一覆盖技术位/成交量/财务/SEC/A 股公告/研报预期/Insider/资金筹码/宏观/Sentiment/
Thesis 失效监控；入场、分批、退出、有效期和目标仓位计划；风险预算、ATR、波动率目标与
相关持仓约束；以及主题上限、回撤、流动性、事件、T+1、涨跌停、停牌、数据过期和重复订单。

所有结果仍是计划、区间或检查，不产生订单、成交或确认权限。

## 10.6 Phase 3E：Paper 模拟与持续评估

Paper Trading、Attribution、周期复盘和 Scheduler State 共享模拟账本与时钟，因此合并建设：

- 模拟账户、订单生命周期、部分成交、费用、滑点、净值与 Shadow Portfolio；
- 标的选择、择时、仓位、Beta、行业主题、汇率、成本、主观干预和计划遵守率归因；
- 幂等的每日持仓、每周组合和事后复盘运行；
- 策略、Agent 建议、Paper 结果与真实账户观察结果分源比较。

现有 CLI 和 Codex Automation 是外部调度入口，不等同于内部 Paper Engine 或 Scheduler State。
Paper 与真实券商永久隔离，真实订单写入仍属于 Phase 4 的独立 Execution MCP。

## 10.7 MCP 公共面原则

能力域不等于一项能力一个 MCP 工具。Phase 3 继续使用闭合 `operation`、聚合 coordinator 和
已有工具扩展公共面；`monitor_*`、`risk_check`、`portfolio_run_review` 不创建重复别名。
具体工具 schema 在各能力域设计冻结时确定，公共面保持紧凑且可审计。

## 10.8 出口门槛

- 正式期货与现货不会混淆，合约、连续、期现和基差口径可追踪；
- 回测可复现，数据版本可追踪且无明显历史泄漏；
- A 股和美股规则、费用与滑点正确；
- 至少 20 个基准策略验证；
- Paper 连续运行 8–12 周；
- 模拟账本、Risk Tests、监控重试去重和 Scheduler State 稳定；
- 研究、计划、模拟和真实账户清楚区分，无 Critical 风控绕过。

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
→ Cross-Asset Facts + Historical Validation + Plan Controls + Paper Evaluation
→ DuckDB/Parquet + Scheduler State
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
research_formal_cross_asset
validate_hypothesis_history
manage_judgment_to_plan_controls
simulate_paper_plan
evaluate_strategy_and_portfolio
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
正式期货/现货口径可靠；回测可复现且无明显泄漏；Paper 8–12 周稳定，
Risk、Scheduler State 和模拟账本可靠

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
