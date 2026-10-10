# 图表、估值假设与判断校准

## 日常快速复查

从 Overview 的 Today Review 选择待复查标的；同一标的有多个研究档案时，选择确切档案。卡片自动读取已同步 Moomoo 笔记中
你的最近想法、上次确认判断、新变化和持仓快照；原文、模型提取摘要和引用他人的观点
分开显示。这里只读取本地记录，不会因为打开页面而刷新行情、笔记或运行模型。

日常只需选择结果：维持判断后点击 Confirm No Action；暂缓时填写缺口和未来日期后
点击 Save Follow-up。无需再填一次标的、观点版本和计划 ID。已有原判断会被准确引用，
新决定不会覆盖旧记录。没有原判断时先整理草稿；超过卡片历史范围时使用完整研究页。

需要修改时点 Adjust Thesis / Adjust Plan，在同一 Research 页面使用已有编辑器，
Return to Quick Review 返回后原理由和日期仍保留。Review Thinking in Thesis 先展示原观点与可编辑新草稿，核对后点 Use Edited Draft
再进入正式编辑器，不会覆盖已经打开的编辑器。想法日期与同步日期分开显示；
Changes from Previous Thinking 展开上一同步版本的用户段落变化，不把文字变化当作正式观点变更。

笔记已更新但尚未同步时，点 Refresh Notes。后台沿用现有刷新与解读进度；完成后
当前空闲复查卡自动更新，保留你的输入。正式编辑中或已经记录的卡片不会被替换。
最新版本还未解读时展示你的原文片段，绝不拿旧摘要当最新观点。仅有摘要或作者归属
不明确时显示缺口；无年份日期的推断和截断信息保留在 Source Details。

复查上下文有效期二十分钟。提交前原判断或证据发生变化时会提示重新读取，并保留
输入。提交失败可使用同一请求重试恢复结果；刷新页面只读，不会自动再提交。通用
NO_ACTION 不会隐式采纳或关闭笔记的独立审阅，复杂观点修改仍保留原有提议/确认步骤。

卡片会列出本次准备的材料引用、时间和覆盖缺口；准备材料不表示每条资料都已读或采纳。
确认后，Decision 与材料引用一起保存。完成回执中的待审笔记数量是准备时的已知数量，
并非当前队列计数；需要继续处理时点 Open Remaining Notes。材料过多导致引用清单部分
可用时，先逐项检查或记录补证安排，不能使用维持快捷动作。

提交结果不确定时先点 Check Submission。刷新页面也只查原请求回执，查到已记录结果
便展示原材料，不用新资料替换它。Retry Same Review 保留原请求；只有明确未找到回执
后才可放弃该次尝试并重新读取。材料回执缺失不表示 Decision 未保存。

## 不依赖模型的笔记审阅

在 Journal → Notes 阅读 FULL 原文及作者归属，选择确切的 ACTIVE 研究档案后，点
Record Independent Decision。即使解释失败或尚未运行，也能打开确认框；理由初始为空，
需要由你填写。确认只记录引用该版本的独立 Decision，原笔记审阅仍保留自己的状态。
SUMMARY_ONLY 资料不能走此路径；同一股票有多个档案时必须明确选择。已有决定草稿
不会被新的来源替换；Notes 中明确选择档案后，后台上下文刷新不会清掉这次选择。
浏览器重载后的原请求恢复适用于 Quick Review；普通 Journal 决定草稿仍限当前页面。

## 今日队列与每周回顾

Today Review 默认只展开当前待办。相同标的的笔记、监控、催化事件和到期复查聚合显示，
各来源与研究档案仍可分别打开。Deferred 与 Reviewed 默认折叠，新证据出现后会重新
进入待办；这不会把底层笔记审阅偷偷关闭。处理失败和独立操作仍会显示。

切换 Weekly Review 查看本地时区本周一开始至今的确认观点、原版本差异、未解决问题和
到期复查。日期窗口、来源覆盖和缺口明确显示；没有胜率或模型信心得分。记录 NO_ACTION
同样计入已复查记录，不把没有交易当作失败。这是页面内回顾，不是新增自动推送任务。

Quick Review 的理由、结果选择与日期会在当前浏览器会话中保存，最多二十四小时，
再次打开同一标的可以恢复。未确定的提交保留原请求；重载只查询保存回执，不会重复写入。
看到未决结果时先 Check Submission，必要时 Retry Same Review。若回执确认未找到，
可主动 Abandon Attempt and Refresh 后重新核对。浏览器不支持存储时会明确提示。

## 从研究进入图表

Research 的 Open Chart 与 Monitor 的图表快捷入口携带精确 Instrument；有 Research
Subject 时一并携带其 ID。Charts 页需显式点击 Open Chart 才读取行情。更换标的或周期
立即丢弃旧画布，并忽略迟到的旧请求。价格复权基础由 Provider 返回，界面不虚构可选
复权参数。重新取得不同基础或数据版本时，旧画线不沿用。

SMC 图层锁定，用户画线保留在当前会话。点击图层或 Inspect Structure 查看发生时间、
确认时间、快照状态、算法版本及源 K 线范围。Historical Cutoff 按确认时间过滤结构和
K 线；趋势和失效/回补/扫掠状态仍属于当前快照，不是历史状态重建或回测。

Formal Trade Plan 单独显示匹配标的的 ACTIVE 版本及条件。当前 Plan 没有可比的复权
基础，故其价位不叠加到算法图层。

