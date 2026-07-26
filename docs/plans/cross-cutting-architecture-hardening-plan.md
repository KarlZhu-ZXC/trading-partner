# Trading Partner — 跨阶段架构加固设计

> 状态：核心加固已完成；MCP E 工作包的 52-tool registrar 方案已被后续 compact-native R7 收口取代
> 历史基线：Phase 1–3D 曾有 52 个公共 MCP 工具；当前唯一工具面为 compact 28
> 目标：补齐调度可观测性、请求幂等和调用者信任边界，并拆分巨型接口/装配/Provider 模块；不改变投资产品语义，不增加订单能力。

## 1. 为什么现在做

Phase 3D 完成后，核心能力边界仍然健康，但长期迭代成本集中在五处：

| 区域 | 当前形态 | 主要风险 |
|---|---:|---|
| Post-market Automation | due-check CLI + 外部调度；只保留 terminal receipt | 调度漏跑无主动信号；CLI 无 `--help`/`--status`/受限补跑入口 |
| Workflow / Challenge Review | terminal receipt 或一次性状态更新 | 调用重试不能返回同一结果，可能重复 Provider 成本或重复 Review |
| MCP façade | `interfaces/mcp/server.py` 约 2,400 行 | 52 个 schema、注册、适配和错误处理集中，修改冲突面大 |
| Composition root | `bootstrap.py` 约 1,400 行，Container 暴露大量字段 | Provider、资源生命周期和应用服务图难以独立测试 |
| A-share Provider / codec | Eastmoney 约 5,000 行；typed codec 约 3,300 行 | endpoint、解析、时间语义和错误映射互相牵连 |

实施采用兼容式纵向拆分，没有改变投资产品语义。数据迁移、工具适配和 Provider
移动分别接受 focused contract tests，最终再统一恢复完整质量门。

## 1.1 实施结果（2026-07-26）

| 工作包 | 结果 |
|---|---|
| A | Post-market CLI 已具备 `run/status/catch-up/help`、统一 due-session 规则和脱敏心跳；实机补跑及重复跳过成功 |
| B | Strict Challenge start/resolve 使用 payload hash + 持久化 idempotency，migration `0021` |
| C | 五个 workflow 在 Provider 前写 STARTED/RUNNING receipt，并持久化有界 hash fact artifact，migration `0022` |
| D | `ActorContext` 已区分 `CALLER_ASSERTED`/`AUTHENTICATED` 并阻断 principal mismatch；stdio 仍明确为 caller-asserted，Codex 聊天转交另记 channel 与原始授权语句 |
| E | `server.py` 仅保留 lifecycle；库存集中在 `tool_inventory.py`；后续 compact R7 删除 registrar/HandlerRegistry，由 capability operation adapters 直接组装精确 28 个工具 |
| F | `RuntimeResources`、`ProviderBundle`、`ApplicationServices` 已成为显式 bundle；旧平铺字段作为迁移兼容面保留 |
| G | Eastmoney、Sina、typed codecs 已物理拆成 client/common/fact-capability 模块；旧 import path 由薄 façade 保持 |

## 2. 冻结边界

所有工作包共同遵守：

1. 当前公共 MCP 工具精确为 compact 28；response envelope 与业务语义不漂移。
2. 不新增 order、fill、position mutation、backtest 或 runtime LLM。
3. 账户和 Watchlist 默认 durable-first；只有显式刷新或外部 post-market job 访问上游。
4. Provider raw payload 不越过 infrastructure；持久化 replay 只能保存已标准化、已脱敏、大小受限的 Tool Envelope 数据。
5. Domain 不导入 MCP/SQLAlchemy/Provider；Application 不导入 infrastructure/interfaces。
6. 结构重构与数据迁移分开提交；不得在同一 PR 中同时移动大量模块和改变业务 schema。
7. `.env`、token、账户原始标识和浏览器 OAuth 不进入日志或 replay artifact。

## 3. 目标结构

```text
external scheduler
  -> post-market CLI (due/status/catch-up)
      -> PostMarketSyncService
          -> account refresh -> durable snapshot
          -> exact Watchlist sync -> durable state
          -> terminal receipt + observable exit status

MCP runtime
  -> server.py (lifecycle only)
      -> tool_inventory.py
      -> tools/{research,market,portfolio,workflow,risk_monitoring,...}.py
          -> application service protocols

bootstrap.py (cross-layer composition only)
  -> infrastructure provider/resource bundle
  -> application service graph
  -> ApplicationContainer + deterministic close order
```

