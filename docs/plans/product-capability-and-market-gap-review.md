# 产品能力清单与同类产品缺口审视

状态：2026-08-01 基线审视。当前 runtime 公共面为 `compact_28` / `compact-v11`。

## 1. 产品定位

Trading Partner 不是行情终端、券商前端或通用个人财务软件。它是一个 local-first、MCP-native
的长期投资判断伙伴：Codex 等 Host 负责对话、解释与综合，服务负责可追溯事实、持久研究记忆、
组合上下文、确定性风险检查和持续监控。任何精确事实都必须保留来源、事实时间、freshness、
口径与 typed warning；缺失不能由 LLM 补造。

## 2. 当前用户可感知能力

| 用户任务 | 已有能力 | 主要边界 |
|---|---|---|
| 跨会话继续研究 | 标的研究档案、当前投资判断、版本历史、Journal、Decision、统一时间线与 Context restore | Host 负责叙述；写入需要显式授权 |
| 挑战既有判断 | 反方优先 Context、十维 Challenge Review、候选 Propose → Confirm/Reject/Withdraw | Codex 不能替用户确认 |
| 找到并识别标的 | A 股、美股、韩股、ETF、指数、金属期货及 OTC 金银的 local-first 解析和唯一候选缓存 | Instrument Master 是缓存，不是白名单 |
| 查询市场事实 | 单标的/批量报价、K 线、综合市场事实、美股宽度/轮动、A 股结构/资金/热度、金属期货曲线和现货-期货基差 | 免费源可延迟或降级；不伪造缺项 |
| 做公司基本面研究 | 美股 SEC point-in-time 财报/filing/内部人/事件；A 股财报、公告、经营指标与研报搜索 | 韩股 DART 尚未接入；估值历史不能由当前值代替 |
| 研究宏观和情绪 | FRED/ALFRED、新闻、Reddit + Apify fallback、Moomoo 确定性讨论情绪、Polymarket 当前概率 | 来源分离；runtime 不调用 LLM；StockTwits 已退出范围 |
| 技术分析 | A 股/美股/韩股及已支持跨资产的日周指标、结构位、形态与 PNG 图 | `historically_validated=false`，不是预测或信号 |
| 读取真实组合 | Schwab、Moomoo 或 Manual CSV 持仓；显式同步、durable-only 日常读取；标准化交易活动和 coverage receipt | 不读写订单；不隐式刷新；不隐含 FX |
| 管理 Watchlist | Moomoo 或 Manual CSV 二选一，保留分组、成员生命周期和同步回执 | 不做双源 reconcile |
| 检查风险与仓位方案 | Exposure、simulate addition、Risk v2、版本化 Risk Policy、A 股/美股 Position Sizing | 缺 NAV/现金/FX/价格时必须 `INCOMPLETE` |
| 持续监控 | 价格及 v2 多类事实规则、统一 Dashboard、不可变 run、状态迁移 event、小时 dispatcher、Telegram | 只观察和通知；不会改 Thesis、仓位或订单 |
| 固化研究流程 | Deep Dive、Catalyst Review、中美市场复盘、Portfolio Review、同行比较 | MCP 返回事实包，Host 负责综合 |
| 验证策略 | 生成带 hash 的 LEAN 包并导入 QuantConnect Free Results JSON | 用户手工运行网页；无自动远程 runner |
| 运维和质量控制 | system health、Data Quality Center、Provider route 历史、Console、备份/retention、OAuth/通知诊断 | Console 仅 loopback；质量读取不触发 Provider |

## 3. 可比产品带来的启发

以下比较只采用公开产品/官方项目资料，检索时间为 2026-08-01；它们的目标用户和付费数据
范围不同，表格表示产品模式，不表示数据质量等价。

