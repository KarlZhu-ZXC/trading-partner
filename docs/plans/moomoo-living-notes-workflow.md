# External Observation Sources → Journal Workflow

状态：**首个可用切片已实现；后续联动按本文边界演进**

日期：2026-08-27

## 目标

用户继续在 Moomoo、未来 TradingView 或其他看盘工具中维护看法，不需要在 Console 重抄。
Trading Partner 将每次可见内容变化保存为不可变 revision，区分本人观点与明确署名的外部观点，再把
模型解释作为可审阅草稿送入现有 Journal 流程：

```text
External Source Document
  → Immutable Note Revision
  → Viewpoint + Change Draft
  → User Reviews Decision / NO_ACTION
  → Existing Plan / Monitor / Order / Fill / Review Loop
```

这不是另一套 Thesis、Scorecard 或交易模块。确认后的判断继续由现有 Decision Record、Thesis
和 Trade Plan 持有；订单权限不变。

## 作者与连续性规则

- 每个日期段开始默认归属 `USER`。
- `宝总：`、`Boss墨：` 等明确前缀开启对应 `NAMED_PERSON` section；后续无署名段落继续
  继承该作者，直到下一日期、另一明确作者或“我/本人：”显式重置。
- `整体观点：`、`风险：`、`结论：` 等是结构标题，继承当前作者，不是人物姓名。
- 作者归属由确定性 parser 决定；模型只能报告边界疑点，不得改写归属。
- 合成回归样例中，日期段内的显式外部 speaker 会持续拥有后续段落和结构标题；下一日期边界
  重置为 USER。该状态机在相同严格 schema 下通过模型解析。
- 外部观点只作为来源观点，不能自动成为用户 Thesis、Decision 或市场事实。
- 同一 `(source_code, external_id)` 是稳定 identity；内容 hash 变化才新增 revision，未变化不重复写入。
- 新 revision 与上一版成功解释比较，关系限定为 `NO_MATERIAL_CHANGE`、`CORRECTION`、
  `REVISION`、`SUPERSEDES`、`INVALIDATES`、`REMOVED_FROM_NOTE` 或 `NEW_THREAD`。
- 删除、改写或失效只产生历史关系，不删除旧 revision，也不自动修改已确认 Research 状态。
- 幂等使用稳定 `source_revision_key`，不再把 content hash 当 revision identity。重复 replay
  不新增版本，乱序旧 observation 被忽略并告警，而较新时间点恢复为历史相同正文仍保存为新的
  reversion revision。
- Console、CLI 与 adapter capture 共享跨进程锁；同进程并发 capture 串行化，超出有界等待返回
  retryable `OBSERVATION_SYNC_BUSY`，不竞争写入 revision。

## Adapter 合同

Journal、持久化和模型层只依赖统一 `ObservationSourceCapability` 与 canonical snapshot，不知道
Moomoo、TradingView 的原始字段。每个 adapter 声明 source code、是否支持全文、是否增量同步、
是否要求交互式会话及 content mode。Console `Refresh Sources` 可在一轮中聚合多个 adapter。

无法直接嵌入应用的来源可输出
[`observation-source-v1.schema.json`](../contracts/observation-source-v1.schema.json) 到 owner-controlled
`data/observations/inbox`。`LOCAL_OBSERVATION_BRIDGE` 只接受封闭 JSON 与完整正文；未来
TradingView adapter、浏览器侧 capture 或本机 UI capture 都复用该入口，不新建另一套 Journal。

## 模型边界

用户已明确允许把私有笔记正文发送给 OpenCode Go。解释模型独立固定为
`qwen3.8-flash`，`max` 推理强度、120 秒单次超时、严格 function schema，关闭
Web Search。模型只输出：分作者摘要、本人四情景草稿、关键点位、缺失证据、矛盾、版本关系和
下一步建议。

同步与解释分离：Console 的 `Refresh Sources` 只做本机来源扫描与持久化，随后只后台分析具有
`FULL` 正文的新 revision；
页面不等待数分钟的模型批处理。失败保留 typed unavailable 状态，原始 revision 仍可读，且不会
生成 Decision、Plan、Monitor 或订单。