## 4. 工作包 A — Post-market Automation 可观测性

这是最高优先级，因为 2026-07-26 实测发现：账户/Watchlist 刷新主体成功，但最近 terminal
receipt 停在 `2026-07-23`，而 `2026-07-24` 是应执行的 XNYS session。

### A1. CLI 契约

把当前无参数 CLI 改为显式子命令，同时保持无参数行为兼容：

```text
trading-partner-post-market-sync run       # 默认；现有 due-check 行为
trading-partner-post-market-sync status    # 只读；比较最近应完成 session 与 terminal receipt
trading-partner-post-market-sync catch-up  # 仅补最近一个已收盘且缺 receipt 的 session
trading-partner-post-market-sync --help
```

- `status` 不访问 broker/OpenD，输出一行 JSON；健康返回 0，缺失/失败 receipt 返回非零。
- `catch-up` 只允许最近一个已关闭、已到 delay、且没有 `SUCCEEDED` receipt 的 session；不接受任意历史日期，避免大范围重放。
- `catch-up` 复用现有进程锁、账户优先顺序和 per-session 幂等 receipt；它不是绕过账户认证或交易日历的通用 `--force`。
- stdout 始终只有一个 terminal JSON；配额等待进度每 15 秒最多一条写到 stderr，不包含账户、持仓或 secret。
- Schwab 认证失败只返回 typed error，后台任务不启动浏览器 OAuth。

### A2. Repository 和调度健康

- `PostMarketSyncRunRepository` 增加 `get_latest()`；不把 SQL 查询放进 CLI。
- Calendar 增加 `latest_due_session(now, delay)` 查询，统一 `run/status/catch-up` 对“应执行 session”的定义。
- `status` 输出 `expected_session_date`、`receipt_session_date`、step statuses、attempt count、warning/error codes；不输出 snapshot/account ID。
- 外部 Automation 在正式盘后候选时间调用 `run`，在日间健康检查调用 `status`；后者失败应形成用户可见告警。

### A3. 验收

- 非交易日 `run` 不访问 Provider；`--help` 绝不执行服务。
- 漏失最近 session 时 `status` 确定性失败，`catch-up` 只执行一次并产生同一 session receipt。
- 第二次 `catch-up` 返回 already-completed，不重复访问账户或 Watchlist。
- 实机 smoke 保留账户/持仓数量和同步关系数，不打印金额与具体标的。

## 5. 工作包 B — Challenge Review 请求幂等

### B1. 数据模型

- Strict `challenge_review_start` 新增必填 `idempotency_key` 和 canonical payload SHA-256；
  `DISCUSSION` 仍不持久化且不要求 key。
- 新建 append-only `challenge_review_resolutions` 表：
  `resolution_id`、唯一 `review_id`、唯一 `idempotency_key`、payload hash、resolution、rationale、confirmed actor、resolved_at。
- 已有 resolved review 在 migration 中回填一条 resolution；旧列先保留一个版本周期，只读 hydrate 优先新表。

### B2. 重放语义

- 相同 key + 相同 payload 返回原 review/resolution。
- 相同 key + 不同 payload 返回 `IDEMPOTENCY_CONFLICT`。
- 同一 review 用另一 key 再次 resolve 返回 `CHALLENGE_REVIEW_ALREADY_RESOLVED`。
- start 的 repository 写 review/questions/findings/idempotency receipt 必须在一个 transaction。

### B3. 迁移顺序

1. 先 additive migration 和双读；
2. 再切换写路径并回填；
3. 一个发布周期后才考虑删除旧 resolution 列。

## 6. 工作包 C — Workflow 精确重放与中断可见性

### C1. Receipt 状态机

现有 `research_runs` 从 terminal-only 扩展为：

```text
STARTED -> RUNNING -> SUCCEEDED | PARTIAL | FAILED
```

新增字段：`idempotency_key`、`request_sha256`、`lease_expires_at`、`last_heartbeat_at`、
可选 `terminal_error_codes`。在第一次 Provider 调用前先提交 `STARTED` receipt。

### C2. 标准化 fact artifact