| 产品类型与代表 | 公开能力 | 对 Trading Partner 的启发 |
|---|---|---|
| [Portfolio Performance](https://www.portfolio-performance.info/en/) | 完整交易账本、TTWROR/IRR、费用税项、风险指标、基准、资产分类、目标配置与再平衡 | A1–A3 必须先把收益率和贡献勾稽做扎实，再做漂亮图表 |
| [Wealthfolio](https://wealthfolio.app/features/investments-tracking/) | local-first 多账户、CSV/券商同步、持仓穿透、TWR/IRR/波动/回撤、移动端与 AI/MCP | 保留 local-first 和 MCP 优势；补足移动端可读的组合绩效与收入视图 |
| [Ghostfolio](https://github.com/ghostfolio/ghostfolio) | 自托管、多账户、交易导入导出、组合风险、PWA 移动端 | 安装、首次导入、备份恢复和移动阅读应保持低摩擦 |
| [Koyfin](https://www.koyfin.com/features/) | 自定义 Dashboard/Watchlist、图表、组合报告，以及价格/估值/技术/新闻告警 | Monitor 需要统一管理、通知策略与可下钻上下文，而不只是规则执行器 |
| [TIKR](https://www.tikr.com/) | 全球财务、估值、分析师预期、筛选器、Transcript、filing/news 事件流和可保存估值模型 | 公司研究仍缺管理层讲话/Transcript、可版本化估值假设和研究候选漏斗 |
| [Finn](https://heyfinn.co/) | 按持仓/Watchlist/Thesis 过滤 filing、财报、新闻和管理层评论并主动发送影响解释 | Catalyst Agenda 后应优先做“新事实触及了哪条假设/失效条件”的确定性关联包 |
| [Portrait](https://portraitresearch.com/) / [Alphora](https://alphora.dev/) | Thesis/催化剂持续跟踪、来源摄取、版本控制、团队研究工作流 | 研究档案需要事件议程、待处理研究 Inbox 和原始材料导入，但解释仍留给外部 Host |
| [QuantConnect](https://www.quantconnect.com/) | LEAN 策略代码、托管数据与网页回测 | 继续使用 Free 手工桥；没有可证明绑定代码和数据版本的免费 API 前不自动提交 |

## 4. 建议补齐的能力

### P0：先完成可信闭环

1. **A1–A3 真实绩效与贡献**：完成券商报表签收，再实现 TWR/Modified Dietz、标的/账户/
   研究主题贡献和可解释残差。这是所有“判断是否有效”功能的事实底座。
2. **Catalyst Agenda C0–C3**：统一持仓、Watchlist、Case 的财报、公告和宏观事项，保留日期
   certainty、source coverage 与显式刷新，不把已发生 Event 冒充未来事项。
3. **Thesis-impact packet**：当 filing、财报、经营指标或新闻发生变化时，确定性返回它可能
   对应的 Case、假设、失效条件和待复核问题；由 Codex 判断意义并提出候选修订。
4. **Monitor 通知策略**：在现有 transition event、market-close heartbeat 和 Telegram 上增加
   severity 门槛、quiet hours、digest/即时模式与订阅范围；所有规则仍归一个 Dashboard 管理。

### P1：提高研究质量和日常效率

1. **研究 Inbox / 待处理队列**：将新 filing、公告、Transcript、用户上传材料和重大新闻先
   进入可追溯队列，支持关联 Case、标记已读、延后或归档，避免直接污染 Thesis。
2. **管理层讲话与 Transcript facts**：先接免费、许可清晰的 SEC/公司 IR 材料；只抽取带
   来源片段、说话人和时间的事实，跨期措辞变化由确定性 diff 呈现。
3. **版本化估值与情景假设**：保存收入、利润率、倍数、折现率和情景，不自动生成目标价；
   当前事实变化时展示模型敏感性及过期输入。
4. **研究前数据 preflight**：一次显示某标的可用 Provider、覆盖期、延迟、关键缺口和预计
   请求成本，减少 Deep Dive 跑到一半才发现数据不可用。
5. **可移植研究包**：按 Case 导出不含 secret/账户标识的 Markdown/JSON bundle，便于备份、
   分享或交给其他 MCP Host；导入必须保留来源和冲突提示。

### P2：在核心稳定后再做

1. **候选发现与 Screener bridge**：接收用户或外部筛选器给出的候选集，做同口径初筛和
   research queue；不在本地维护全市场数据库，也不让 LLM 自主选股。
2. **更多免费市场覆盖**：只有当新市场能填补真实 Case/组合缺口且有清晰 identity、时区、
   freshness 和 license 时才接入。
3. **多渠道通知与摘要**：Email/Telegram 等共享同一 durable Outbox 和订阅策略，不让每个
   channel 产生另一套业务逻辑。

## 5. 明确不跟进的竞品功能

- 订单、自动执行、复制交易和未经人工确认的策略动作；
- 预算、消费、FIRE、家庭净资产等通用个人财务模块；
- runtime 内置 LLM 或自动替用户确认 Thesis/Trade Plan；
- 为了 Screener 或回测维护本地全市场分钟数据库；
- 将分析师目标价、社区热度或经纪商周末 CFD 冒充可比较的权威事实。

## 6. 与当前路线图的合并结论

不新增一条平行大路线。近期顺序保持：实际缺陷 → A1 签收 → A2/A3 → Catalyst Agenda C0–C3
→ Thesis-impact packet → Judgment Scorecard。研究 Inbox、Transcript 和版本化估值进入后续
评审池；它们只有在免费数据源和 point-in-time/许可边界明确后才进入实施。
