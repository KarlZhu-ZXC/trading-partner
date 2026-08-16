# Reliability, Usability, and Decision-Loop Plan

状态：**闭环目标已完成；生产指标持续积累**  
开始日期：2026-08-12  
完成日期：2026-08-13  
范围：冻结新的市场与独立功能面，优先提升现有功能的稳定性、易用性和跨模块闭环。

当前进度：M1 的 Broker 状态持久化与取消确认语义已实现；Agent/Broker 未决只读查询、
Catalyst overdue、Trade Retro review/action item 和 Scorecard 持续缺口已接入可持久关闭的
内部 ReviewItem。ReviewItem 保留稳定来源键、首次/最近发现时间、期限、发生次数、状态、
resolution ref、expected-version、幂等键和授权说明；只有完整清单或本页明确返回的 exact
source object 能证明事项消失并自动关闭，分页遗漏和读取失败都不能关闭历史事项；来源再次
出现会增加 occurrence 并重新打开。Research Subject Decision
Workbench 已作为独立 Console menu/page 实现第一版；旧 Research、Monitor、Agenda、Retro、
Scorecard 页面继续作为各自的完整操作入口，不被删除或复制写入逻辑。Workbench 的浏览器
数据入口已由 5 个独立请求收口为 1 个 durable-only 聚合请求；服务端只读取当前标的的完整
Research 状态，并行聚合 Monitor、Agenda、Retro、Scorecard。每个辅助模块保留独立失败
envelope，缺失数据不再被误显示成“无 Monitor / 无 Catalyst / 无 Retro / 无 Scorecard”。

## 1. 产品目标

Trading Partner 下一阶段不以增加功能数量为目标，而是把现有能力组织成每天可依赖的
投资判断工作台。每个异常、提醒或复盘结论都必须回答：

1. 发生了什么；
2. 影响哪个 Research Subject、Monitor、账户或订单；
3. 用户下一步可以做什么；
4. 什么事实或动作能够使事项关闭。

目标闭环为：

```text
Provider fact / Monitor / Catalyst
  -> Attention item
  -> human review
  -> proposal and explicit confirmation
  -> durable result observation
  -> Trade Retro / Judgment Scorecard
  -> tracked follow-up action
```

确认门、27-tool MCP 公共表面、Research/Execution 分离和无人值守交易禁令保持不变。

## 2. 冻结项

本计划完成前，不主动增加：

- 新市场、新券商或新的独立 Provider 类别；
- 新的顶级 Console 工作台；
- 第 28 个公开 MCP 工具；
- 自动确认、自动下单或订单重试；
- 无法进入统一 Attention 和恢复路径的新后台状态。

已明确延期的多币种组合统一视图、Catalyst 市场范围扩展和韩国市场深度覆盖继续延期。

## 3. 工作流与验收指标

### M1 — 高风险状态机闭环

- Schwab Provider status observation 必须可持久化读取；刷新不能只返回瞬时结果。
- 取消请求与确认取消分离为 `CANCEL_REQUESTED -> CANCELLED`，不能把请求接受描述成已取消。
- Broker/Agent `UNKNOWN` 永不自动重试，但必须进入可见的人工核对路径。
- 每个状态迁移有聚焦测试，测试覆盖提交、查询、部分成交、取消请求、取消确认和不确定结果。

### M2 — 统一 Attention

先扩展现有 Console Attention 聚合，不增加公开 MCP 工具。覆盖：

- 待确认的 Research candidate；
- Agent `UNKNOWN`；
- Broker `UNKNOWN`、`CANCEL_REQUESTED` 和需要确认的 Provider 状态；
- Catalyst 逾期未验证；
- 未复核 Trade Retro 及尚未关闭的行动项；
- 最新 Scorecard 中重复出现的未评估或纪律缺口；
- Monitor、通知和 Data Quality 的既有事项。

每项至少包含稳定 key、来源引用、严重度、原因、目标页面和建议下一步。第一阶段保持
durable facts + deterministic projection；需要跨版本追踪时再引入内部 `ReviewItem` 实体。

状态：**已实现**。公共 MCP 工具数保持 27；ReviewItem 仅属于内部 Console/Agent 闭环。

### M3 — Research Subject 决策工作台