Use as Research Context 可附上用户解释，Continue in Research 暂存一个与精确标的、
Subject、算法、时间与基础绑定的会话上下文，有效期 24 小时。URL 只有上下文 ID，不含
笔记和价位。Research 展示来源，用户再次接受后才预填可编辑草稿。Thesis 初始为 DRAFT；
Plan 只补充笔记和来源，不从图层推断交易阈值、止损或参考价。已打开的目标编辑器不会
被覆盖。原有提议和确认流程继续负责所有正式写入。

## 估值假设台账

在美国股票 Research Subject 的 Valuation 模块显式选择 Retrieve SEC Annual EPS。
首版只接受可见的 SEC 年度 10-K/10-K/A、USD、正稀释 EPS 和完整披露日期；缺参数、
亏损、其他币种及仅有当前快照的 fallback 均显示缺口。银行、保险、REIT 和其他非普通
经营公司不适用；业务类型由用户明确声明，不代表 Provider 已完成行业适用性认证。

来源事实、用户假设与计算结果分开显示。来源保留报告期、披露时间、accession、查询
时点、稀释每股口径以及可得的同期股数。股数只作附注，不用作第二次除数。来源令牌
由当前 API 进程保管，最多一小时；页面刷新、进程重启或过期后，需显式重新取数。
计算和保存均不会隐式刷新 Provider，不调用模型。

方法 `normalized_diluted_eps_pe_v1`：

```text
归一化 EPS = 报告年度稀释 EPS × 用户归一化因子
每股估值情景 = 归一化 EPS × 用户 P/E
```

所有假设输入初始为空。用户填写归一化因子、P/E、两个敏感性步长和理由，生成基值及
上下各一步的九格情景。使用 Decimal、固定精度与舍入，缺值不作猜测。不输出企业价值
或总股权价值，不混用市值、净债务和股本；也不把历史报告 EPS 自动调整为当前拆股口径。
这是一种用户假设下的情景估值，不是客观公允价、价格预测或投资建议。

用户核对来源、假设、结果并填写保存说明后，可以显式保存不可变版本。版本复用 Journal
NOTE 的唯一幂等键、审阅者、审计和 Search 机制，以 `journal_id` 为版本身份；不新增
数据库表，不覆盖旧记录。历史恢复只恢复用户假设，重新计算仍需显式取得来源。新版本
可指向一个精确旧版本，允许独立分支而不是偷偷改写“最新”。这不确认 Thesis/Decision。

## 判断校准

Calibration 默认选择最近的用户 Decision，也支持 API 按精确 Decision 选择。
其 Thesis 与 Plan 版本保持锁定，不使用当前版本替换。页面分四组展示：

- 事实预测与已有结果：匹配版本的 Scorecard、Subject 内 Agenda 及其结果来源。
- 计划条件观察：与确切 Plan 条件匹配的 Monitor 变化，以及已有覆盖卡片。
- 执行纪律：匹配 Decision/Plan 的期前快照与 Trade Retro 记录。
- 可归因交易结果：现有记录无法建立归因时明确 UNKNOWN，不新增收益引擎。

四组不合成为胜率。Agenda 同属一个 Subject 不代表因果链接；未到复查日期、NO_ACTION、
未交易及无证据均不等于失败。每个来源独立披露不可用和有界历史的 PARTIAL 状态。
刷新只读持久数据，不生成 Scorecard、Monitor、Retro 或任何正式判断。


## Plan dependency review and execution readiness

After confirming a Thesis or Trade Plan change, open Research → Trade Plan →
Dependency Review. Compare the previous Plan fields and condition definitions,
check the current Thesis revision, and inspect each Monitor's exact Plan binding.
An older bound Monitor remains unchanged. Review Rules or Pause opens that Monitor;
if its version has changed since the link was created, Console warns before you
use its existing edit/lifecycle controls. Create Monitor From Plan prepares a draft
for the current exact Plan version and still requires its normal confirmation.

Keep Existing Rules and Defer Review record only the choice in the current view.
They do not create a durable review or reminder. MANUAL conditions link to explicit
Journal review and Agenda entry; review before acting and when the relevant evidence
changes. The owner chooses the actual reminder time.

Coverage distinguishes unconfigured, configured without observations, usable
observations, unavailable data and manual conditions. The same calculation supplies
new Scorecards. The age check is conservative elapsed time against the condition's
maximum fact age; historical observation references remain visible. A triggered
condition can be observed and still argue against execution. A read failure is
incomplete coverage, never proof that no dependency exists.

Choose Check Sizing and Risk only when considering execution. The calculation uses
durable account snapshots and the Plan reference price, shows missing NAV/cash/stop
and other constraints, and rejects results for another Plan version. Use Portfolio's
explicit Sync Accounts action if a refresh is needed; a changed price/stop goes
through the existing Plan proposal and confirmation. Optional risk inputs remain
available through the existing Portfolio risk workspace.

Prepare Separate Order Preview opens an editable capability draft with exact Plan
and Subject references. Fill and review the order terms before requesting a preview;
only a separate unexpired preview plus exact authorization can submit. Nothing on
this page grants that authorization. For UNKNOWN outcomes, inspect the existing
order intent/status without resubmitting. Use the Journal timeline and trade cycles
to reconcile partial fills and external activity; unlinked activity remains
execution-only until an explicit attribution is recorded.
