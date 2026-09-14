# 本地操作控制台与数据维护

## 控制台

控制台完全在本机运行，API 只允许绑定 `127.0.0.1` 或 `localhost`。普通页面读取不调用
Provider 或 LLM；只有用户显式运行 Agent、启用复合判断的 Monitor、Observation 分析等入口
才调用配置好的服务端模型。它不是只读看板：用户可以主动运行到期 Monitor、账户/交易同步、收盘后任务、通知、
备份和缓存清理，也可以从 MCP 工作台调用全部 24 个公开工具。它不会在页面加载时隐式
访问 Provider，也不提供订单能力。

终端一：

```bash
uv sync --extra console
uv run trading-partner-console
```

终端二：

```bash
cd console
npm ci
npm run dev
```

打开 `http://localhost:3000`。浏览器前端会自动取得当前 Console 进程的短期会话令牌，
正常页面操作不增加登录、复制令牌或二次配置步骤；API 进程重启后，前端也会自动刷新令牌。
为保持本机授权边界，前端 Origin 固定为 `http://localhost:3000` 或
`http://127.0.0.1:3000`，不要接受开发服务器自动换用其他端口。直接编写脚本调用写接口时，
需先读取 `GET /api/session`，再把返回的令牌放入
`X-Trading-Partner-Console-Token` 请求头；只读 GET 不需要该请求头。

### Console 与 Agent 本机 supervisor

需要让 Console API（回环 `127.0.0.1:8765`）和 Next production 前端由 macOS
launchd 管理时，使用共享 Agent CLI。安装前会检查或安全执行 `console` 的 production
build；两个 job 均设置 `RunAtLoad`、`KeepAlive` 和短 `ThrottleInterval`，日志只写到
`data/logs/`，plist 为 owner-only。命令不会杀宽泛进程，也不会把环境变量或密钥写入状态：

```bash
uv run trading-partner-agent console install
uv run trading-partner-agent console status
uv run trading-partner-agent console restart
uv run trading-partner-agent console uninstall

```

`status` 只报告 `installed`、`loaded`、`running`、`pid`、`start_time` 和
`last_exit`。`restart` 明确使用 `launchctl kickstart -k`。Agent 的持久偏好通过
`/preferences` 读取；写入必须显式携带 `version`、`idempotency_key` 和
`authorization_note`，且仅允许语言、回答密度、来源代码、风险风格和默认图表等
presentation 字段。Web Search 默认开启，不提供偏好开关：所有 Agent 模型通过私有
`tp_web_search` 使用服务端 Tavily Search sidecar。回答模型不会收到 sidecar API key，
搜索不会额外调用百炼模型；网页只作不可信背景，不能覆盖 Trading Partner 事实。Console
对应接口为：

```text
GET  /api/agent/preferences
PUT  /api/agent/preferences
POST /api/agent/preferences/reset
GET  /api/agent/preferences/history?limit=100
GET  /api/agent/conversations/{conversation_id}/metrics
```

偏好会以明确标注的 presentation-only system context 注入 Agent，不得被模型当作事实、
记忆、授权或交易意图。Metrics 只从 durable `model_receipt_json` 与 turns 汇总，最多采样
500 条；超限时返回 `truncated=true`，畸形 receipt 被忽略并计入 warning。

行为门禁使用真实 Agent runtime 的确定性 fake fixtures，覆盖版本化 catalog cases：

```bash
uv run trading-partner-agent eval
```

默认不访问网络、不调用券商，也不放开订单；回执包含每个 case 的 pass/fail、tool trace、
逐场景行为断言、schema repair 结果、失败原因和关键 prompt/runtime/capability 源码
fingerprint。真实 Provider smoke 由操作者单独以有界只读请求执行；`--live` 当前 fail closed，
不会意外联网或调用 LLM。

### Copilot 研究模型选择

当前 Go 合成证据实测支持日常 Research 优先使用 `deepseek-v4.1-flash` 的 `high` 档；
重要判断可手动选择 `grok-4.6` 的 `high` 档复核，`qwen3.8-flash` 的 `max` 档作为备选。
这不是自动跨模型复核或故障切换配置。Grok 的 `max` 在探测中返回请求拒绝，
模型目录列出的档位不代表当前路由一定接受。这个选择基于字段与解释交付，
不代表投资预测准确率。使用 Composer 的 Provider、Model、Reasoning Effort 选择器；
模型与档位按浏览器保存，不会更改 Monitor 或私人笔记分析的共享服务配置。
Research 事实由程序按引用生成，解释保持推断；精确数值和日期应放在事实块。

### OpenCode Zen / Go Provider（可选）

