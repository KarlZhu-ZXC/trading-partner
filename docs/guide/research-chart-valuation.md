# 图表、估值假设与判断校准

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
