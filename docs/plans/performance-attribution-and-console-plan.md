# 真实业绩归因与本地控制台计划

状态：控制台 v1、A0 覆盖/标准活动账本和 A1 确定性损益账本已实施；A1 券商报表人工对账
待完成，A2–A4 按本计划推进。

## 1. 当前决策

- A 股 QMT 接入、A 股账户同步和跨币种 FX 统一折算至少延后两个月；在此之前不建设
  临时 A 股券商适配器，也不把缺失汇率当成 1。
- 首期真实业绩归因只覆盖已持久化的美股账户事实。多币种账户分别核算，不输出伪精确的
  跨币种总收益。
- 归因基于真实交易、现金流、费用和账户快照，不依赖本地历史行情平台，也不执行回测或
  订单。
- 本地控制台不依赖 Codex/LLM；读取默认无副作用，用户可明确触发 Monitor、同步、通知、
  备份、缓存维护和全部公开 MCP 工具。写操作继续使用既有审计、确认与幂等流程。

## 2. 归因目标与边界

要回答的不是“策略理论上怎样”，而是：

1. 账户实际赚亏多少，数据覆盖到哪里；
2. 哪些标的、持有区间、费用、股息和现金流贡献了结果；
3. 组合收益来自市场暴露、选股还是仓位变化；
4. 实际交易是否遵守当时确认的 Thesis、Trade Plan 和风险预算；
5. 哪些结论因交易历史、现金事件、估值点或 FX 缺失而不能计算。

首期明确不做税务报表、A 股归因、跨币种总组合、因子模型、期权 Greeks 归因或自动判断
“交易好坏”。

## 3. 数据先决条件

当前交易同步可提供真实交易记录，但归因需要比持仓分析更严格的覆盖证明。先新增一个
`attribution_coverage_receipt`，逐账户记录：

- 请求区间、最早/最晚可见交易、快照密度、缺口区间；
- 券商原始类型到标准事件类型的映射版本；
- Trade、Dividend、Interest、Fee、Transfer、Corporate Action 是否完整；
- 成本基础、手续费和外部现金流的来源与缺失原因；
- 重复记录、冲正、拆并股和符号变化的处理回执。

标准账本事件必须允许现金事件没有 `instrument_id`，并用 Decimal、原币种金额、时区感知
时间和稳定的 provider event id。任何覆盖不足都返回 `INCOMPLETE`，不能静默外推。

## 4. 实施切片

### A0 — 覆盖与标准账本

状态：已实施（`compact-v9`）。

- 扩展美股交易同步为可续传的区间积累，不把单次 API 窗口描述成完整历史；
- 标准化交易、股息、利息、费用、转入转出和公司行动；
- 生成覆盖回执、缺口和可重跑去重收据；
- 对 Moomoo 无法提供的费用字段保留 typed unavailable。

退出标准：同一账户重复同步不重复记账；现金事件可表达；覆盖范围可机器判定。

### A1 — 实际损益账本

状态：代码与本地控制台已实施（`compact-v10`）；真实账户报表对账尚未签收。

- 按账户、标的和原币种建立 lot ledger；
- 支持 FIFO 与 broker-reported cost basis 两种口径，输出时明确 basis；
- 分开 realized P/L、unrealized P/L、dividend、interest、fee 和 net cash flow；
- 每个汇总数字可下钻到事件和快照。

退出标准：选定的美股账户可与券商同期报表对账；差异有明确残差和原因。

当前实现不会为了达到“完整”而消除真实缺口：若账户 inception 历史、公司行动 lot 变换、
手续费、期末仓位勾稽或带时间估值不足，会给出可下钻数字并保持 `INCOMPLETE`。在完成一份
券商同期报表人工对账前，A1 不宣称最终验收。

#### A1 人工对账输入与签收

首份签收优先使用 Schwab 同一账户、同一自然月的两类官方导出：

1. 月结单 PDF，作为期末现金、持仓、已实现/未实现损益及账户口径的正式记录；
2. `Accounts > History > Realized Gain/Loss` 的 CSV，提供 symbol、开仓/平仓日期、cost、
   cost-basis method、proceeds 与 realized gain/loss 等可逐项匹配字段。

若 CSV 未覆盖股息、利息、费用、转账或公司行动，再补同区间 Account History CSV；不要用
截图或手工改写数字替代原始导出。Schwab 明确说明 statement/confirmation 才是正式记录，
且成本基础可能因缺失 lot、费用或公司行动而显示不完整，所以 Trading Partner 必须保留
`INCOMPLETE` 与残差原因，而不是强行调平。

