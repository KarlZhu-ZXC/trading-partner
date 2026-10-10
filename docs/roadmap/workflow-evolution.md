# Trading Partner 工作流演进提案

本提案规划如何把已有投资判断能力连接成省力、可恢复、可追溯的日常工作流。建议下一轮产品投入集中在审阅完成语义、材料交接、计划变更后的跟进和复盘反馈。衡量进展应看用户是否更容易完成一次判断，以及后来能否解释这次判断。

提案日期：2026-10-02。状态：后续演进规划；W01–W11 的实现边界见 [当前产品说明](../product.md) 和 [Unreleased](../releases/unreleased.md)。下文工期、目标值和设计均为建议，不表示已经交付，也不授权运行生产迁移、私有内容模型调用、券商刷新或订单。

## 阅读范围和依据

- 以本地当前工作区为基线，包括尚未提交的证据关联、研究问题、Decision Replay、能力使用统计及架构改进。应用版本为 0.7.0，源码声明的迁移 head 为 `0073_research_evidence_links`；本轮没有核对已安装运行时版本。
- 当前能力以 [product.md](../product.md) 为准。本提案细化 [全局路线图](global-roadmap-cn-us.md) 中的工作流可靠性方向；原有市场范围、排除项和外部数据准入条件继续适用。
- 本轮阅读了产品契约、核心服务、Console 入口、测试与评估代码，并检查独立合成预览中的 Today、Quick Review 和 Evidence 页面。静态代码发现、预览观察和产品推论在下文分别标明。
- 市场对标覆盖 18 个代表性开源项目与产品，采用官方仓库和官方文档。不声称穷尽全市场，也没有建立可比较的活跃度或 Star 排名。没有安装第三方系统、验证其收益或测试其实盘适配器。
- 没有读取真实账户、笔记、密钥或生产数据库，没有运行模型、刷新券商、迁移数据库或执行订单。市场审视阶段没有重跑项目全套测试；后续 W01–W11 的实现与验证边界见当前产品说明和 Unreleased。

## 建议的产品选择

Trading Partner 应继续服务于长期投资者的持续判断：在 Moomoo 形成观察，在 Trading Partner 查看变化、审查证据、记录判断、维护计划，并在后续复盘中检验过程。Moomoo 保持主要分析和笔记编写入口。

用户每次回来，应能迅速回答六件事：

1. 哪件事值得我现在看，原因是什么？
2. 它与我上一次确认的判断有什么关系？
3. 哪些材料支持、挑战或暂时无法解释这个变化？
4. 我这次决定维持、调整，还是继续补证？
5. 本次已经完成什么，还有什么没有处理？
6. 到下次复盘时，我能否还原当时依据，并检查后续行动？

建议近期约 60% 的研发容量用于日常审阅和模块交接，25% 用于可靠性及端到端评估，15% 用于消除已证实的阅读与操作摩擦。该比例是容量分配建议，应在首轮测量后调整。

保留已有模块化单体、共享能力 Registry、Provider Router、Candidate 生命周期和精确订单授权。新增工作优先落在现有服务的协调和呈现上，不另建通用工作流平台、第二套 Agent 框架或独立判断数据库。

## 从当前实现推导出的改进优先级

以下列的是后续工作依据，不替代当前产品说明。P0 表示应先验证或补齐关键阻塞，P1 表示直接改善核心闭环，P2 表示在前置工作稳定后推进。