- 新建 `research_run_fact_artifacts`，按 `(run_id, ordinal)` 唯一。
- 只保存 Tool Envelope 中标准化后的 `data/sources/warnings/errors/as_of/fetched_at`；禁止 raw payload。
- canonical JSON、schema version、SHA-256 和明确字节上限；超限则保存稳定 artifact reference 或显式 `WORKFLOW_FACT_NOT_PERSISTED`，不能静默截断后声称精确重放。
- 账户引用必须已经是项目脱敏 hash；再次经过 SecretRedactor 防御性检查。

### C3. 幂等行为

- 相同 key + payload hash，terminal run：从 artifact 精确重建原 `WorkflowRunDTO`，不访问 Provider。
- 相同 key + 不同 hash：`IDEMPOTENCY_CONFLICT`。
- 有效 lease 内重复请求：`WORKFLOW_RUN_IN_PROGRESS`，返回已有 run ID。
- 失效 lease 不自动接管；需要显式 resume/catch-up 设计，避免两个 host 并发重复 Provider 请求。
- Step 完成后立即原子写 step receipt + fact artifact，使进程中断位置可见。

## 7. 工作包 D — 调用者信任边界

这是宿主/传输依赖项，不能通过验证字符串等于 `"user"` 来假装解决。

### D1. 应用模型

- 引入不可伪造的 `ActorContext(actor_type, principal_id, assurance, request_id)`；应用写服务接收它，不直接信任 DTO 中的 `confirmed_by`。
- `confirmed_by` 可在一个兼容周期内保留为“调用者声明”，但有 trusted principal 时必须一致；不一致返回 `CONFIRMER_MISMATCH`。
- Audit 只保存稳定、脱敏 principal reference，不保存 access token、邮箱或账户名。

### D2. Transport gate

- HTTP/SSE host 从已认证连接产生 `ActorContext`。
- 本地 stdio 没有身份协议时继续标记 `assurance=CALLER_ASSERTED`；高影响确认操作不得被描述成经过身份认证。用户在当前 Codex 聊天中的明确决定以 `submitted_via=codex_chat` 和有界 `authorization_note` 留痕，既允许代理提交，也不把宿主转交伪装成 Codex 自主决定或认证身份。
- 在宿主能提供可信 principal 前，`AUTH-001` 保持 deferred，文档和 UI 明示该边界。

## 8. 工作包 E — MCP façade 模块化

### E1. 单一库存源

- 把工具集合移到 `interfaces/mcp/tool_inventory.py`，按 capability 分组并导出唯一
  `PUBLIC_TOOL_NAMES` / `FORBIDDEN_PUBLIC_TOOL_NAMES`。
- tests、wheel smoke 和 delivery audit 全部引用这个库存，不再复制数字 52 或工具集合。

### E2. Feature registrar

```text
interfaces/mcp/
├── server.py                 # FastMCP lifecycle、logging、stdio main
├── tool_inventory.py
├── validation.py             # model_validate + sanitized unexpected failure
└── tools/
    ├── research.py
    ├── research_memory.py
    ├── a_share.py
    ├── market_technical.py
    ├── us_research_context.py
    ├── portfolio.py
    ├── workflows.py
    ├── watchlist.py
    └── risk_monitoring.py
```

该方案首先以 `register_<capability>_tools(server, services)` 完成拆分；后续 compact R7 将其
替换为 `build_<capability>_adapters(services)`。内部 helper 可以存在，但不会注册成额外 public
tool；`server.py` 不包含业务 DTO 组装。

**最终实现：** `server.py` 是生命周期入口，`compact.py` 负责唯一 28-tool 组装；研究状态、
研究记忆、A-share、市场技术、US research/context、portfolio、risk、monitoring、workflow、
watchlist 和 challenge 由 capability operation adapters 持有。旧 `tools/core.py`、
HandlerRegistry、ToolRegistrar 和 `@server.tool` 收集点均已删除。

### E3. 防漂移验收

- 精确 28-tool inventory、forbidden/retired inventory、stdio smoke 和 wheel smoke 持续通过。
- 架构测试禁止恢复 `handler_registry.py`、`ToolRegistrar`、`HandlerRegistry` 或 capability
  模块内的 `@server.tool` 收集点。
- 所有本地 schema `$ref` 可解析，输入 schema 总量保持在 40 KiB 门禁内。

## 9. 工作包 F — Composition root 瘦身

保持“只有 bootstrap 跨 application/infrastructure 装配”的规则，但允许各层内部先形成 bundle：