- Research Subject 页面聚合 Thesis、Trade Plan、Monitor、Agenda、Retro 和 Scorecard 的
  当前状态与最近变化。
- Console 使用单一聚合 refresh cycle；辅助模块可局部失败，且失败不得被解释成业务缺失。
- Research Subject 列表保持轻量，只为当前选中标的读取完整判断状态。
- 页面和 Agent 回答使用相同的下一步语义，不要求用户复制 opaque ID。
- 安全动作跳转到 exact target；写入仍经过原有 expected-version、idempotency 和确认门。

状态：**已实现第一阶段**。旧的专业页面继续保留。

### M4 — 复盘到行动

- Trade Retro action item 从无结构字符串演进为可追踪事项，包含状态、期限和 resolution ref。
- Scorecard 缺口可以生成一个待确认的后续行动，但不得自动修改 Thesis 或 Trade Plan。
- 后续 Scorecard 能区分新缺口、持续缺口和已关闭缺口。

状态：**已实现第一阶段**。旧 Trade Retro 字符串 action item 以规范化内容摘要形成稳定
ReviewItem；可设置期限并记录关闭说明/引用。连续两次 Scorecard 的同维度缺口标为
`Persistent`，首次为 `New`，关闭记录保留在最近历史中。

### M5 — Agent 操作联动

- 按 operation 风险等级扩展 `tp_prepare_action`，不开放通用写入口。
- 用户在当前会话明确作出的 candidate 决定可通过原确认服务完成，不制造无意义的双确认。
- Agenda、Retro、Scorecard 的安全操作逐项接入；真实订单保持独立的 exact preview 与二次确认。

状态：**已实现确认门扩展**。`tp_prepare_action` 仅新增 candidate decision、Agenda item、
Trade Retro 和 Judgment Scorecard 的 exact operation allowlist；candidate/Agenda/Retro review
额外校验当前用户授权字段。Broker submit/cancel 未进入该通用 allowlist。

## 4. 量化指标

每个里程碑记录变更前后：

- `UNKNOWN` 数量、最老未决时长和人工关闭率；
- Catalyst 逾期数量与按期 outcome-link 比例；
- Trade Retro 未复核数量、行动项关闭比例；
- Scorecard 缺口复发比例；
- Attention 从发现到确认、行动、关闭的中位耗时；
- 聚焦测试数量、净增减数量和 wall-clock 时间；
- Console 关键流程所需点击数，以及是否仍要求复制内部 ID。

不以增加宽泛测试数量为目标。优先保留状态机、权限门、Provider 失败恢复和跨模块投影测试；
重复 schema 快照、重复页面文案和低价值穷举应合并或删除，并记录测试时间变化。

### 第一阶段验收数据（2026-08-13）

- 全量 Python：`2244 passed`，pytest 本体 `39.49s`，端到端 wall clock `41.10s`。
- 最慢测试为完整 Research MCP stdio 生命周期 `3.65s`；其余最慢项均低于 `1.1s`。
- 新闭环聚焦切片：`33 passed`，`2.72s`；数据库迁移往返约 `0.91s`。
- Console：`19/19` 通过，测试体 `0.29s`，含启动的完整命令 `4.62s`；production build 与 lint 通过。
- 当前工作集测试净变化：Python `+15`（新增 16、移除 1），Console `+2`，合计净增 `17`；
  没有为 ReviewItem 引入宽泛参数穷举。
- 静态验收：Ruff、`mypy src`（640 个源文件）及 `git diff --check` 全部通过。
- Decision Workbench 浏览器聚合请求由 5 个减少为 1 个（减少 80%）；打开工作台只需一个
  顶级菜单动作，选择 Research Subject 不要求复制内部 ID。旧 Research 页面保留为独立菜单项。
- Attention 从发现到关闭的耗时、人工关闭率和各来源复发率已具备 durable first/last seen、
  occurrence、due 和 resolution 字段；需在生产积累样本后再报告分位数，当前不虚构基线。

## 5. 实施顺序

1. Broker 状态持久化与取消确认语义；
2. Agent/Broker 未决状态的只读聚合与 Attention 可见性；
3. Catalyst overdue 与 Trade Retro review/action 的 Attention 联动；
4. Scorecard 持续缺口和统一 ReviewItem；
5. Research Subject 决策工作台；
6. Agent 安全动作覆盖扩展。

