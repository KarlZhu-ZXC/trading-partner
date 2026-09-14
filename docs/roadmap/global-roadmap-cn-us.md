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

## Ordered delivery slices

下列验收门槛是拟定目标，不是当前已测得的成绩。按 A → B → C → D 顺序推进；
每片先在现有契约上给出可检查的最小结果，再决定是否扩展。工作量为相对规模，不是工期承诺。

### A — 变化与原判断同屏（P0，中）

- 用户结果：打开一份 Research Subject，看到自上次明确复查以来的新 Observation 或
  已保存事实、关联的精确 Thesis 假设/失效条件、旧值与新值及来源时间。
- 复用：Observation review/current、Research context/timeline、Monitor observations、
  Agenda 与现有 ReviewItem。基线必须来自用户完成的精确复查，不能取最近一次模型聊天。
- 增量：增加统一变化投影与 Subject 的“本次变化”视图。仅将可确定验证的关系标作匹配；
  模型推断的相关性单列为草稿，缺少关系时显示未关联。首版不增加后台抓取或自动模型调度。
- 通知沿用既有 Monitor/Outbox，展示实际受影响标的、规则与时间；数据恢复不等于买卖信号。
- 验收：同一 source revision 不重复；旧版本仍可复原；源读取失败不自动消除复查；
  混合时点和来源差异可见；无变化时不产生额外通知；确认前不改正式判断。
- 衡量：有效变化条目中具备精确来源/版本的比例、重复条目数、用户查明变化所需操作数。

### B — 有预算且可查证的研究过程（P0/P1，大）

- 依赖 A 的版本和证据标识；复用现有 Agent 工具、6 轮默认上限、恢复与成本/时延回执，
  不增加 LangGraph/TradingAgents 等第二套模型运行时。
- 增量：显式研究请求展示少量研究问题、已完成步骤、证据缺口和停止原因；每个关键
  数值可回到具体事实。公开网页可作背景，不能覆盖规范化行情/账户事实。
- 基础模式只运行一次主分析；有明确冲突或用户请求时才运行一次有界反方审查。
  比较的是论点与证据，不默认调度多个“名人投资者”互相投票。
- 展示模型调用数、输入/输出 token、耗时；费用缺价格表时显示未知，不估算为零。
  预算限制工具轮次、墙钟时间与总调用数，不覆盖 Provider 既有协议/输出预算规则。
- 在现有行为评估中补 30–50 个脱敏/合成用例：时点污染、数值/来源错配、缺数据、冲突、
  用户授权、假突破、重复恢复和过时记忆。财务计算与时点由程序判定；模型裁判只评文字质量。
- 验收：越权写入、未来信息泄漏、编造价格/持仓在固定门禁集上为零；关键数值引用覆盖
  100%；超预算能够停止并保存已有证据；恢复不重复已完成的有副作用操作。
- 衡量：相同用例/模型配置下引用正确率、任务完成率、P50/P95 时延和 token 用量。
  反方模式若没有改善证据缺陷发现率，默认关闭，而非增加角色。

### C — 图表成为研究的一部分（P1，中）

- 复用已经完成的 KLineChart、tp_technical_v3 和 tp_smc_v1；不重做指标库。
- 增量：从 Research/Monitor 直接进入同一标的图表；点击 SMC 结构可以查看发生时间、
  确认时间、状态、算法版本和原始 K 线区间。图层与已有正式 Plan 条件并列且明确区分。
- 图表动作只能预填用户可编辑的 Thesis/Plan 草稿；新增跨页面数据传递必须绑定精确
  Instrument、数据版本和复权基础，仍走已有提议/确认流程。
- 首版维持绘图 session-only。只有用户确有反复复用画线的需求，再设计版本化本地绘图保存。
- 验收：更换标的/周期后不串线；未确认 pivot 不提前出现；历史区间不显示未来确认结构；
  切换复权基础时旧标注不当作有效价位；用户画线与算法图层在视觉和数据上均可区分。
- SMC 核心计算兼容性复评已完成；收益预测验证并未完成。Breaker Blocks、displacement、
  交易时段结构及更细扫掠类型继续延期，直到一个明确复查问题证明其增量价值。

### D — 估值假设与判断校准（P2，大，分两小片）

1. **估值假设台账**：先选一种有稳定输入的 US 普通经营公司估值方法，复用 SEC-first
   statements 与 Research Subject；新增版本化假设和确定性计算结果，提供敏感性分析。
   区分报告事实、用户假设、模型草稿；保留单位、币种、股本和发布时间。银行、保险等不适用
   的公司明确不支持，不套用一份通用 DCF。来源/许可不足则显示缺口，不先采购数据。
2. **已复查判断的校准**：复用 Decision、Agenda outcome、Scorecard、Trade Retro 与 ReviewItem，
   分别展示事实预测是否发生、条件是否触及、用户是否按计划行动、交易结果是否可归因。
   不新增第二套交易收益引擎，也不把某段股价上涨当作整个 Thesis 正确。

- 依赖 B 的来源验证；估值首片先确定公式、适用域和输入契约，再写 UI。
- 验收：相同输入重算一致；假设改动不覆盖旧版本；缺参数不自动填值；股本/企业价值与
  股权价值口径一致；无成交的 NO_ACTION 仍可复查；未到观察期不提前判输赢。
- 衡量：可复现估值覆盖、到期复查完成率、被证据纠正的假设比例；不以预测胜率或模型信心
  作为唯一产品指标。概率校准只有在事前概率与事件定义被明确保存后才进入范围。

## Investment of effort and stop rules

优先 A 的单标的完整切片，加上 B 的必要证据回归门禁；再增强 B 的研究过程，随后交付 C。
D 必须等到用户已经在 A/B 中反复遇到估值或复盘缺口。建议资源顺序为证据质量与连续性、
研究效率、图表衔接、估值/校准；不按外部项目 Star 排名决定工作量。

不新增通用新闻流、更多同质化 Agent、模型排行榜、自动基金、独立移动客户端或韩国市场
扩展。沿用免费事实路线；付费数据仅在具体问题、覆盖收益和成本获明确确认后才评估接入。
借鉴工作流不等于复制代码；任何直接依赖或代码采用都先核对目标版本许可和维护成本。

## Deferred integrations

- Broader Korea coverage: DART, company research, news/sentiment, broker accounts,
  catalyst data, and Position Sizing.
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