## Console 工作流

Journal 顶部 `Observation Inbox` 按当前 Research Subject 的执行 Instrument 过滤笔记：

1. `Refresh Sources` 导入所有已配置来源；
2. 查看 coverage、revision、作者拆分与结构化策略草稿；
3. `Review as Decision` 把 exact note revision 和模型草稿预填到现有 Decision 对话框；
4. 用户修改 Scenario、Action、Reason、Review Date 后显式保存；
5. 只有保存后的 Decision 进入既有 Plan、Monitor、Order、Fill 与 Review 闭环。

`SUMMARY_ONLY` 表示本机缓存只保留列表文本，不能冒充完整笔记、调用模型或采纳为 Decision。
完整编辑器缓存被淘汰时，同一摘要不得制造一个虚假的降级 revision。无匹配 Research Subject
时笔记仍保存在 Inbox，但不能直接采纳为 Decision。

## 已实现验收

- 只读解析 Chromium Simple Cache 和 Moomoo 本机 stock map，不写回 Moomoo；
- 约 53,800 个缓存文件扫描从约 11.4 秒降到约 2.64 秒；
- 使用脱敏合成笔记验证 USER / 外部 speaker 拆分，并成功生成四情景结构化草稿；
- 使用脱敏回归正文做 adversarial replay：原文 hash 无损，重复导入不新增 revision，
  更正/失效/回退形成有序版本，重新扫描保持幂等，乱序输入被拒绝；
  Moomoo 压平换行后的 243 字列表正文经历史 editor 逐行超集证明后恢复为 `FULL`，作者保持
  `USER` / `宝总`。真实 OpenCode Go 两次 schema 验收分别为 `41.76s` 与 `70.12s`，均返回
  USER 四情景和闭合 block citations；
- source revision、model interpretation、sync receipt 均持久化；该切片最初落在
  `0067_post_market_observation_sync`，当前 migration head 为
  `0070_retire_unlinked_review_items`；
- 手动刷新快速返回，分析后台执行；重复内容不新增 revision；
- 可选的 authenticated HTTP 增补层无需控制桌面 UI：owner-only Cookie 通过 stdin 配置，
  list/editor 请求串行且逐次采用有界随机等待；鉴权、限流或页面漂移失败回退缓存；
- 经用户授权的只读 CEF NetLog 验收提高了当前笔记的 FULL 覆盖；原始 NetLog/TLS keys 在
  提取后删除，只保留 `0600` Cookie。证明式列表提升
  支持 newest-first 的纯前插和旧模式纯后插，并保留 editor 段落边界；中间改写继续 fail closed。
  日期边界后的后续更新继续归属 USER；
- 定向回填历史 summary-only identity 后，已观察集合达到完整 FULL 覆盖；Moomoo 没有账户级
  全局笔记索引，因此仍无法证明不存在从未进入本机列表缓存的其他标的笔记；
- 公共 MCP 仍保持 27 个工具。

## 已完成的后续切片

1. Revision History 已按需展示版本关系、时间、证明状态及有界的新增 / 删除行；
2. Moomoo adapter 可从历史 editor 缓存证明并恢复完整正文，不能证明时继续 fail closed；
3. 失败解释提供 exact revision 的有界 Analyze / Retry，不做无限自动重试；
4. 无匹配 Research Subject 的观察可打开预填创建草稿，仍需用户显式保存；
5. Journal 已将模型返回的四情景草稿展示为可扫描的 Scenario blocks，并保留完整正文披露。

## 后续切片

1. 在 Research 中预填 Thesis / Trade Plan Candidate，但仍走 Propose → 用户 Confirm；
2. 由确认后的 Plan 条件预填 Monitor Candidate，不从原始笔记直接激活 Monitor；
3. 在 Trade Retro 中引用 exact note revision 与采纳 Decision，检验观点变化是否及时转化为操作；
4. 为未来 TradingView capture 增加同一 `observation-source-v1` 合约的具体 adapter。