每一步必须先完成失败恢复与测试，再进入下一步。任何新增联动不得绕过原应用服务，
也不得从 Console 或 Agent 直接写数据库。

## 6. 第一阶段完成边界

- M1：取消请求与 Provider 确认取消已分离；非取消观察不会回退 durable intent 状态。
- M2：统一 Review Queue 已覆盖 Agent/Broker 未决、Catalyst、Retro 和 Scorecard，且读取失败
  不会触发错误自动关闭。
- M3：Decision Workbench 是新增菜单和页面；旧 Research 与专业页面完整保留。
- M4：Retro action item 和 Scorecard 连续缺口具备稳定、可确认、可关闭、可复发的追踪记录。
- M5：Agent 仅开放 exact safe-operation allowlist；真实订单仍使用独立确认合同。

## 7. 第二阶段：可用闭环与可度量可靠性

状态：**已完成**（2026-08-13）。

- 新增 occurrence 级生命周期账本。首次发现、每次复发重开、首次确认、人工关闭和来源恢复
  自动关闭分别持久化；复发后的当前未决年龄不会错误沿用历史首次发现时间。
- Review Queue 展示当前 OPEN/ACKNOWLEDGED、逾期、最老当前缺口、中位确认耗时、中位关闭
  耗时、样本数和复发数。零样本返回 null/`—`，不伪造生产基线。
- 指标查询不受 500 条队列分页限制；测试以 501 条事项验证全量分母。
- Decision Workbench 可直接 Acknowledge、设置/调整期限和 Resolve，不再必须跳回 Overview；
  所有写入仍使用 Console session、expected version、幂等键和显式确认合同。
- Workbench 的任一 durable section 失败时明确显示 INCOMPLETE，不会把缺失上下文显示为 READY。
- Retro ReviewItem 现在按不可变 plan snapshot 中的 exact Research Subject 归属，不再用当前
  Trade Plan/Instrument 反推历史归属；Retro、Scorecard、Agenda 事项跳转到 exact
  run/card/item 锚点。旧专业页面仍然保留。
- Overview 的 Research Subject 状态读取由最多 200 次串行读取改为最多 8 路有界并发；仍是
  durable-only，不触发 Provider。
- Agent Runtime 的 Pending Action allowlist 注入从不可达代码移回构造边界，失败保持关闭。

### 第二阶段验收数据

- 全量 Python：`2261 passed`，pytest `41.99s`，端到端 `43.73s`。
- 闭环/Workbench/迁移聚焦切片：`6 passed`，pytest `3.53s`，端到端 `4.83s`。
- Console：`19/19`，测试体约 `0.30s`；production build 与 lint 通过。
- 最慢测试仍是既有 Research MCP stdio 完整生命周期 `4.15s`；本阶段未新增超过 1 秒的
  单元级闭环测试。
- Ruff、`mypy src`（640 个源文件）和 `git diff --check` 全部通过。

闭环能力已经具备；人工关闭率、复发率和耗时分位数需要真实使用产生样本，之后只基于
durable occurrence 账本报告，不以测试数据冒充生产结果。

## 9. 第三阶段：减少重复步骤（2026-08-16）

状态：**已实现并完成聚焦验收**。

- Console 采用“一次明确按钮动作就是一次确认”的规则。Research Candidate、Agenda、Risk
  Policy、Retro Review、Scorecard、Provider Sync 和 Monitor Editor 不再先点击语义明确的
  action button、随后再确认内容相同的浏览器弹窗。expected version、幂等键、actor 与审计
  receipt 均保留。
- 破坏性或有外部影响的动作仍保留额外确认：订单、Research Subject/Monitor 归档、Monitor
  生命周期切换、立即执行 due Monitor 及 OAuth 重连不受本轮简化影响。
- Review Queue 可从 OPEN 直接 Resolve；Acknowledge 不再强迫先填写可选期限，只有已
  Acknowledged 的事项才显示 Update Due。Resolve 只要求真正必需的关闭说明。
- ACTIVE Trade Plan 增加 `Create Monitor From Plan` 直达入口，自动带入 exact plan ID、版本、
  Research Subject 和 Instrument，并默认编译 MONITORABLE conditions；用户无需复制 opaque ID
  或重新录入规则。
