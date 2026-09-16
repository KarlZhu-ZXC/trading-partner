# Trading Partner product roadmap

This roadmap contains future work only. The implemented product boundary lives in
the [current product specification](../product.md); shipped changes live in release
notes.

## Direction

Trading Partner remains a local-first, long-horizon investment judgment companion.
New work must strengthen the loop from Observation through reviewed judgment,
portfolio evidence, and later calibration. It is not becoming a generic market
chatbot, broker terminal, or autonomous trading system.

## Evolution proposal and evidence

本轮方向更新：2026-09-14。以下是拟议工作，不表示已经实现，也不授权自动模型调用、
新增付费数据订阅、研究确认或交易。当前实现仍以 [product.md](../product.md) 为准。

### Research scope

本轮查阅项目官方 README、版本说明和可见仓库页面。GitHub Trending 周榜未能读取，
匿名 API 也未返回可用统计，因此没有可信的周增 Star 排名或统一提交活跃度排名。
下表 Star 是查询时页面显示的约数，只表示累计关注度；“trending 候选”不等于已验证
上榜，更不证明投资效果。没有安装或运行这些外部项目。

| 参考项目 | 可核实的产品信号 | 对本项目的取舍 |
|---|---|---|
| [TradingAgents](https://github.com/TauricResearch/TradingAgents)，约 105.4k | [v0.4.0](https://github.com/TauricResearch/TradingAgents/releases/tag/v0.4.0) 修正 point-in-time、决策记忆、价格依据及恢复问题 | 借鉴时点隔离与反方审查；不引入第二套常驻多 Agent 运行时 |
| [AI Hedge Fund](https://github.com/virattt/ai-hedge-fund)，约 63.4k | 当前 README 描述持久化 fund mandate、周期记录和回测；always-on/live 仍属演进愿景，当前声明不实际交易 | 借鉴明确研究任务与可追溯运行记录；不复制自动基金、回测和执行路线 |
| [Dexter](https://github.com/virattt/dexter)，约 27.6k | 任务分解、工具研究、循环限制和财务问答评估；数据能力依赖配置的 API | 借鉴有边界的研究过程和评估；不能把模型自检当作事实验证 |
| [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot) | README 描述确定性估值与模型叙述分离、证据化报告；明确区分开源 V0/V1 与未开源 V2 | 借鉴估值假设台账与数值来源；采用前核实具体版本源码和数据许可 |
| [ValueCell](https://github.com/ValueCell-ai/valuecell)，约 11k | 财务文档研究、定时新闻、桌面体验；同时包含加密合约交易能力 | 借鉴跟踪体验；不引入合约交易、A2A 网络或额外通道 |
| [OpenBB](https://github.com/OpenBB-finance/OpenBB)，约 73k | ODP 将数据服务于 Python、REST、MCP 等；Workspace 是独立企业 UI | 借鉴同一事实供多个界面使用；保留现有 Provider Router，不整体替换 |
| [Wealthfolio](https://github.com/wealthfolio/wealthfolio)，约 8.9k | 本地优先的组合体验；自动券商及设备同步属于可选 Connect 服务 | 借鉴组合证据可读性；不扩张到消费记账和净资产管理 |
| [FinceptTerminal](https://github.com/Fincept-Corporation/FinceptTerminal) | 桌面金融终端；免费 AGPL 版本与私有 Enterprise 在数据、AI 和交易能力上有区分 | 参考工作区组织；不照搬全功能终端或把商业功能当作开源能力 |
| [global-stock-data](https://github.com/simonlin1212/global-stock-data)，约 1.6k | 为 AI 提供 US 数据入口与来源分级，包含 SEC/FINRA/CBOE 类数据 | 仅作为来源发现线索；逐源验证时点、许可、稳定性，不整体接入 |

这些来源支持的观察是：可持续的投资研究工具需要任务状态、确定性数值、可追溯证据、
交互工作区和评估。将它们组合为下述路线是本项目的产品判断，不是外部项目的效果证明。

### Product direction

让 Trading Partner 成为“持续维护个人投资判断的研究工作台”：用户每次回来都能回答：
**什么变了、影响原判断哪一条、还缺什么证据、是否需要我复查、上次判断后来怎样。**

已有 Observation revisions、Thesis/Plan/Decision、ReviewItem、Agenda、Scorecard、Agent
receipts 和图表是基础；不把这些已有能力再次列为待开发。新增价值集中在它们之间的
证据关联、可读呈现和可验证质量。Moomoo 继续拥有主要笔记编写体验。

## Delivered foundation and remaining evolution

A–D 的首版已交付，当前边界见 [产品说明](../product.md) 与
[图表、估值和校准流程](../guide/research-chart-valuation.md)。后续方向只在实际复查问题
证明增量价值后扩展：

- 持久化、版本化用户画线；当前会话画线已覆盖首版需求。
- SMC Breaker Blocks、displacement、交易时段结构与更细扫掠类型；不把兼容性测试
  解释为收益预测验证。
- 普通经营公司以外的估值方法、DCF、显式拆股调整与更完整股本口径；当前只支持
  来源明确的年度稀释 EPS/P/E 情景，不把估值假设当事实。
- 明确事前概率和事件定义之后的概率校准，以及存在充分因果证据时的交易归因。
  当前未知状态不以股价上涨或有无交易代替。
- 更广泛的真实问题验证与可读性改进；合成门禁和小样本模型测试不能证明投资效果。

保留 [解释核验的边界](../operations/known-issues.md)。沿用免费事实路线；不增加通用
新闻流、同质化 Agent、自动基金、独立移动客户端或未经授权的付费数据订阅。

## Deferred integrations

- Additional brokers and A-share account execution feeds.
- Cross-currency consolidated performance until timestamped FX coverage and policy
  are defined.
- Licensed LBMA/LME benchmarks, complete expired-futures history, and research-grade
  back-adjusted continuous futures.
- Stable future-event providers for A-shares.
- StockTwits runtime access; historical stored values remain readable only.

Deferred means unsupported. It does not authorize approximation through a different
Provider or identity.

## Non-goals

- Korean-market expansion is excluded from future plans, not deferred. Existing
  implemented support does not imply a commitment to further coverage.
- Automated backtesting, paper-trading engines, or parameter optimization inside the
  current product.
- Autonomous Thesis, Trade Plan, Candidate, position, or order decisions.
- General unattended orders beyond the installed SGOV cash-sweep exception.
- Order replacement, options/complex orders, short selling, or Schwab overnight
  execution.
- A runtime that scrapes arbitrary websites after a formal Provider fails.
- A general-purpose social network, news terminal, tax engine, or accounting system.

## Advancement gates

Start a new capability only when:

- a repeated owner workflow demonstrates the product need;
- the data source and identity have a testable contract;
- timestamps, basis, freshness, degradation, and fallback semantics are defined;
- writes have explicit authority, versioning, and idempotency;
- the capability fits an existing intent or justifies a compatibility migration;
- focused tests cover the new invariant;
- the product specification, relevant guide, release note, roadmap, and agent
  instructions are updated without duplicating the same contract.