| 优先级 | 当前证据 | 流程影响和建议 |
|---|---|---|
| P2 | 专属 Observation review 要求 FULL revision，且解释结果必须为 SUCCEEDED。[external_note_review_service.py](../../src/application/services/external_note_review_service.py) 第 56–63 行 | 代码事实。模型失败或未授权时，专属引导链受阻。已有独立人工 Decision 入口；后续只有在实际需求证明必要时，才设计人工审核直接结束来源待办的契约，不能伪造解释成功。 |
| P1 | Evidence Impact 已接入 Changes/Replay，Scorecard 的相关维度仍只读取 EvidenceAssessment。[research_changes_service.py](../../src/application/services/research_changes_service.py) 第 117 行；[judgment_scorecard_service.py](../../src/application/services/judgment_scorecard_service.py) 第 248–254 行 | 代码事实。仅走新链接流程的用户可能看到旧口径缺口。先并列披露两种证据口径和 coverage，避免把“旧评估缺失”说成“没有证据”；不能直接合并计分。 |
| P1 | 当前 Scorecard 已按 exact Plan version 检查 Monitor 覆盖。[judgment_scorecard_service.py](../../src/application/services/judgment_scorecard_service.py) 第 513 行 | 流程建议。将已有覆盖检查前移到 Plan 修订后的下一步，展示旧 Monitor 与新 Plan 的差异。旧规则继续保留自己的含义，不自动重绑或关闭。 |
| P1 | Trade Retro 已有 prepare/run/review，weekly CLI 已组合上期运行、下期准备和 Behavior Review。[trade_retro.py](../../src/interfaces/cli/trade_retro.py) 第 155、173、204 行 | 代码事实加待验证假设。需要测量周内新 Plan 对事前证据覆盖的影响，不需要再造 weekly runner。逐笔归因必须依据成交前存在的精确记录。 |
| P1 | 实模型评估入口明确不验证语义正确性；现有四类合成案例主要检查字段、上下文与边界。[copilot_research_live_evaluation.py](../../src/interfaces/cli/copilot_research_live_evaluation.py) 第 1、49 行 | 已有评估基础，但不能证明解释理解正确。建立完整任务留出集，分开评价事实引用、语义、遗漏反证和流程完成。 |
| P2 | capability usage 仅保留每日能力/操作/结果/耗时聚合。[capability_usage.py](../../src/application/ports/capability_usage.py) 第 38 行 | 代码事实。它适合诊断，不能表示判断闭环完成。流程指标应从业务记录派生，不能把诊断 sidecar 变成第二份业务账本。 |
| P2 | Research 的 read 调用目前不进入普通聊天的并行 read 分支。[agent_runtime_service.py](../../src/application/services/agent_runtime_service.py) 第 1316 行 | 代码事实，不代表已经测得性能瓶颈。先分解耗时，再评估预算内独立读取的有界并行；不靠无限加大模型预算解决体验问题。 |

已有 Today/Weekly、Quick Review、Changes Since Review、问题模板、Evidence Comparison、Calibration、Replay、Trade Retro、Scorecard 都应复用。把这些重新列为待新增功能，会掩盖真正的交接问题。

## 市场项目对标及取舍

表中第二列概括官方资料；第三列是针对 Trading Partner 的设计推论。引用日期为本提案日期，实施时应再次固定相关版本或 commit。