- `infrastructure.composition.build_runtime_resources(settings)` 只组合 infrastructure 内部对象，返回 `RuntimeResources` / `ProviderBundle`。
- `application.composition.build_application_services(ports)` 只组合 application services，参数全是 ports/纯对象，不导入 infrastructure。
- `bootstrap.py` 负责把两者连接、构造 `ApplicationContainer` 和声明关闭顺序。

Container 分为三个显式 bundle，而不是一百余个平铺字段：

```text
ApplicationContainer
├── services: ApplicationServices
├── providers: ProviderBundle
└── resources: RuntimeResources  # database/transports/locks; owns close()
```

迁移期间提供只读 compatibility properties，逐个调用点改完后再删除。关闭顺序由
`RuntimeResources.aclose()` 单测，禁止靠字段命名或 `getattr` 猜测资源生命周期。

**已实现：** 三个 bundle 均为显式 dataclass，资源关闭具备去重和幂等测试。为避免一次性
改写所有 Phase 1–3D 调用点，`ApplicationContainer` 暂保留平铺 compatibility 字段；新增
代码应优先使用 `services/providers/resources`。

## 10. 工作包 G — Provider 和 codec 垂直拆分

先拆 Eastmoney，再用同一模式拆 Sina；公共 VendorId 和 CategoryProvider 行为不变。

### G1. Eastmoney

保留 `EastmoneyAShareAdapter` 作为薄 façade，委托给：

```text
eastmoney/
├── client.py              # gate、headers、HTTP/status/content-type、meta
├── quote_bars.py
├── market_structure.py
├── fundamentals.py
├── research.py
├── capital.py
└── sentiment.py
```

分页、current-only/as_of gate 和时间边界 helper 只能有一个 owner。先写现有 contract test 的
characterization fixtures，再移动实现；禁止顺手改变 endpoint 参数或 fallback 语义。

### G2. Typed codecs

按市场、研究、资金、情绪、期权拆文件；`codecs.__init__` 保持现有 factory import 路径。
cache schema/version 和 canonical JSON 必须字节兼容；golden cache fixture 覆盖旧数据解码。

**已实现：** Eastmoney façade 保留 E2 market-board 的模块级分页参数兼容，其他 endpoint
分别进入 `quote_bars/fundamentals/capital/sentiment`；Sina 分为
`daily_flow/financials/options`；codec 的旧 `typed.py` 仅作 79 行兼容 façade，真实实现位于
`base/market/research/capital/sentiment/options`。Provider contract 与 codec security tests
覆盖原有 cutoff、error mapping、canonical JSON 和 fallback 行为。

## 11. 推荐实施顺序

| PR | 内容 | 数据迁移 | 主要退出条件 |
|---:|---|---|---|
| 1 | Automation `help/status/catch-up`、进度和漏跑诊断 | additive repository method only | 实机 status 能识别缺失 2026-07-24 receipt |
| 2 | Challenge Review 幂等 | 是，单独 migration | start/resolve retry 精确重放 |
| 3 | Workflow STARTED receipt + fact artifact | 是，单独 migration | 终态重放不访问 Provider，中断位置可见 |
| 4 | compact inventory + capability adapters | 否 | 精确 28 tools、schema/local-ref 门禁通过 |
| 5 | Application/Infrastructure bundles + Bootstrap 收口 | 否 | 生命周期、overrides、isolated wheel 无变化 |
| 6 | Eastmoney façade拆分 | 否 | 全部 provider contract/时间 cutoff golden 通过 |
| 7 | Typed codec / Sina 拆分 | 否 | cache 字节兼容和 fallback 行为不变 |
| 8 | Trusted ActorContext enforcement | 视宿主能力 | authenticated principal mismatch 可阻断；stdio 边界明确 |

不要并行实施 PR 2/3 的 migration 与 PR 4–7 的大规模移动；否则回滚、审查和 blame 都会失去边界。

## 12. 每个工作包的统一验收

```bash
uv sync
uv run ruff check .
uv run mypy src
uv run pytest
uv run alembic upgrade head
uv run python scripts/smoke_isolated_wheel.py
```

另外必须满足：

- `git diff --check`；
- 临时数据库 `upgrade head -> downgrade -1 -> upgrade head`（有 migration 的 PR）；
- exact 52 inventory 和 schema golden；
- 不读取/打印 `.env`，不启动后台 OAuth，不出现 order surface；
- 实机 Provider smoke 只输出数量、状态、时间和脱敏代码。
