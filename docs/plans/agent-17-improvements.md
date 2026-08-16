# Shared Agent Runtime — 17 项成熟度清单

状态：**已完成（2026-08-13）**。本文恢复 2026-08-13 对话中提出的原始 17 项建议，作为完成审计的唯一逐项
清单；不能再用后续的简化归类替代原始范围。每一项只有在实现、聚焦测试和实际运行证据
同时具备后才能标记完成。

真实订单 Agent-E 不属于这 17 项，继续默认关闭。公开 MCP 工具面保持 27 个。

| # | 目标 | 当前状态 | 完成验收 |
|---:|---|---|---|
| 1 | 动作 schema 可发现 | COMPLETE | `read` 与 `prepare_action` 分离；后者只投影 Pending Action allowlist，发现不执行 |
| 2 | Durable Agent Turn、断线恢复与真正取消 | COMPLETE | Durable RUNNING/WAITING_TOOL/terminal 状态；服务端 cancel、durable reconnect replay、orphan 收敛和 FAILED retry 均已完成 |
| 3 | Pending Action 刷新恢复 | COMPLETE | exact identity/channel/principal/expiry/version CAS 换发一次性 token，旧 token 失效 |
| 4 | Agent 服务自动启动与健康恢复 | COMPLETE | launchd 可管理 Console API、Next production 与 Telegram；status/restart/KeepAlive、安全 PID/start/exit 投影和 UI 状态完成 |
| 5 | 独立只读工具有界并行 | COMPLETE | 同一模型响应最多四路并行；消息、event、receipt 保持模型顺序；写/混合批次串行 |
| 6 | 更可靠且可审计的能力路由 | COMPLETE | 领域词/字段匹配、相邻 operation、缺参 hint、query SHA-256、选择理由与有界 durable audit 完成 |
| 7 | 工具参数结构化自动修复提示 | COMPLETE | 缺失/非法字段以脱敏结构返回，模型可在下一轮修复，不复制异常正文 |
| 8 | operation 专用结果压缩 | COMPLETE | Monitor、Portfolio、Research、Agenda、filings/news/company updates 与 Review Queue 均有保留 envelope/provenance 的专用压缩 |
| 9 | 程序化证据绑定与回答口径守卫 | COMPLETE | 数字/date/action、price basis、freshness/degraded/warnings/errors 可追溯；失败最多一次安全修复，仍失败则显式未验证 |
| 10 | 显式 typed 当前工作台对象 | COMPLETE | 独立 DTO 含 route hash、surface、Subject/Monitor/Run/tab/Workbench IDs；16KB/untrusted/navigation-only，不复制页面事实 |
| 11 | Agent 接入 Decision Workbench / Review Queue | COMPLETE | durable-only open/summary/subject read 已接入；acknowledge/resolve 只生成 exact Pending Action，不自动关闭 |
| 12 | 回答排版与消息级操作 | COMPLETE | 安全结构化渲染、inline receipts、鉴权图表、来源、复制、失败重试、编辑后新 turn 与 runtime artifact 持久化完成 |
| 13 | 右栏可调宽度与专注模式 | COMPLETE | 折叠、handoff、归档、320–720px 鼠标/键盘调宽、持久化宽度与 46% 研究模式完成 |
| 14 | 显式、非事实型长期偏好 | COMPLETE | owner-scoped、版本化、CAS/幂等/history/reset；仅语言、密度、来源、风险表达与图表，事实/状态字段被拒绝 |
| 15 | 真正 token streaming | COMPLETE | Chat Completions/Responses SSE 增量、工具 delta、usage/source/request ID、发出后不重试及 final-only fallback 完成 |
| 16 | Auto 模型路由与只读故障转移 | COMPLETE | 显式 Auto、确定性 fast/complex route reason；仅安全 read/search 在未输出前对 timeout/rate/unavailable 单次 failover |
| 17 | 真实 Agent 行为评测集 | COMPLETE | 14 case 全部真实执行 Runtime 并逐项断言；另含 schema repair 与关键源码 SHA-256 fingerprint 门禁 |

## 横向完成门

- Console 显示当前会话累计 model calls、token、Web Search/Extractor 次数和耗时；Telegram
  `/context` 提供同口径的紧凑统计。
- 每轮模型、实际路由、request ID、延迟、usage、工具轨迹有界持久化，且不保存 API key、
  Authorization、完整 endpoint/query、原始异常或无界模型/Provider payload。
- 每个新增写入口复用现有 DTO、expected-version、idempotency、actor 和 Pending Action
  exact-confirmation 契约。
- 完成审计需运行 Agent 单元/集成/迁移/架构测试、Console lint/build/render tests、wheel
  smoke 与 secret scan；真实 Provider smoke 只做有界只读验证。

## 最终验收证据

- Python 全量：`2321 passed`；Ruff 全仓、`mypy src` 通过。
- Console：Next production build、ESLint、`21/21` 渲染与交互测试通过。
- 行为评测：`uv run trading-partner-agent eval` 为 `passed=true`、`14/14`，schema repair
  通过并输出 prompt/runtime/capability/action/model-provider fingerprint manifest。
- 迁移：本地与隔离 wheel 均到 `0050_agent_preferences`；隔离 wheel smoke 输出
  `ISOLATED_WHEEL_SMOKE_OK`，公开 MCP 仍精确为 27 个。
- Secret scan：Gitleaks 扫描当前 Git 历史及未提交 diff/untracked 文件无泄露。
- 有界真实 Provider smoke：Bailian `qwen3.8-max` 完成
  `tp_capability_search → market_data_get/quote → token stream → durable COMPLETED`，并保存
  tool receipt、usage、request ID、latency 与 evidence manifest；未触发任何动作或订单。