个人报表只放在 gitignored、owner-only 的 `data/artifacts/reconciliation/`，不得加入 fixture、
日志、聊天内容或 Git 历史。签收至少记录：账户哈希标识、区间、币种、basis、系统结果、
报表结果、绝对残差、可解释残差类别、未解释残差和签收时间；原始账户号不得进入签收摘要。
本步骤目前是人工验收门，不新增公开 MCP 工具，也不让 MCP runtime 自动读取任意 PDF/CSV。
CLI 已提供一个严格的准备入口：

```bash
uv run trading-partner-performance-reconciliation inspect-schwab-realized \
  --realized-csv schwab-realized-2026-06.csv

uv run trading-partner-performance-reconciliation compare-schwab-realized \
  --realized-csv schwab-realized-2026-06.csv \
  --account-ref schwab_STABLE_ACCOUNT_REF \
  --statement-account-ref schwab_statement_HASH_FROM_INSPECT \
  --month 2026-06
```

该命令只接受 `data/artifacts/reconciliation/` 下的相对路径，拒绝绝对路径、目录穿越与
symlink，并把目录/文件权限收紧到 `0700/0600`。解析按 Schwab Realized Gain/Loss
lot-details 表头名称而非固定列序完成；账户标题在 Provider 内转为稳定哈希，raw row、账户
标签与文件路径不进入 application DTO。成本、开仓日期或 realized P/L 缺失保持 `None` 和
typed warning，重复 lot 或无法识别的表头直接失败。`compare-schwab-realized` 不接触券商，
只读取 durable ledger；它按美东自然月和 symbol 比较 Schwab statement realized P/L 与
FIFO after-fee P/L，显式列出 wash-sale、缺费用、覆盖不足、statement-only/system-only symbol
和超容差残差；即使账户总残差为零，symbol 残差相互抵消也保持 `REVIEW_REQUIRED`。结果写入
owner-only `receipts/` JSON 草稿，原始 row、文件路径和账户标题均不
进入草稿。该草稿不会自动签收；真实 PDF/CSV 与同期 durable ledger 完成人工残差确认前，
A1 仍保持“未签收”。

### A2 — 收益率

- 快照密度足够时计算 time-weighted return；
- 不足时使用 Modified Dietz，并明确近似方法；
- money-weighted return 仅在现金流和区间端点完整时计算；
- 以原币种分别报告，FX 未启用时不产生统一组合收益。

退出标准：每种收益率均附公式版本、现金流口径、覆盖和 `COMPLETE/INCOMPLETE` 状态。

### A3 — 贡献与比较

- 标的、行业、研究主题和账户的收益贡献；
- 可选基准的区间比较，基准、复权和时区 basis 显式；
- 将配置效应、选择效应和交互效应作为后续可选 Brinson 切片；
- 不在行业映射或基准权重缺失时伪造分解。

退出标准：总贡献与账户净收益可解释地勾稽，残差单列。

### A4 — 决策与执行纪律复盘

- 将成交时间映射到当时有效的 Thesis、Trade Plan、Decision 和 Risk Check 版本；
- 区分“计划内执行”“缺少计划”“超出计划范围”和“事实不足”；
- 输出持有期、加减仓、止损纪律和事后结果，但不自动给人或策略打分；
- 复盘写入仍走用户确认，不由运行时 LLM 自动改变 Thesis。

退出标准：任何纪律判断都能追溯到当时版本，禁止用事后最新版计划覆盖历史。

## 5. MCP 与控制台落点

首选扩展现有 `portfolio_analyze` 的闭合 operation，而不是新增一组碎片工具：

```text
coverage
performance_summary
contribution
decision_adherence
```

若 schema 预算或权限语义不适合，再在设计冻结时决定是否新增一个聚合工具。控制台增加
“业绩归因”页，展示覆盖、收益瀑布、标的贡献、费用/现金流和可下钻事件；在 A0–A1 完成前
只显示“数据尚未具备”，不以账户当前未实现损益冒充区间业绩。

## 6. 验收和推进顺序

推荐顺序是 A0 → A1 → 券商报表人工对账 → A2 → A3 → A4。QMT 接入后再追加 A 股事件
映射和 FX 层；FX 必须使用可追踪的日度换算口径与现金流时点，不能简单使用当前汇率。

这条只读归因线可独立于真实下单推进，也不要求历史行情平台先完成。