- Agent 的 Thesis、Trade Plan 与 Instrument-selection Proposal 直接创建未生效 Candidate，
  不再外套一层 Pending Action。Candidate 的 Confirm / Reject / Withdraw 仍是唯一最终决策门；
  其他有效写入继续使用 `tp_prepare_action`，真实订单边界不变。

### 第三阶段聚焦验收数据

- Python 高相关切片：`84 passed`，pytest `5.18s`，端到端 wall clock `6.59s`；新增 2 个
  Proposal 单次门测试，未增加慢速集成夹具。
- Console：`25/25`，测试体 `0.43s`；包含 production build 的端到端 wall clock `6.99s`。
- Trade Plan → Monitor 从“打开页面、创建、复制 4 个字段、重录规则”收敛为“一次跳转、复核、
  保存”；至少减少 5 个机械步骤，且仅对 ACTIVE plan 显示入口。
- 全量 Python：`2340 passed`，pytest 本体 `55.84s`，端到端 wall clock `58.18s`。验收中
  发现误留仓库根目录的 Monitor Design QA 文档，已无损归档到对应审计目录。
- Ruff 全仓、`mypy src`（648 个源文件）与 `git diff --check` 全部通过。

## 8. 独立批判式复审与加固（2026-08-13）

本轮没有把“测试通过”等同于闭环成立，而是以分页、跨页面归属、越权请求、时间倒流和
超过页面上限等反例重新验收。发现并修复：

- ReviewItem 曾把成功读取的一页误当成完整清单，可能自动关闭未出现在本页的历史事项。
  现在区分 observed source type、authoritative exact source ref 和 fully observed inventory；
  Agent/Broker 查询触及 limit 时保守地不推断缺失即关闭。
- Retro 的 Overview 与 Workbench 曾用不同规则改写同一个 source key，可能发生唯一键冲突或
  把历史复盘串到错误标的。Retro history 现在从不可变 plan snapshot 输出去重后的
  `subject_ids`，每个 subject 使用独立稳定 key；旧无归属记录保留 global 语义。
- Review Queue 曾先截取最近 500 条再排序，较早的 ERROR/逾期事项可能永远进不了首页。
  现在先对完整未决集应用错误、逾期和期限优先级，再截取页面；指标仍直接聚合完整账本。
- Agent 曾在 conversation ownership 校验前访问模型目录，并可能在本轮尚未创建 turn 时把
  上一轮 RUNNING turn 标记为 FAILED。鉴权已前移，异常处理只允许修改本次实际创建的 turn。
- Research selection candidate 的 action allowlist 因缩进错误成为不可达代码。现已恢复
  `create/update_status` fail-closed 检查，并增加“kind 合法但 action 越界”的反例测试。
- Overview 同一事项曾同时出现在 Decision Inbox 与 durable Review Queue；成功持久化后现在
  只由 Review Queue 展示，原始 projection 仅留作失败回退/诊断。独立 durable reads 改为并行，
  不改变各模块失败语义。
- Retro 精确归属初版会对每个 run 分别查询 snapshot 和 reviews，形成 `1 + 2N` 查询；history
  现按 run 集合批量读取 runs、snapshots、reviews，查询数量不再随列表长度线性增长。
- occurrence/action 表补充时间顺序、确认/关闭字段一致性和 occurrence number 数据库约束；
  stale reconciliation 不能覆盖或关闭更新的状态。指标 DTO 也校验状态总数、样本数、模式、
  中位数和 rate 的一致性，避免静默返回貌似合理的错误数字。

### 批判式复审验收数据

- 高风险聚焦切片：`79 passed`，`6.59s`，覆盖 Agent 授权/Pending Action、Broker 生命周期、
  ReviewItem 分页/复发/指标/时间倒流、Workbench/Retro 归属及 migration round-trip。
- 全量 Python：`2273 passed`，pytest `42.45s`，端到端 `44.14s`。相对上一轮 `2261` 个测试
  净增 `12`，pytest 本体仅增加约 `0.46s`（约 `1.1%`）。
- Console：`20/20`，测试体 `0.30s`；Next production build 与 ESLint 通过。
- Ruff 全仓、`mypy src`（640 个源文件）和 `git diff --check` 全部通过。