OpenCode Zen 与 Go 在 Console 中是两个独立 Provider，拥有不同的模型目录、Base URL
和计费/entitlement 路径，但默认共用同一把 OpenCode 账户 API key。按
[OpenCode Zen](https://opencode.ai/docs/zen/) 或 [OpenCode Go](https://opencode.ai/docs/go/)
的控制台连接流程复制 API key，再写入项目私有 `.env`：

```dotenv
OPENCODE_API_KEY=
OPENCODE_GO_BASE_URL=https://opencode.ai/zen/go/v1
OPENCODE_GO_MODEL=deepseek-flash
EXTERNAL_NOTE_ANALYSIS_MODEL=deepseek-flash
EXTERNAL_NOTE_ANALYSIS_TIMEOUT_SECONDS=120
OPENCODE_ZEN_BASE_URL=https://opencode.ai/zen/v1
OPENCODE_ZEN_MODEL=gpt-5.6-luna
```

配置公共 key 后，Console Agent 下拉框同时显示 `opencode_go` 与 `opencode_zen`，并从
各自服务端短时缓存 `/models` 目录。`OPENCODE_GO_API_KEY` 与
`OPENCODE_ZEN_API_KEY` 仅作为可选的 Provider 专用覆盖。目录可读不等于某个付费模型已有
推理 entitlement；401/403 仍作为 typed Provider error 返回。若要让 Monitor 以其中一个
作为主判断层，另设：

```dotenv
LLM_PROVIDER=opencode_go
MONITOR_JUDGMENT_ENABLED=true
```

Go 的 `muse-spark-1.2-contributor`、`muse-spark-1.3-contributor` 以及 Grok 4.5/4.6
使用 Responses API。Contributor 模型允许使用提示词和补全训练未来模型，并受 Meta 地域
政策限制；只有所有者明确接受该条款并配置
`EXTERNAL_NOTE_CONTRIBUTOR_TRAINING_OPT_IN=true` 后，才允许任一 Contributor 模型接收
私密研究内容。订阅限额、模型
entitlement、地域限制与上游故障会保留为 typed Provider error，
不会静默切换成行情事实或安静规则。该适配不会读取 `~/.local/share/opencode/auth.json`，
不会把任一 API key 发给浏览器，也不发布原生 Web Search。

所有 OpenCode Go HTTP 请求（Chat Completions、Responses、Messages、流式响应与模型
目录）都会携带 `x-opencode-session`。Agent 对话使用 Conversation ID，笔记解释/升级复核
使用 Note ID，Monitor 判断使用 Monitor ID，Trade Retro 使用幂等键作为稳定工作流身份；
适配器发送前统一做不可逆散列，所以这些内部 ID 不会原样离开进程。同一工具循环、重试或
结构修复复用同一值，不同工作流不会有意共用会话。该要求仅适用于 Go，Zen 请求不附加此头。

Journal 的 `Refresh Sources` 只读扫描本机 Moomoo 缓存并快速返回；新 revision 的私有正文会在
后台发送给单独配置的 OpenCode Go `deepseek-flash`（DeepSeek V4.1 Flash），使用 `max` 推理强度与
120 秒单次超时。该授权只覆盖笔记结构化草稿，关闭 Web Search，且不能确认 Research 状态、
创建 Monitor 或授权订单。未署名段落确定性视为本人观点，明确姓名前缀保留为外部观点。

当前主调用统一使用 `deepseek-flash / max`：两层笔记处理、Monitor 综合判断和事件解释、
Trade Retro，以及 Console Agent 默认模型。设置 `LLM_PROVIDER=opencode_go` 后，Monitor、
事件解释、复盘与 Agent 复用 `OPENCODE_GO_MODEL`；笔记的两个模型字段独立配置。
Go 的 Chat Completions 与 Responses 请求不发送客户端输出 token 上限，包括服务内原有的
384/5000/8000 预算；普通调用、流式和重试策略一致。平台自身限制、超时与输出结构校验保留。
Messages 协议仍保留其必需的 `max_tokens`；Zen 和其他 Provider 的预算不变。
Console 会保留浏览器中主动选择的 Provider/模型；默认值更新不覆盖已有选择，需在模型菜单
选择 `opencode_go` → `deepseek-flash` 才会改变该浏览器的既有选择。

升级复核默认配置：

```dotenv
EXTERNAL_NOTE_REVIEW_MODEL=deepseek-flash
EXTERNAL_NOTE_REVIEW_REASONING_EFFORT=max
```

Contributor 是可选替代模型。所有者明确接受训练条款时，才可使用：

```dotenv
EXTERNAL_NOTE_REVIEW_MODEL=muse-spark-1.3-contributor
EXTERNAL_NOTE_REVIEW_REASONING_EFFORT=high
EXTERNAL_NOTE_CONTRIBUTOR_TRAINING_OPT_IN=true
```

`high` 是 Muse 1.3 的推荐档位。Muse 1.3 的模型菜单也提供 `xhigh`，选定档位原样发送到
Responses API。当前 Go 路由拒绝 `reasoning.effort=max`（HTTP 400），因此菜单不发布
该档位，也不把它静默映射成 `xhigh`；待上游支持后再更新能力目录。
早期单例比较中 `xhigh` 更慢且出现 PULLBACK/EXIT 分类差异，该结果不代表完整质量排名。
OpenCode Go 的 `omen-alpha` 从实时模型目录发现，显式使用 Chat Completions 路由，
可在 Agent 模型菜单选择，也可作为独立配置的 review model；接入本身不会切换生产模型。
关闭 opt-in 或将其遗漏会在配置阶段 fail closed，不会静默把私人正文发送给 Contributor。
升级复核的结构错误最多修复一次；忠实度守卫拒绝的时间、数字或行动条件立即失败，
保留精确错误码，不进入结构修复重发。失败草稿不被采纳；复核任务数不等于模型请求数。

该入口现已扩展为 provider-neutral `Refresh Sources`。可用
`uv run trading-partner-observation-sync` 同步全部配置来源，或通过 `--source MOOMOO_NOTE`
限制单个 adapter。不能直接读取的来源可按
`docs/contracts/observation-source-v1.schema.json` 将完整正文 JSON 放入
`data/observations/inbox`；文件保留原 source code，进入同一不可变 revision、作者归属、模型草稿
与 Decision review 链路。列表摘要不能通过该 bridge 冒充全文。

`data/observations/` 是 owner-only 运行数据并被 Git 整体忽略。安装版的相对路径统一从
`runtime.env` 所在的 runtime root 解析，而不是从 Wheel/虚拟环境目录解析。账户成本校正同样只
允许保存在 runtime `data/secrets/account_basis_checkpoints.yaml`；仓库中的
`config/account_basis_checkpoints.example.yaml` 仅为空结构示例。

Moomoo 笔记还支持一个不控制桌面 UI 的可选只读增补层。它使用用户主动提供的原始 Cookie
header 请求内部 note-list 与 editor HTML，并从 `window.__INITIAL_STATE__` 读取正文。Cookie
只保存在 owner-only 的 `data/secrets/moomoo_notes_cookie.txt`，不进入 `.env`、数据库、日志、
错误信息或 Console payload。不要把 Cookie 作为命令行参数；从标准输入写入，避免 shell history：

```bash
# Copy a Cookie header from a separately authenticated Moomoo Web session.
# The desktop app's CEF Cookie database contains only locale/presentation state.
pbpaste | uv run trading-partner-moomoo-notes-cookie set
uv run trading-partner-moomoo-notes-cookie status
uv run trading-partner-observation-sync --source MOOMOO_NOTE
```

每个 HTTP 请求前都会在
`MOOMOO_NOTES_REQUEST_DELAY_MIN_SECONDS`–`MOOMOO_NOTES_REQUEST_DELAY_MAX_SECONDS`
之间重新抽取随机等待，全部串行执行，并受 stock/note 数量上限约束。缺少或过期 Cookie、429、
网络错误或内部页面结构变化只产生闭合 warning 并回退缓存；摘要仍为 `SUMMARY_ONLY`。该接口
不是 Moomoo OpenAPI，可能随版本变化，不得用于写回、发帖或任何交易操作。
Moomoo 桌面原生桥接注入的会话票据不会写入标准 CEF Cookie 数据库。禁止用 Computer Use、
坐标点击或 UI 自动化读取 Moomoo；经用户明确授权，可只读分析进程元数据、本地 IPC 与网络协议
形态，但不得修改应用、系统代理/证书、账户或交易状态，也不得显示或持久化恢复出的凭据。
缓存中的完整 editor HTML 仍可只读恢复。

Agent 模型请求失败会在对话区显示专用通知卡，并随 durable turn 保留。通知只包含
Provider、模型、内部错误码、安全 HTTP 状态、是否可重试、尝试次数与固定说明。例如
HTTP 400/422 显示不可重试的 `PROVIDER_REQUEST_REJECTED`，HTTP 401 表示 Key
认证失败，HTTP 403 明确表示模型 entitlement/地域/账户策略拒绝，HTTP 429 显示
`PROVIDER_RATE_LIMIT_ERROR`，HTTP 503 显示 `PROVIDER_UNAVAILABLE_ERROR`。通知不会显示
endpoint URL、响应正文、异常文本、请求体、headers 或 API key。
通知固定显示在会话滚动区上方，关闭后会在本机保留最近 50 个 turn 的展示偏好；关闭
通知不会删除失败 turn、错误码或 Provider 审计，新失败仍会自动出现。

Zen 的 Ox Alpha Free 对应模型 ID `x-preview-f-free`。该模型支持 `low`、`high`、`max`
三档 reasoning effort；选择 Auto 时不发送显式档位，由 Zen 使用默认推理配置，不表示关闭
thinking。其他免费 Chat 模型仍按各自目录能力决定是否显示档位。

Composite Monitor 与 Agent 的默认模型分开配置。Monitor 默认使用：

```dotenv
MONITOR_JUDGMENT_MODEL=deepseek-v4-flash-0731
MONITOR_JUDGMENT_FALLBACK_PROVIDER=bailian
MONITOR_JUDGMENT_FALLBACK_MODEL=qwen3.8-max
MONITOR_JUDGMENT_FALLBACK_REASONING_EFFORT=high
```

主模型走百炼 Chat Completions 并携带 `response_format=json_object`。若首次内容不是完整
合约 JSON，只允许一次 structure-only 重试；重试不得改变 feature snapshot、确认状态或
事实判断。备用 Qwen 继续走百炼 Responses。

选择 OpenCode Go/Zen 作为 Monitor Provider 时，Chat Completions 路由优先使用唯一的
function call 及其参数 JSON Schema，Responses 路由优先发送 strict JSON Schema；若兼容网关
明确拒绝结构参数，才降级为 JSON-object 模式。系统只投影 schema 已声明字段，再执行本地
闭合枚举、必填字段、长度、数量与证据校验；不合约输出只允许一次 structure-only 修复，
不能从自由文本猜测或补造判断。

### 受保护的局域网访问（可选）

需要从同一可信局域网中的手机或另一台电脑访问时，后端仍保持在
`127.0.0.1:8765`，只把 Next.js 前端绑定到 `0.0.0.0:3000`。前端通过同源代理访问后端，
浏览器不能直接连接数据 API；所有页面和代理接口先验证 HttpOnly、SameSite 会话 Cookie。

先按上文在终端一启动后端。终端二用非空密码启动 LAN 模式：

```bash
cd console
read -rs "TRADING_PARTNER_CONSOLE_LAN_PASSWORD?LAN password: " && echo
export TRADING_PARTNER_CONSOLE_LAN_PASSWORD
npm run dev:lan
```

启动信息会列出可用的 `http://<Mac局域网地址>:3000`。其他设备打开该地址后先进入登录页；
登录会话有效 12 小时，也可从左下角主动退出。可用
`TRADING_PARTNER_CONSOLE_LAN_PORT` 改用其他 1024–65535 端口。

这个模式面向可信家庭/办公局域网，使用普通 HTTP，不适合公网、访客 Wi-Fi、端口映射或
云服务器。密码不要写入 URL、`NEXT_PUBLIC_*`、Git 或聊天记录。使用结束后按 `Ctrl-C`
停止前端并执行 `unset TRADING_PARTNER_CONSOLE_LAN_PASSWORD`。需要跨不可信网络访问时，
应另行使用带 TLS 和设备身份的私有网络方案。
密码至少 16 个字符；登录失败按客户端在有界窗口内限流，跨站 Origin 会被拒绝。

若希望 Console supervisor 每次启动时同时开启受保护的 LAN Web，安装持久模式：

```bash
uv run trading-partner-agent console install --lan
```

密码生成或复用于 owner-only 的 `data/secrets/console-lan-password`，LaunchAgent 只持有文件路径。
本机可用 `cat data/secrets/console-lan-password` 读取登录密码。此后普通
`uv run trading-partner-agent console restart` 会一起重启 loopback API 与 LAN Web；后端仍不
绑定 LAN 地址。可用 `--lan-port 3001` 选择其他端口。

页面包括：总览、全部
研究档案/Thesis、Journal、Judgment Scorecard、Catalyst Agenda、Trade Retro、Monitor 定义/Run/事件、24 个 MCP 能力、持久化账户、
同步/OAuth/通知/数据库/保留策略。

总览 Review Queue 是内部持久化决策闭环，不增加公开 MCP 工具。Acknowledge 可选填期限；
Resolve 必须填写关闭依据并可记录 resolution ref。每次写入带 Console session、expected
version、idempotency key 和用户授权说明。只有成功完成的 durable source projection 才能
触发自动关闭；Provider/数据库读取失败只显示降级，不得解释为问题已消失。

Research 页面使用研究标的索引和单个研究档案工作区：默认包含已归档研究档案，并展示所选研究标的的
Thesis、当前版本、假设、失效条件、开放问题、Trade Plan 与待审候选。读取只通过现有
`research_get/query` 与 `research_get/state` 聚合，不会请求行情 Provider。
用户可以创建、编辑或归档研究档案；研究档案编辑只修改标题、摘要、标签和关联研究档案。Thesis
修改始终先产生候选，再由用户显式确认或拒绝，不能覆盖已确认 revision。单个研究档案的研究
状态读取失败时，该研究档案仍保留并显示局部错误，不会让其他研究档案从页面消失。

Portfolio 页面使用 Holdings、Activity、Performance、Risk 四个稳定标签页。初始加载只聚合
持久化账户快照、交易、暴露、覆盖回执和风险状态，不访问券商或其他上游。账户和交易各有
独立的显式同步按钮；同步失败只影响对应区域。Holdings
按账户展示原币种现金、净资产、购买力、融资、快照时点、警告和持仓；Activity 保留交易与
覆盖缺口；Performance 只做可追溯的 FIFO/券商成本口径计算；Risk 支持当前政策、确定性检查、
手工假设新增或已确认 Trade Plan 试算。Watchlist 暂不在 Portfolio 前端展示，相关 MCP 能力
仍可在 Capabilities 工作台中使用。
所有写入继续经过 compact Registry 的 expected-version、confirmation 与 idempotency 校验，
页面不提供订单、隐含 FX 汇总或后台自动刷新。

Trade Retro 页面读取不可变的历史运行，不会刷新券商。用户应在周期开始前点击
`Prepare next week` 固化当前 Trade Plan 和 Decision Record；周期结束后点击
`Run previous week`，用持久化成交与覆盖回执做确定性纪律审计。可选模型只叙述已计算
Finding，失败不会丢失确定性结果。每个 Run 可展开查看完整摘要、Finding 和交易引用；
`Review` / `Edit review` 会追加一个人工复核版本，可修改整体复核状态、纠正说明、行动项和
逐 Finding 结论。保存需要显式确认，并以 `expected_version` 拒绝陈旧页面覆盖。原始 Run、
模型摘要和 Finding 始终不可改。`Export to Obsidian` 只替换配置目录中周记的 Trading Partner
marker block，并包含最新人工复核，不覆盖手写正文。命令行等价入口为：

```bash
uv run trading-partner-retro prepare \
  --start 2026-08-10 --end 2026-08-17 \
  --idempotency-key retro-plan-2026-w33
uv run trading-partner-retro run \
  --start 2026-08-10 --end 2026-08-17 \
  --idempotency-key retro-run-2026-w33 --export-obsidian
uv run trading-partner-retro history
uv run trading-partner-retro weekly --export-obsidian
```

如果不传日期，CLI 使用上一完整 UTC ISO 周。Obsidian 导出需要配置
`RETRO_OBSIDIAN_JOURNAL_DIR`；不开启 `TRADE_RETRO_LLM_ENABLED` 或模型不可用时，
确定性中文报告仍然可用。面向现有周六定时任务，`weekly` 使用固定的周一 00:00 UTC
至周六 00:00 UTC 窗口，完成审计/可选导出后再固化下一周同口径快照；Automation
不再自行解析周记或重建交易纪律结论。

Catalyst Agenda 页面只在用户点击时运行免费 Provider 同步；普通加载读取持久化 scope、
coverage、事项版本和 sync receipt，不刷新行情、账户或 Watchlist。用户可创建、修订、取消
事项，或把已发生事项链接到同研究范围的 Event/Report/Evidence；每次写入要求确认、幂等键
和 expected version。结果表单可从 durable timeline/search 选择候选事实，也可直接输入 ID；
OCCURRED 结果的补充或纠正会追加新 version。Yahoo/yfinance 日期是 current-only，FRED release date 不保证精确发布
时刻，失败和日期漂移不会被解释为“无催化剂”。页面可预览或入队一条移动端 Agenda
Telegram 摘要，发送仍复用 generic durable Outbox。

CLI 同样可在一次显式同步后入队并尝试发送摘要：

```bash
uv run trading-partner-catalyst-sync sync --window-days 30 --notify --flush
```

Judgment Scorecard 页面选择 Research Subject 与明确 Thesis 后生成不可变校准 run，并浏览
历史。S1 展示 revision 定义、evidence、失效条件、Trade Plan/Monitor、行动时序、Trade
Retro 和 Catalyst outcome 九个维度；它不生成总分，不回写 Thesis/Plan，也不调用 Provider
或 LLM。历史 run 按其原 algorithm contract 原样读取，不用当前事实重算过去。

已有历史周记可做一次性迁移：

```bash
uv run trading-partner-retro import-markdown \
  --path /absolute/path/to/WeekNN.md \
  --start YYYY-MM-DD --end YYYY-MM-DD \
  --idempotency-key legacy-retro-YYYY-wNN
```

该命令只提取 `## 2. Retro` 到下一个二级标题之间的原文，保存为明确标注的
`trade-retro-legacy-markdown-import-v1` 不可变 Run。它不会把旧文字伪造成结构化 Finding，
也不会宣称重新验证了成交覆盖；后续修订仍通过 Console 的 append-only Review 完成。

Monitor 页面提供专用编辑器，不需要手写 MCP JSON：可按市场和代码/名称解析规范
`instrument_id`，选择按需、整点间隔或 A 股/美股收盘后 cadence，并添加多条价格、
组合风险或事实比较条件。事实比较覆盖价格、成交量、技术面、基本面、公司事件、宏观、
情绪、Thesis 状态和组合风险；页面会根据事实类型提示 `metric_key`，并在提交前检查
必需标的、阈值、事件比较方式和数据时效。编辑现有 Monitor 会创建新版本，不覆盖历史。
事件流显示真实 `event_type`、严重度、观测值和阈值；用户可在页面填写审计说明后执行
`ACKNOWLEDGE` 或 `RESOLVE`，两者仍通过 `monitor_manage` 的确认与幂等门。
每条 Monitor 规则在创建和更新时必须填写具体释义；规则卡片将释义与机器 `rule_code`、
方向/阈值、严重度、当前观测和状态一起展示。Monitor 卡片分别显示原始创建时间和最近运行时间。
总览及 Monitor 页的最近 Run 会显示本次 observation 实际记录的标的代码；只有 Run 版本
与当前 Monitor 版本一致时才附带当前名称，避免 Monitor 改版后把旧运行误标成新标的。
失败 observation 的详情会直接展示结构化 Provider 诊断链：Provider、请求阶段、typed error、
HTTP 状态、attempt 和 retryability。诊断是为定位“解析失败、主源失败还是 fallback 失败”而
设计的，不保存 URL、代理地址、请求头、响应正文或异常原文。迁移 `0036` 之前的 Run 没有
诊断 sidecar 时，页面明确显示缺失，不能从顶层 warning 猜测具体失败环节。

能力工作台按选定 operation 的 schema 预填必需字段，并把
`technical_render_chart` 返回的 PNG image block 直接显示在结果区。账户页只按原币种汇总
持仓市值和未实现损益，不把持仓市值描述成 NAV，也不隐式做 FX 合计。

外部访问和写入操作要求用户明确点击确认。MCP 工作台仍经过原工具 schema、候选确认、
actor gate、expected version 和 idempotency 校验；前端不能把“点击运行”伪装成 Thesis、
Trade Plan 或研究记录的确认。缓存删除另有二次确认；Console 仍不提供真实下单，
确认门禁的 Schwab 下单只存在于 `broker_order_manage` MCP。

Console 的 MCP 工作台与 Codex MCP 不是两套业务实现：两种 transport 都由同一份
24-tool Capability Registry 提供 handler、请求 schema 和 effect policy。健康、账户、
自选、Research 及 Monitor 等一一对应的前端查询也通过 Registry 调用；`overview`、
`research`、`monitors` 等路由
只负责把多项读取合并成适合页面的 BFF 响应。收盘任务、通知、备份和缓存维护仍是
Console/CLI 专用 operational capability，不会为了接口对称而扩入公开 MCP。

通知同样保持在 operational CLI 边界：`trading-partner-notifications` 提供
`status`、`test`、`flush`，以及从 stdin 读取 UTF-8 正文的显式授权
`enqueue`；旧的 `trading-partner-monitor-notifications` 仍是别名。MANUAL
enqueue 必须带 `title`、幂等键、`user`/`external_agent` 确认者和授权说明，
JSON 回执不会回显正文或授权说明，也不会产生订单或其他交易状态效果。
内部确定性生产者使用封闭的 `SYSTEM` source；`MANUAL` 仅用于显式授权的调用者写入。

投递语义：durable Outbox 携带 Monitor 告警和显式授权的手工文本；重复的 Monitor
观测只留在 Run 历史，不会重复通知。每小时本地 dispatcher 重试 pending 消息，不打开
Codex 任务；统一 dispatcher 同时负责 A 股/美股/KR 收盘后 Monitor 执行，Codex
market-review Automations 不得重复 Monitor 评估或告警。真正产生状态转换的 Monitor 会在
通知末尾增加一次不超过 160 个中文字符的模型分析；同一 Monitor 同轮多个事件合并一次，
无变化不调用模型，使用 `max` 推理强度；80 秒超时或格式失败不会阻止确定性通知。显式启用 composite judgment
policy 的 Monitor 会复用同轮成功判断摘要，避免第二次调用。Trade Retro 叙述只接收已持久化的
确定性事实；搜索用量与有限来源 URL 会被持久化，价格/账户事实始终由确定性 Provider
所有。完整契约见 AGENTS.md。

独立的美股盘后编排任务在交易日收盘十分钟后运行账户、Watchlist、Observation 与
`US_POST_MARKET` Monitor 链路，并以交易日回执保证幂等。launchd 使用精简环境；外层任务
可由绝对 `uv` 路径启动，但 Python 编排器的子任务必须通过当前 `sys.executable -m` 调用，
不得再次依赖 `PATH` 查找 `uv`。运维验收需要同时检查最新 durable receipt 与 launchd
`last exit code = 0`，其中任何一项成功都不能单独证明自动链路完整。

Schwab SGOV 自动现金管理属于 operational capability，不增加 MCP 工具：

```bash
# 立即刷新 Schwab 并在终端显示所有账户的购买计划表（不通知）
uv run trading-partner-sgov-plan preview

# 安装即持久授权；检查 SGOV-only launchd 自动买入调度
uv run trading-partner-sgov-plan-scheduler install
uv run trading-partner-sgov-plan-scheduler status

# 撤销后续自动买入授权
uv run trading-partner-sgov-plan-scheduler uninstall
```

普通交易日 15:45 America/New_York 只做准备检查；15:55 再刷新并自动提交合格账户的
`SGOV BUY LIMIT · DAY · NORMAL`，官方提前收盘日分别使用收盘前 15 和 5 分钟。
launchd 每小时 `:45` 与 `:55` 只做本地到期判断，休市日和其他时段不访问 Schwab。
每账户按 `$3,000 + $200 + active BUY reserve` 保留现金，且提交前再次检查 margin、
现金、bid/ask、30 秒报价年龄和 `$0.02` 最大价差。每账户/交易日使用稳定的 preview/
submit 幂等键；`SUBMITTING`/`UNKNOWN` 不会自动重试。完成结果通过 SYSTEM Outbox
发送，回执延长至收盘后 24 小时；不调用 Codex/LLM。此授权不包含卖出、撤单、改单、
其他标的或盘前盘后/overnight 订单，其他实盘动作继续逐单确认。

## 数据维护

查看状态和保留策略：

```bash
uv run trading-partner-maintenance status
```

创建 owner-only SQLite 在线备份：

```bash
uv run trading-partner-maintenance backup
```

缓存清理默认 dry-run，只有显式 `--apply` 才删除超过保留期的已过期 Provider/Reddit 缓存：

```bash
uv run trading-partner-maintenance prune-cache --retention-days 30
uv run trading-partner-maintenance prune-cache --retention-days 30 --apply
```

Monitor Run/observation/event、研究记录、交易和账户快照都不自动删除。数据库备份也由
使用者显式管理，避免静默丢失审计历史。

### 券商对账草稿（owner-only）

`trading-partner-performance-reconciliation` 只接受 `data/artifacts/reconciliation/`
目录下的相对 CSV 路径，读取时把文件限制为 owner-only 权限，输出哈希与脱敏账户摘要，
不回显原始行或账户标签。`compare-schwab-realized` 是 durable-only 对账：不刷新 Schwab，
把逐标的费后 FIFO 残差和 typed 缺口写入 `receipts/` 下的 owner-only JSON 草稿；相互抵消的
标的残差不能被账户级零总额掩盖。草稿匹配或命令成功都不构成 A1 sign-off，账户与标的
残差仍需人工复核。

## 数据集维护脚本

`scripts/` 下的两个生成器用于维护内置数据集快照，均无订单效果：

```bash
# A-share 交易日历候选（确定性：2024–2026 固定节假日 + 周末排除，无网络访问）
uv run python scripts/generate_a_share_trading_calendar.py --check
uv run python scripts/generate_a_share_trading_calendar.py --stdout
uv run python scripts/generate_a_share_trading_calendar.py --write --force

# CNINFO orgId 映射快照（--check 离线校验；--refresh/--write 拉取官方清单并重写版本化快照）
uv run python scripts/generate_cninfo_org_map.py --check
uv run python scripts/generate_cninfo_org_map.py --refresh --write
```

两者默认拒绝覆盖已跟踪文件，需显式 `--force` / `--write`；刷新后按 diff 审阅再提交。

### Console account aliases

Console reads optional owner-managed labels from `RUNTIME_ROOT/data/console/account-aliases.json`
through the read-only `/api/account-aliases` endpoint. The JSON object maps exact durable
`account_ref` values to display labels (at most 80 characters). Keep the file owner-only
and outside Git. Portfolio, Journal account filters, Cycles, activity paths, Cycle
adjustments, and Overview account notices share these labels. Unmapped accounts retain
the Provider and a short reference suffix. Aliases never change identifiers, broker
account types, filters, or transaction attribution; bind them once rather than recalculating
them from changing balances. Reload Console after changing the file.

### Journal result and filter scope

Journal keeps aggregate result metrics in Behavior; Overview does not recalculate a
second Results panel. Period and Account filters remain available while durable reads
refresh. Custom ranges require both dates and reject reversed ranges before querying;
Clear Filters restores all-history/all-account scope. Date changes reset browser
pagination. Behavior receives exact account references and aware start/end timestamps;
account aliases affect labels only. The Cycle browser uses close time (open time for
open Cycles), while transaction lists use activity occurrence time. Existing bounded
history and coverage warnings still apply; filtering is not a broker refresh.

### Observation → Research draft carryover

**Open Research** from a selected Observation keeps its exact revision as the source.
Research displays the stored model draft, source version/model, USER judgment, level
references, other speakers, and the complete parsed review. It reuses a successful
escalated draft when available and otherwise the first-pass interpretation; navigation
never invokes a model or adopts a Decision.

After **Create Research Subject**, the DRAFT Thesis editor opens with the parsed
USER statement and supporting text. **Use Review in Thesis** and **Use Review in
Plan Draft** prepare editable drafts for a matching existing Subject. The Plan draft
keeps point text in notes and USER scenarios as manual review conditions; reference
price, stop, sizing, and monitor thresholds are not guessed from untyped level text.
Existing plan settings and conditions are retained, and an open editor is not replaced.
Source content is kept in page memory, not URL parameters or browser storage; the
opaque revision ID in the URL allows the source to be read again after refresh.

### Observation refresh progress and recovery

Journal's **Refresh Sources** submits a durable `observation.refresh` run and returns
its request identity before source/model work finishes. Journal and Operations show
capture/save, interpretation, and escalated-review receipts. The browser remembers
only the opaque request identity; reloading reads progress and does not replay a
write. Use **Resume Failed Stages** after FAILED/INTERRUPTED to reuse the same run:
completed stages and successful interpretations are retained. A new refresh is a
new explicit request; it may retry failed drafts. Existing CLI/source-specific sync
contracts remain available.

Model stages have a ten-minute deadline each and at most 100 candidate calls per
stage. The current intake view is bounded to 200 notes; exceeding that boundary is
reported as degraded, not complete coverage. Provider capture uses its existing
bounded request policy. A cancelled threaded capture keeps its process lock until
the worker stops. API shutdown waits for that cleanup before closing resources.
No cancellation UI is offered for requests that cannot be stopped reliably.

Execution and quality are separate: a terminal SUCCEEDED execution can carry
`OBSERVATION_REFRESH_DEGRADED` when coverage or a draft is incomplete. Disabled
analysis is explicit, and rejected drafts do not become Decisions. A successful
process exit or a not-due scheduler skip does not certify a complete business run.
Operational diagnostics persist closed codes rather than provider payloads.

**Load Review Context** reads the exact Observation revision alongside current
confirmed Thesis/Plan/Decision references and coverage. It is explicitly current
context, not a point-in-time reconstruction of the portfolio on the note date.
Revision history remains the source for old note text and differences. None of
these reads confirms a judgment or invokes a model.

Operations also shows `runtime_identity`: on-disk Git revision/dirty state, Next
build ID, and actual/expected database revisions. On-disk identity does not prove
which edits an already-running Python process has loaded; restart after verified
changes. Non-test startup performs a read-only migration-head preflight before
repository construction. Normal reads never run migrations. The expected head is
centralized in `application.ports.database`.

Post-market completion markers derive from the same runtime configuration as child
commands. An installed runtime without an explicit root fails closed rather than
writing under site-packages. Only the attributable default owner runtime may reuse
an owner-only legacy macOS marker for the exact same session; custom runtimes never
inherit another installation's completion marker.

### Explicit capture versus analysis

The compatibility Console sync routes (`/api/observations/sync` and
`/api/moomoo-notes/sync`) and `/api/observations/import` honor `analyze=false`:
only capture runs, with `analysis_started=false` and no implicit background batch.
`analyze=true` retains the service's explicit analysis behavior. For the complete
capture/interpret/review flow, use Journal Refresh Sources and its durable progress;
for an exact revision, use its Analyze/Retry action.

`trading-partner-moomoo-notes-sync` is a compatibility entry to the same implementation
as `trading-partner-observation-sync --source MOOMOO_NOTE`. Existing analysis flags
and receipt fields remain; the shared receipt additionally identifies source and
source capabilities. The Moomoo command cannot override its source.