| 参考项目 | 官方资料中的主要工作流 | 应借鉴的机制与边界 |
|---|---|---|
| [TradingAgents](https://github.com/TauricResearch/TradingAgents) | 分析师报告、多空讨论、风险审查、组合决策，保留运行配置与 checkpoint | 让主张、反证、分歧和中间产物可检查；不把多个 Agent 的共识视作用户确认。 |
| [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) | 当前 README 描述基金配置、下一周期预览、审批、paper session 与持续 ledger；明确不实际交易 | 借鉴“预览本轮变化→明确提交→查看连续历史”。模拟账本不能充当真实账户；愿景文档与当前实现应分开。 |
| [Dexter](https://github.com/virattt/dexter) | 问题分解、工具研究、自检与步骤/循环限制 | 借鉴围绕具体问题的有界研究及停止条件。模型自检不是外部事实验证。 |
| [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot) | 当前 README 描述数据、分析、确定性估值、讨论、报告的可追溯过程，并区分 V0/V1/V2/V3 | 借鉴确定性计算与模型叙述分离。当前 README 已将 V2 列为开源，不能沿用较早“V2 未开源”的结论；生产成熟度仍是项目自述。 |
| [ValueCell](https://github.com/ValueCell-ai/valuecell/blob/main/README.md) | 文档研究、定时新闻和交易 Agent；README 同时包含加密合约自动执行 | 参考持续研究任务的呈现与跟踪；不引入通用新闻流、A2A 网络或合约交易。 |
| [OpenBB](https://docs.openbb.co/odp) | 统一数据入口供 Python、REST、MCP 等使用；[OBBject](https://docs.openbb.co/odp/python/concepts/obbject) 携带结果和元数据 | 证据在多入口传递时保留 provider、warning、时间与口径；不替换已存在的 Provider Router。 |
| [Microsoft Qlib](https://github.com/microsoft/qlib) | 数据准备、模型任务、训练预测、分析；[Recorder](https://github.com/microsoft/qlib/blob/main/docs/component/recorder.rst) 管理参数、指标和产物 | 借鉴同一冻结输入下的运行比较、失败阶段和产物管理；不建设训练/回测平台，也不盲目收集环境变量。 |
| [FinRL](https://github.com/AI4Finance-Foundation/FinRL) | 数据处理、训练与后续评估时间分段，再到 [FinRL-Meta](https://github.com/AI4Finance-Foundation/FinRL-Meta) 所述 paper 等验证环节 | 借鉴开发集与未见验收集隔离；不引入强化学习策略、奖励函数或市场仿真。 |
| [QuantConnect LEAN](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview) | Universe、Alpha、Portfolio、Risk、Execution 分工 | 判断、意图、风险约束和执行分别交接。复用 TP 的 Subject/Thesis/Plan/Sizing，不另造竞争对象体系。 |
| [NautilusTrader](https://nautilustrader.io/docs/latest/concepts/live/) | 恢复、连接、[对账](https://nautilustrader.io/docs/latest/concepts/execution/reconciliation/)，然后才开始运行 | 借鉴可解释的恢复与 UNKNOWN 处理。结果未知只能核验，不能自动重试提交。 |
| [Freqtrade](https://www.freqtrade.io/en/stable/strategy-101/) | 策略、回测、前视检查、dry run、live，明确模拟与实时差异 | 借鉴新规则的影子验证和时间泄漏检查；验证的是判断提示，不扩展为本地交易模拟器。 |
| [Hummingbot](https://hummingbot.org/strategies/) | Controller 持续观察，Executor 处理有界任务 | 将 Monitor 检测与用户处理分工，明确一次处理何时结束；不复制自动执行器。 |
| [vn.py](https://www.vnpy.com/docs/cn/community/app/cta_strategy.html) | 策略初始化、启动、停止、变量保存与恢复 | 借鉴可理解的就绪检查和生命周期。其停止策略与撤单行为不能替代 TP 的独立撤单授权。 |
| [TradingView](https://www.tradingview.com/support/solutions/43000739708-watchlist-alerts-your-trading-edge/) | Watchlist 条件按标的独立追踪，列表变化更新适用范围 | 借鉴规模化监控时的范围提示和单条状态；不要把同一 Instrument 的不同 Research Subject 合成一个判断。 |
| [TrendSpider](https://help.trendspider.com/kb/trading-bots/trading-bots) | 先建立策略，再配置 Bot、核验、管理运行 | 借鉴下一阶段前的就绪核验和回看入口；不照搬 webhook 自动交易。 |
| [Tradervue](https://www.tradervue.com/site/trading-analysis) | 导入交易、标签分类、分组比较、风险和表现报告 | 借鉴同口径 cohort 比较，把复盘结论转回下一轮行为；不以短期盈亏替代长期判断质量。 |
| [Portfolio Performance](https://help.portfolio-performance.info/en/getting-started/measure-performance/) | 账户、现金流与持仓数据形成明确期间的绩效报表 | 借鉴期间、现金流和收益口径解释；跨币种不完整时不强行输出总收益。 |
| [Wealthfolio](https://wealthfolio.app/docs/introduction/) | 本地账户、交易活动、持仓、表现和收入；可选 Connect 同步 | 借鉴本地数据的导入、核对和阅读体验；自动同步是额外服务，不等于免费核心能力。TP 无需扩成家庭财务系统。 |

共同可借鉴的是输入可追溯、阶段有出口、失败能恢复、结果可复核。不同产品的目标差异很大，不适合比较功能数量或据此推断投资收益。

## 目标工作流

以下是六条用户旅程，允许跳过不适用步骤。没有交易意图的研究可以在 NO_ACTION 结束；证据明确的人工判断不必先调用 AI；一个宏观研究档案也不必强行绑定股票。

### 工作流一 建立第一个可复查的研究档案

**触发：** 首次使用，或用户带来一条新观察。

1. 选择已有 Observation 或使用已授权的显式来源同步。说明本次读取来源、覆盖方式、时间和失败状态。
2. 对已有 Instrument/Research Subject 做匹配建议，由用户确认归属；允许“暂未归属”，避免同一股票被误并入错误研究范围。
3. 查看原始观察及可用解释。没有模型解释时仍可阅读，人工路径显式标明其材料范围。
4. 用户选择保留为观察、继续研究、建立/修改 Thesis，或记录独立 Decision。复用现有 Candidate 和确认流程。
5. 只有有执行意图时才建立 Plan；仅需跟踪时选择可观察条件或明确下次人工复查时间。
6. 结束页显示正式记录、仍待处理来源和下次触发条件。

**完成定义：** 至少形成明确 disposition，或形成带问题/证据要求/下一复查点的待办；“创建了 Subject”本身不算首次闭环完成。

**失败恢复：** 来源为 SUMMARY_ONLY 时只能处理元数据或安排补充，不能进入正文解释/adoption；身份歧义保持未归属；没有模型授权不发出模型请求。

### 工作流二 日常变化到一次明确判断

**触发：** Today Review 的新笔记、Monitor transition、到期问题、Agenda 或用户主动复查。

1. 首页先显示触发原因、最近事实时间、覆盖缺口和上次处理结果。严重数据问题与投资变化分开，但均可见。
2. 用户选定一个 Research Subject 和本次处理范围。多 Subject 的 Instrument 必须分别保留上下文。
3. 打开 Quick Review：上次 Decision、精确 Thesis/Plan、这次变化、已有反证和账户快照时间在相邻位置呈现。
4. 用户选择维持、调整、补证或暂缓。保留理由草稿及既有幂等恢复。
5. “维持”保存本次 NO_ACTION；“调整”进入既有编辑与确认；“补证”建立或更新既有 OpenQuestion；逐条 Observation 是否采纳由其精确流程单独处理。
6. 展示完成回执：写入了哪些记录、哪些步骤未执行、哪些来源仍待审、何时再看。

**完成定义：** 本次用户选择的范围有明确结果；不要求清空所有提醒。不将页面展示、展开、停留时间或滚动推定为“用户已审核”。

**失败恢复：** 材料/版本变化时保留文本并显示需要重新比较的内容；响应丢失先查询原幂等键回执；已成功 Decision 后另一步失败，不能重放整条链。

### 工作流三 一个研究问题到可用证据

**触发：** 具体问题，例如“订单增长是否已经转化为现金流”，不是泛泛的“分析一下”。

1. 复用 OpenQuestion，记录问题版本、证据要求、截止时间和需要检验的 Thesis assumption；模板仅作为可编辑起点。
2. 用户选择人工调查或显式启动 Research。启动前显示所需数据来源、可用缓存、缺口、模型模式及预算。
3. 研究只收集所选问题需要的材料；来源、期间、单位、事实/存储时间和不可用原因随字段保留。
4. 输出事实、推断、缺口，并给出最强反证与尚未解决分歧。若只有支持材料，解释检索范围，不能宣称不存在反证。
5. 用户决定是否建立精确 Evidence Impact、修改问题答案或改变 Thesis。Impact 仅可选择已有 durable Evidence、FULL Observation revision 或 evaluated Monitor exact observation；当前回合 Provider/Web 引用通过校验不等于已成为这种持久来源。每种改变保留自己的确认。
6. 问题完成后可连接后续同 Subject Decision；未完成则说明下一份必要证据和复查条件。

**完成定义：** 问题获得符合用户证据要求的已审答案，或明确保留缺口；产出长报告不等于完成。

**失败恢复：** 超时/预算中止保留已完成步骤和事实回执，恢复不会重新确认动作；问题或 Thesis 版本改变时结果保留原关联，不能自动搬到新版本。

### 工作流四 判断或计划改变后的持续维护

**触发：** 用户确认 Thesis revision 或 Plan 新版本。

1. 展示这次具体改了什么，以及可能需要复核的 Plan、Monitor、OpenQuestion 和复盘基线。
2. 分清三种情况：精确引用仍有效、旧版本仍有历史含义但不能代表新版本、需要用户明确迁移/终止。
3. 若用户要执行，检查 ACTIVE Plan、失效时间、币种、价格时效、现金/NAV、数量规则等，复用 deterministic sizing/risk。
4. 将可监控条件与 MANUAL 条件列清；为旧 Monitor 提供修改差异草案，仍逐个确认，不自动重绑。
5. 让用户选择完成、暂不执行或保留待复核步骤。没有交易意图时不要求填写仓位数量或进入订单页。

**完成定义：** 所有受影响依赖都有准确状态；尚未维护的依赖可见，不以自动级联伪造完成。

**失败恢复：** Plan 提交成功而 Monitor 修改失败时保留前者，后者从原步骤继续；新版 Plan 不得继承旧版 Monitor 的覆盖证明。

### 工作流五 从执行意图到事实对账

**触发：** 用户明确进入受支持的执行流程，或查看已入库的券商活动。

1. 引用精确 Decision、Plan 与 sizing/risk 结果，展示缺口和时效。
2. 生成独立订单 preview，由用户对精确且未过期内容授权 submit/cancel。研究确认不能代替该授权。
3. 分别呈现未发出、拒绝、结果未知、已确认、部分成交等实际状态；不把模型回答当成订单事实。
4. 结果未知时仅提供安全的状态核验/对账步骤。需要刷新券商数据时仍走显式 operational gate。
5. 对账后的活动连接 Trade Cycle。无法证明 Decision/Plan 关联的活动保持未关联，由用户按现有机制处理。
6. 保留事前材料覆盖和缺失原因，供 Retro 使用。

**完成定义：** 可以说明执行结果和记录关联状态；“点击提交”不是完成，“出现相同股票和相近时间”不是归因证明。

**失败恢复：** UNKNOWN 不自动重试，重启不恢复旧授权，迟到成交或人工券商操作有单独处理路径。SGOV 已安装调度器继续遵守自己的封闭契约。

### 工作流六 周复盘到下一轮行为改进

**触发：** 用户打开 Weekly Review，或检查已有 weekly Retro 结果。

1. 先显示本周范围、数据完整性、未解决项和事前材料覆盖。
2. 分别回顾判断依据、计划遵守、执行事实及市场结果；NO_ACTION 同样可回放。
3. 复用 Calibration/Replay 对照当时已知材料，不把后来修订的事实投射回过去。
4. 从 Retro finding 中由用户最多选择 1–3 个值得跟进的问题；可以选择已有问题覆盖、建立提醒或无需跟进，不能自动制造任务。
5. 每个接受的跟进项说明原 finding、需要改变的过程、下次检查样本和复查时间；复用 Question/ReviewItem 等对象。
6. 下次复盘显示执行证据及同类问题是否重复出现。若样本不足，就保留“尚不能评价”。

**完成定义：** 本周结论有可追踪的后续动作，或有明确无需动作的理由；下一轮能检查是否执行。单一总分或一张盈亏图不能代替这一出口。

## 必须分开的状态和记录

| 语义 | 能证明什么 | 不能推断什么 |
|---|---|---|
| 材料已准备/已呈现 | 指定 cutoff 下服务器准备或界面显示过哪些引用 | 用户已阅读、同意或采纳 |
| 用户明确选定审阅范围 | 本次确认对应哪些对象和版本 | 未选择的来源也处理完成 |
| Candidate 已确认 | 精确被审阅的研究修改已生效 | 其他候选、订单或整条工作流已完成 |
| Decision / NO_ACTION 已记录 | 用户本次判断及引用、理由、后续时间 | 每条 Observation 已 adoption、所有提醒已关闭 |
| 来源处理已完成 | exact revision 达到其正式 disposition | 下一次 revision 无需再审 |
| 订单结果已确认 | 独立 broker 契约所证明的执行事实 | 判断正确、执行归因完整 |
| 本轮覆盖完整 | 指定范围和截止时间内的所有必需源已完成扫描 | 整个市场无变化、所有历史资料完备 |

建议将“本次审阅材料”先实现为有界 DTO 与回执投影，复用已有 immutable records 和 audit。只在历史重建确实缺字段时追加最小持久化引用，避免为所有页面行为建表。

拟议最小材料引用包括：Subject、触发来源、baseline Decision、Thesis/Plan versions、source IDs/revisions、多个 account snapshot IDs、数据 cutoff、coverage、所用规则/解释版本。`prepared_at` 与 `presented_at` 不冒充 `reviewed_at`；用户明确提交才产生授权记录。hash 只能证明内容一致性，不能证明事实正确或用户已读。

展示材料、用户选定材料、正式采纳材料应明确分开。若只展示资料，通常不需要持久化点击轨迹；业务完成回执必须由正式记录派生。既有诊断 sidecar 继续保持无业务内容。

多步骤交接不是一个跨系统原子事务。只对当前 UoW 内的提交承诺原子性；其余显示逐步结果、原幂等键和安全继续位置。完成回执必须支持“部分成功”。

## 分阶段路线图

工期假设：一位主要架构与验收负责人，配合受控执行代理；用户每周能对代表性合成流程作一次验收。下表按后续 8 个有效开发周排布，建议另留 2–4 周缓冲。它不是发布承诺；每两周根据完整性、返工和流程耗时重估。若为业余投入，按实际可用人日顺延，不压缩确认与验证。

| 阶段 | 建议窗口 | 用户可感知的成果 | 主要依赖 | 退出条件 |
|---|---|---|---|---|
| M2 按问题推进研究 | 第 1–2 周 | 从缺口进入有限研究，回到同一问题和 exact assumption | M1 的材料引用；既有 OpenQuestion/Research | 不同版本不误关联；答案不越权；中止有可继续位置 |
| M3 维护判断后的依赖 | 第 3–4 周 | 修改 Plan 后知道哪些 Monitor、手工条件和执行检查还未完成 | M1；既有 invariants/Sizing | exact-version 覆盖准确；部分成功可恢复；订单仍独立授权 |
| M4 让复盘产生后续改变 | 第 5–6 周 | 上次改进项在本周可检查，证据口径清楚 | M1/M2；既有 Retro/Calibration/Replay | 所选跟进项有证据链；旧新证据口径不混算；历史缺口不伪补 |
| M5 总体验收与收敛 | 第 7–8 周 | 一套稳定的日常、事件、变更、执行和周复盘流程 | 前述各阶段 | 留出场景、恢复演练、跨入口一致性通过；试用确有收益或据证据缩减 |

M2 与 M3 可依托已实现的审阅接口部分并行。M4 的证据口径披露可提前做。可靠性测试与合成试用从 M0 开始持续进行，M5 负责总验收，不是首次测试。

M0/M1 对应的 W01–W08 工程基础已纳入产品与验收测试；后续仍需以真实使用任务测量省时效果，不将工程测试通过当作投资效果验证。

### M2 的后续验证

问题版本、只读研究启动、结束说明和合法来源草稿交接已实现，使用方式见
[证据与研究问题指南](../guide/evidence-links-and-research-questions.md)。后续仍需针对
期间错配、因果误读和关键反证遗漏建立人工语义评分，验证真实问题的研究质量；
工程门禁通过不代表模型已正确回答投资问题。

### M3 的后续验证

W12–W14 的依赖复核、精确版本覆盖、执行就绪与对账入口已进入当前产品契约，
见 [操作指南](../guide/research-chart-valuation.md#plan-dependency-review-and-execution-readiness)。
后续留出验收仍需覆盖真实使用中用户是否理解局部成功、手工条件和执行归因的区别；
合成数据检查不能替代这项人工验证。

### M4 的后续验证

W15–W17 的双口径披露、事前覆盖测量与明确后续问题选择已进入
[当前操作指南](../guide/evidence-links-and-research-questions.md#scorecard-evidence-cohorts-and-retro-follow-up)。
W18 已将选定跟进的到期、版本变化、来源缺口及人工复核依据接入既有 Retro 页面，
详见当前操作指南。后续仍需人工验证过程是否改善，保留 NO_ACTION 与真实交易的不同分母。不能仅凭问题已关闭、盈利或事后补填
文字就认定事前流程改善；这仍需要留出集和用户自愿的人工验收。

### M5 的具体任务

1. W19 的合成自动化检查已覆盖事实、操作、授权和恢复；冻结的五维语义案例仍需独立人工逐例验收，不能用自动化通过率替代。
2. 验证 Console、MCP、Copilot 对同一业务输入的权限和版本规则一致；保留各传输的完整/compact 差异。
3. 执行模型不可用、预算中止、源缺失、版本冲突、写成功响应丢失、重启、续约失败等故障场景。
4. 对合成日常/事件/周复盘任务做前后比较；用户自愿试用真实资料时，仅在本地保留业务记录。私有内容模型使用、生产升级和券商刷新另按既有授权。
5. 若 Research 耗时仍是主要阻碍，再测试有界并行，原子预留预算并保留取消、顺序和 cutoff 语义。
6. 删除无实际收益的提示/步骤，更新产品说明、操作指南和 release note；已交付部分从本路线图移出，保留未来方向。

## 可以直接拆分的实施任务

每行应作为独立、可验收的变更，不要求一行对应一个大 PR。文件是职责落点，不表示必须修改所有列出的模块。依赖中的 W01–W18 指已实现基础，当前行为以产品说明为准。

| 编号 | 交付切片 | 主要落点 | 验收重点 | 依赖 |
|---|---|---|---|---|
| W19 | 剩余独立语义与实际体验验收 | 现有 evaluators、contract tests、e2e | 质量维度独立，权限/版本无退化 | M0 起持续 |
| W20 | 按测量优化 Research 延迟 | AgentRuntime/ModelExecution/provider admission | 改善耗时且不扩大预算/权限 | W19 的可比基线 |

架构与验收由主负责人掌握；实现前需确定文件归属、输入输出、命名和验收标准。共享工作区已有改动需保留。

## 指标和验收方法

北极星建议：**需要处理的变化中，有多少能在用户选择的时限内形成可追溯的判断或明确补证安排，并在到期后得到检验。** 不追求决策条数、工具调用数、报告长度或交易频率增加。

| 指标 | 定义和排除规则 | 建议初始目标 |
|---|---|---|
| 安全约束 | 未授权有效写、重复有效提交、跨 Subject/版本误确认、UNKNOWN 自动重试 | 关键验收场景必须为 0；任一出现即阻止发布 |
| 审阅耗时 | 同类任务中用户主动操作时间，模型/Provider 等待单列；同时报告中位数、P95、样本量 | 基线后争取中位数降低 30%；提案目标，非已测结果 |
| 材料可追溯率 | 新审阅中，契约要求的 exact refs、cutoff、coverage 均可还原的比例 | 新流程关键 fixture 100%；历史缺口独立披露 |
| 恢复正确性 | 中断后能回到安全步骤且无重复写的场景数/全部中断场景 | 关键恢复场景全部通过 |
| 覆盖完整性 | 指定 scope/window 的逐源扫描完成情况 | 未扫全只能 PARTIAL；不得显示全量完成 |
| 重复打扰 | 同一未变化 source occurrence 被重复要求执行相同处理的比例 | 基线后降低；新 revision/状态变化单独统计，不能靠隐藏积压优化 |
| 计划观察覆盖 | current Plan 的可监控条件中，exact version 已配置且有有效观察的比例；manual 单列 | 无数据时不出满覆盖，所有缺口可定位 |
| 复盘跟进闭环 | 用户接受且到期的跟进项中，有合格执行证据或明确无需继续理由的比例 | 每个已关闭项均可追溯；不强迫用户接受更多任务 |
| 研究语义质量 | 在未见案例中按期间/范围/因果/反证/信息不足分别评分 | 关键错误不能被平均分掩盖；初期人工逐例评审 |
| 资源耗费 | 每个研究任务的已报告 tokens、calls、耗时和完整性 | 成本无定价则 unknown；不把缩短答案当质量改善 |

初期用户样本很小，应报告原始任务数、paired 场景和失败实例，不做显著性或普遍投资效果声明。没有完整时间记录就先观察记录，不把不存在的计时当 0。用户操作时间测量优先用合成任务的本地观察，不收集私密正文和细粒度点击轨迹。

### 必须覆盖的场景

1. 新 FULL 笔记，无模型授权，人工仍可阅读和形成独立判断。
2. SUMMARY_ONLY 笔记，不得进入正文解释或正式 adoption。
3. 同一 Instrument 下两个 Subject，不串判断和待办。
4. 相同笔记新 revision 再出现，不被旧处理结果永久压制。
5. 一次 Quick Review 之后仍有未采纳来源，完成页准确说明。
6. 多账户快照时间不同，不能伪造单一“当前组合”。
7. Source 缺失、超 cap 或扫描期间变化，不误显示清空。
8. 反证来自不同期间/币种，模型不能仅因字段匹配就宣称结论成立。
9. 研究预算耗尽，已完成证据可见，判断不自动晋级。
10. 用户审阅期间 Thesis/Question/Plan 被更新，旧确认被拒且草稿保留。
11. Decision 写入成功但响应丢失，查询回执后不重复写。
12. 多步骤第二步失败，第一步成功状态保留且可解释。
13. Plan v2 与 Monitor v1 并存，不冒充新版覆盖。
14. 缺 NAV/现金/stop/basis/有效报价时，Sizing 保持 incomplete。
15. 订单提交结果未知，只能核验，不能重试。
16. Job heartbeat、finish、SQLite busy、进程 kill 的终态可恢复。
17. 复盘期间有新 Plan，不能用事后材料评价此前交易纪律。
18. NO_ACTION 可复盘；无成交不能当失败。
19. 仅有 Evidence Impact 而没有旧 Assessment，不表述为完全无证据。
20. 历史问题状态缺失，不从今天的答案重建过去。

### 测试层次

- Domain/Application：先测新增状态约束、材料引用、cutoff、局部事务和幂等；不为只读呈现复制实现细节测试。
- Integration：使用 disposable SQLite 验证分页扫描、部分源失败、故障恢复、追加记录和 migration。不得对安装运行时执行迁移。
- Console：同一合成任务贯穿入口、错误、返回、刷新和恢复；覆盖键盘操作、窄屏和横向 tabs 的可达性。
- MCP/Agent：验证严格 `$ref` schema、closed operations、原确认规则、跨入口相同语义；MCP compact 不能决定 Console 对象是否存在。
- Model：先 scripted 和 synthetic，再在明确选择的模型/effort 下做允许的合成 live 验收；人工语义评分独立。私有内容进入模型需要单独既有授权。
- 最后由主负责人根据变更范围安排 backend checkpoint；CI 继续负责 coverage、packaging、安全等 release matrix。当前提案本身只做文档检查。

## 后续三到六个月的条件性方向

只有前述闭环形成稳定使用习惯，才考虑以下方向：

| 方向 | 进入条件 | 最小交付 | 不应提前做的部分 |
|---|---|---|---|
| 组合层面的共同风险复查 | 多个 Subject 的相同暴露反复造成实际复查负担 | 在现有 portfolio concern、Risk 和快照上提出有来源的联合复查视图 | 自动最优组合、自动再平衡、跨币种收益近似 |
| 更丰富的确定性估值 | 现有 EPS/P/E 不能回答高频实际问题，且来源/股本/拆股口径明确 | 单一受支持公司类型、明确假设、敏感性、版本化保存 | 一次覆盖全部估值法、把模型预测当事实 |
| 新证据源适配 | 用户反复手动补同一类证据，且授权/身份/时间/许可可测试 | 一个来源的完整导入、变更检测、失效处理和审阅出口 | 任意网页抓取、自动语义 ingestion、无边界新闻流 |
| 概率校准 | 用户已事前定义事件、期限和概率，有可判定结果及足够样本 | 对明确定义事件的透明统计 | 用置信度文本反推概率、以涨跌代替事件结果 |
| 更便捷的移动访问 | 现有响应式流程仍有证实的使用阻碍 | 完善已有 Console 窄屏复查和只读访问 | 独立移动客户端、多设备业务冲突体系 |

新增市场、券商和跨币种绩效仍按全局路线图的来源与政策 gate 决定。韩国市场进一步扩张继续排除。当前产品不引入本地自动回测、自治交易、复杂订单、通用社交或税务记账。

## 风险及停止条件

- 流程编排变成重复状态机：先用现有业务记录和 query projection；只有证明缺失的恢复/历史字段才持久化。一个“workflow complete”标志不能凌驾于源对象状态。
- 首页变成更多任务的集合：用户可暂缓、不适用、无需动作；新材料或明确期限才重新唤起。不能通过默认自动关闭提高指标。
- 过度流程化增加审阅成本：用户可直接依据事实判断，问题、模型和证据 link 均按需使用。若新流程比基线更慢且无完整性收益，缩减步骤。
- 模型验证被夸大：字段可匹配不代表理解正确；语义验收、人工判断和事后结果三者分开。
- 把生产试用当成研发默认权限：优先合成 fixture；实际配置、私有内容、broker refresh 和 runtime migration 继续走各自原有边界。
- 迁移与回滚不清：新表/字段只在必要时引入，先验证 disposable migration；上线保留可验证备份，不能以危险 downgrade 假装回滚。
- 计划过宽：每阶段最多推进一条主要旅程。连续两次验收未改善完成度或耗时，暂停扩功能，处理根因或撤回设计。

下一步完成 W19 的独立语义与真实体验验收；自动化结果与人工待验收维度已分开。W20 仅在可比测量显示 Research 延迟仍是主要阻碍时推进，不为新增并行机制而扩展预算或权限。
