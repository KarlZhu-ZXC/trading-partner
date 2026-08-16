# TradingView Internal API Integration Reference

状态：**设计参考，尚未接入运行时**  
审阅日期：2026-08-16  
上游项目：[mikeh-22/tradingview-mcp](https://github.com/mikeh-22/tradingview-mcp)  
固定审阅提交：[`07f5133c19a182153f679886017c746a1d4acac1`](https://github.com/mikeh-22/tradingview-mcp/tree/07f5133c19a182153f679886017c746a1d4acac1)

本文记录 TradingView Web 应用当前使用的内部接口及其与 Trading Partner 的潜在产品映射。
这些接口没有 TradingView 官方公开契约，可能随时变化，也可能受账户条款、市场数据许可和
自动化政策约束。本文不是可用性承诺；任何实现必须先完成独立验证，并以 Experimental
Provider 身份失败关闭。

上游 README 声称 MIT，但本次固定提交中没有发现 `LICENSE` 文件。因此只参考可观察的
协议行为和模块划分，不复制上游代码，也不把上游包或容器直接加入生产依赖。

## 1. 与 Schwab Token 接入的关系

可以复用 Schwab 的**运维形态**，但不能把 TradingView Session 称为 OAuth Token。

| 维度 | Schwab | TradingView 内部接口 |
|---|---|---|
| 授权协议 | 正式 OAuth，经 `schwab-py` | TradingView Web 登录后产生的 Session Cookie |
| 初次授权 | 前台浏览器完成官方 OAuth | 前台临时浏览器由用户手动登录、完成 MFA/SSO |
| 后台复用 | SDK 读取并轮换 Token 文件 | HTTP Client 读取 Cookie Jar |
| 权限范围 | 由正式 API 与 OAuth 应用边界定义 | 没有公开 Scope；Cookie 可能代表完整网页会话 |
| 生命周期 | 项目按 7 日重授权规则检查 | 上游以 25 日为本地启发式上限；实际应读取 Cookie expiry，服务端仍可提前撤销 |
| 刷新语义 | SDK/Provider 有正式 Token 语义 | 没有正式 Refresh Token；`401/403` 后重新交互登录 |
| 兼容保证 | Schwab API/SDK 合同 | 无；endpoint、字段和登录流程均可能变化 |
| 项目状态 | 正式 Account/Order Provider | 只能标记 `EXPERIMENTAL_INTERNAL_API` |

建议复用现有 `SchwabOAuthFlowManager` 的以下设计，而不是复用其 OAuth 名称或 DTO：

- 单一前台授权流程与跨进程锁；
- 凭据文件仅位于 gitignored `data/secrets/`，目录 `0700`、文件 `0600`；
- 临时文件原子替换，任何日志、Console、MCP envelope 和回执都不暴露路径或 Cookie；
- 独立的 credential-free 健康状态、授权时间、估计到期时间和下一动作；
- 认证失败不自动重复登录，旧快照不因失败或空响应被清空；
- Console 只启动一条可见登录流程，用户本人完成 Google/Apple/SSO/MFA。

首版不保存 TradingView 用户名或密码，不采用上游的 direct password login、Headless
自动填充或隐藏自动化标志方案。若未来引入通用 Secret Store，可将 Schwab Token 和
TradingView Cookie Jar 一起迁移到 macOS Keychain；在此之前保持项目现有 owner-only
文件约定。

## 2. 上游接口与功能清单

下表是固定提交中的真实调用，不代表 TradingView 官方支持。

### 2.1 认证与 Session

| 方法/地址 | 上游用途 | Trading Partner 结论 |
|---|---|---|
| `GET https://www.tradingview.com/` | 获取初始 CSRF Cookie | 仅交互登录流程内部使用 |
| `POST /accounts/signin/` | 账号密码直接登录 | **不采用**；会要求项目处理密码，也更容易触发 CAPTCHA/政策风险 |
| Playwright 可见浏览器 | 用户交互登录并捕获 TradingView Cookie | 建议作为首次授权与重授权方式 |
| `.tv_session.json` | 保存 Cookie Jar；本地按 25 日判过期 | 只借鉴结构；改为项目 Secret Store、权限和原子写入规范 |

25 日不是 TradingView 的已知合同。2026-08-16 对本机 Desktop 做的仅元数据检查显示，当前
`sessionid`/`sessionid_sign` 在最后一次更新时获得了 93 日 expiry；这只是一次观测，不能
推广成固定期限。读取 Cookie 会更新 `last_access`，但未收到新的 `Set-Cookie` 时不会延长
expiry。Trading Partner 应以 Cookie 自带 expiry 作为本地上限，以成功响应中的 `Set-Cookie`
更新 Session Store，并接受服务端在 expiry 前撤销会话。

认证健康状态建议独立定义：

- `DISABLED`
- `VALID`
- `EXPIRING`：只能根据本地启发式提示，不能声称服务端保证到期时间
- `REAUTH_REQUIRED`：`401/403`、无 Session 或用户主动撤销
- `UNAVAILABLE`：凭据文件损坏或健康状态无法安全读取

### 2.2 Watchlist

| 风险 | 方法/地址 | 功能 | 首期处理 |
|---|---|---|---|
| 只读 | `GET /api/v1/symbols_list/custom/` | 返回全部自建 Watchlist、ID、名称、标的、活动/分享状态与时间 | **P0 接入** |
| 只读 | `GET /api/v1/symbols_list/custom/{id}` | 返回一个 Watchlist | **P0 接入** |
| 只读 | `GET /api/v1/symbols_list/active/` | 返回当前活动列表；上游还用其 ID 推断用户 ID | 只允许识别活动列表；禁止推断账户身份 |
| 写入 | `POST /api/v1/symbols_list/custom/` | 新建列表 | 延后；必须 Preview + 用户确认 |
| 写入 | `POST /api/v1/symbols_list/custom/{id}/rename/` | 重命名 | 延后；必须 Preview + 用户确认 |
| 写入 | `POST /api/v1/symbols_list/custom/{id}/append/` | 批量添加标的 | 延后；必须 Preview + 用户确认 |
| 写入 | `POST /api/v1/symbols_list/custom/{id}/remove/` | 批量移除标的 | 延后；必须 Preview + 用户确认 |
| 破坏性 | `DELETE /api/v1/symbols_list/custom/{id}/` | 删除整个列表 | 默认不实现 |

符号列表中的 `###...` 是分区标题，不是 Instrument。Trading Partner 不应像上游那样直接
丢弃它们；首期 DTO 应保留有序的 `SECTION`/`SYMBOL` 项，Console 可以选择折叠显示，规范化
同步则只消费 `SYMBOL` 项。

每日只读同步必须保存来源分离的不可变快照与差异回执：列表 ID/名称、抓取时间、标的原始
别名、规范化 Instrument ID、未解析/多义项、增加项、缺失项和 Provider warning。读取失败、
schema 不兼容或结果可疑时保留上次成功快照；绝不能把失败、未授权或非权威分页解释成
“用户清空列表”。

### 2.3 Alerts 与通知设置

| 方法/地址 | 功能 | 结论 |
|---|---|---|
| `GET https://pricealerts.tradingview.com/list_alerts` | 列出报警 ID、名称、标的、周期、条件、阈值、有效期、状态及最近触发时间 | P1 只读候选，可用于 Monitor 对照，不能自动创建 Trading Partner Monitor |
| `GET /api/v1/alert/notificationinfo/` | 通知配置 | 低优先级；可能包含邮箱等个人信息，默认不读取、不持久化 |

上游的 `getAlert(id)` 只是先取全量再在本地筛选，不是独立详情 endpoint。未来必须设置列表
上限、响应大小和超时；报警表达式需保持原始来源，未经完整映射不能宣称与 Trading Partner
确定性 Monitor 等价。

### 2.4 Chart Layouts

| 方法/地址 | 功能 | 结论 |
|---|---|---|
| `GET /my-charts/` | 返回布局 ID、名称、主要标的、周期、创建/修改时间、URL、收藏状态 | P1 只读候选，用于从 Console 跳转到精确 TradingView 布局 |
| 单布局详情 | 上游没有确认 endpoint，只在 `/my-charts/` 结果中本地筛选 | 不伪装为独立详情 API |
| 删除布局 | 上游明确不支持 | 不实现 |

这可以支持“Trading Partner 决策对象 → TradingView 工作区链接”，但不能同步图上手工画线；
布局返回的主要标的与周期也不代表完整图表状态。

### 2.5 行情与公司字段

| 方法/地址 | 功能 | 结论 |
|---|---|---|
| `GET https://scanner.tradingview.com/symbol?symbol=...&fields=...` | OHLC、`close`、成交量、涨跌、交易所、币种、市值及部分基本面/技术字段 | 公开/半公开 scanner 接口；只作为未来实验 Provider，不替换现有规范化行情链 |

上游直接把 scanner `close` 命名为 Quote `close`，没有报价时间、交易时段、延迟、价格基础、
前收定义或来源许可说明，不符合 Trading Partner 的 canonical quote contract。若未来接入，
必须经过独立 Adapter 映射成 `display_price`、`price_basis`、`quote_at`、`session`、
`previous_close` 和 `previous_close_basis`；无法建立语义的字段保持 `null`/unavailable。

### 2.6 Screener

| 方法/地址 | 功能 | 结论 |
|---|---|---|
| `POST https://scanner.tradingview.com/{market}/scan` | 字段选择、过滤、排序和分页 | P2 候选；适合候选发现，不得自动进入 `SELECTED` |

上游已包装 `america`、`crypto` 和 `forex`，类型声明还列出 futures、India、Australia、Canada，
但不能把类型声明当成已验证支持。可用字段包括价格、成交量、估值、财务比率、行业、技术
指标和 TradingView recommendation。所有 recommendation 只能作为 Provider fact，不是
Trading Partner Judgment，也不得自动创建/确认 Research Subject 或 Watchlist candidate。

### 2.7 News 与社区 Ideas

| 方法/地址 | 功能 | 结论 |
|---|---|---|
| `GET https://news-mediator.tradingview.com/public/news-flow/v2/news?...` | 按标的读取新闻标题、时间、来源、紧急度和链接 | 与现有新闻 Provider 重叠；低优先级，需核对转载许可 |
| `GET /api/v1/ideas/?sort=...&page=...` | 搜索最近/热门社区观点 | P2 情绪/证据候选；必须与新闻、研报和事实来源分离 |

Ideas 是用户生成内容，不是行情事实或投资建议。若保存为 Evidence，只保存必要摘要、作者、
链接、抓取时间和来源类型，不复制长篇内容。

### 2.8 Pine Scripts

| 方法/地址 | 功能 | 结论 |
|---|---|---|
| `GET https://pine-facade.tradingview.com/pine-facade/list?...` | 列出 saved/published/public scripts | 延后 |
| `GET .../versions/{id}/last` | 解析脚本版本 | 延后 |
| `GET .../translate/{id}/{version}` | 返回脚本源码/转换结果 | 默认不接入 |

私有或付费脚本源码涉及知识产权、账户敏感性和平台条款。除非未来有明确、逐次的用户导出
需求与合规结论，Trading Partner 不应读取、缓存、发送给 LLM 或持久化 Pine 源码。

### 2.9 Historical OHLCV WebSocket

| 地址/协议 | 功能 | 结论 |
|---|---|---|
| `wss://prodata.tradingview.com/socket.io/websocket` | 私有帧格式，创建 chart session、解析 symbol、创建 series、接收 OHLCV | 研究候选，不进入近期路线 |

上游实现存在硬编码 `currency-id=USD`、`adjustment=splits`、30 秒超时和非公开消息协议；没有
交易日历、合约连续性、复权口径、数据许可、延迟与来源质量说明。它不能直接满足本项目的
bars contract，尤其不能把连续期货、OTC、外汇或非 USD 标的按同一规则处理。

## 3. 建议的产品接入顺序

### P0 — TradingView Watchlist Import（只读、Experimental）

1. Console 提供 `Connect TradingView`，启动一次可见的交互登录。
2. `Test Connection` 只读取列表元数据并返回脱敏健康状态。
3. 用户选择一个或多个精确列表；默认不同 TradingView 列表映射为来源分离的本地 Group。
4. `Sync Now` 先生成差异 Preview，再提交本地 durable import；不向 TradingView 写入。
5. 连续 Live Smoke 稳定后才允许安装每日调度器。
6. TradingView、Moomoo 和 Manual CSV 的成员关系保持来源分离；未定义合并优先级前不得互相
   覆盖或传播删除。

### P1 — Alerts 与 Layout Links（只读）

- 把 TradingView Alert 显示为外部报警对照，不自动编译成 Monitor；
- 在 Research Subject/Monitor 中保存精确 Layout Link，方便跳回 TradingView；
- 只有用户发起的显式导入流程才能把外部 Alert 转成 Monitor proposal。

### P2 — Screener 与 Ideas（发现层）

- Screener 结果只能作为 Research Subject Instrument proposal；用户显式确认后直接附加，
  不要求 Shortlist 或 Select；
- Ideas 保持 UGC 来源标签，可作为待审 Evidence，不影响确定性 Judgment；
- 任何 Research/Watchlist 写入继续使用现有 Propose → Confirm 门。

### 默认延期或禁止

- 自动双向 Watchlist 删除；
- 自动创建/修改 TradingView Alert；
- Pine 私有源码读取与持久化；
- TradingView WebSocket 行情替换现有正式 Provider；
- 任何 TradingView Broker Panel 下单或账户操作。

## 4. 推荐内部架构

```text
Interactive Login
  -> TradingViewSessionManager
  -> owner-only Session Cookie Store
  -> TradingViewInternalHttpClient
  -> operation-specific Adapter
  -> canonical DTO / symbol resolver
  -> durable snapshot + sync receipt
  -> existing Watchlist/Research/Monitor application service
```

建议组件：

- `TradingViewSessionManager`：授权锁、Session 健康、显式重授权；
- `TradingViewInternalHttpClient`：严格 host allowlist、HTTPS、超时、响应大小、低速率和脱敏；
- `TradingViewWatchlistProvider`：只读列表 DTO，不泄漏内部字段；
- `TradingViewSymbolAdapter`：保留 `EXCHANGE:TICKER` 原值并映射 Instrument Master；
- `TradingViewWatchlistSyncService`：生成差异、幂等提交本地快照、失败不清空；
- `TradingViewProviderReceipt`：只保存 operation、结果计数、耗时、HTTP status 和 typed error，
  不保存 Cookie、CSRF、请求头、响应正文或异常文本。

首期不增加第 28 个公开 MCP 工具。可扩展现有 `external_state_sync/watchlist` 的内部 Provider
选择与 Console-only 连接管理；如果未来需要同时激活多个来源，必须先修改当前“exactly one
watchlist upstream”产品合同，而不是在 Adapter 内隐式合并。

## 5. 失败与安全合同

建议 typed errors：

- `TRADINGVIEW_AUTH_REQUIRED`
- `TRADINGVIEW_SESSION_UNAVAILABLE`
- `TRADINGVIEW_INTERNAL_API_CHANGED`
- `TRADINGVIEW_WATCHLIST_NOT_FOUND`
- `TRADINGVIEW_SYMBOL_AMBIGUOUS`
- `TRADINGVIEW_RESPONSE_TOO_LARGE`
- `TRADINGVIEW_RATE_LIMIT_ERROR`
- `TRADINGVIEW_PROVIDER_TIMEOUT`
- `TRADINGVIEW_PROVIDER_UNAVAILABLE`

规则：

- `401/403` 不重试业务请求，立即转 `REAUTH_REQUIRED`；
- 每次成功响应若返回新的 `Set-Cookie`，必须在内存校验后原子持久化新的 Cookie Jar；
- `429` 保留上游状态并进入有界冷却，不用并发重试绕过限制；
- `404` 区分列表已删除和 endpoint 变更，未能证明时按 schema/adapter failure 处理；
- 超时、HTML 登录页、JSON shape 变化、缺字段或异常空结果均不能产生删除 diff；
- Session Cookie 属于 Secret，拥有比普通 read-only API key 更宽的潜在权限；
- 只允许 `www.tradingview.com` 及逐项批准的 TradingView 子域，禁止重定向到任意 host；
- Live Smoke 必须显式运行，不进入普通 CI，也不打印响应正文。

## 6. 上游实现不能直接照搬的部分

- Session 文件普通 `writeFile`，没有显式 `0700/0600`、原子替换或跨进程锁；
- HTTP Client 会把响应中的 `Set-Cookie` 更新到内存 Jar，却不重新保存 Session 文件；进程
  重启后可能退回旧 Cookie，丢失服务端续期；
- 支持环境变量明文密码和自动登录，并尝试隐藏浏览器自动化标志；本项目不采用；
- 没有统一 timeout、最大响应体、Provider admission、重试分类或 circuit breaker；
- TypeScript interface 只是编译期声明，网络响应没有严格运行时 schema 校验；
- 错误包含原始路径/上游信息，WebSocket 错误甚至序列化 Provider payload；
- Watchlist 写入和删除直接暴露，没有 Preview、确认、幂等或审计门；
- `getAccount` 把 active-list ID 当用户 ID 并生成伪 username，不是可靠账户事实；
- Quote 缺少时间、session、basis、previous-close 语义和 provenance；
- OHLCV 对币种与复权口径硬编码，不适合跨资产；
- 没有 durable snapshot、失败不清空规则和来源间冲突策略。

## 7. 实施前最小验收

- 对固定 fixture 验证列表、分区、有序标的、空列表和未知字段；
- 验证 `401/403/404/429/5xx`、超时、HTML 响应、超大响应和 schema drift；
- 验证空结果只有在成功、权威、完整响应下才表示真实空列表；
- 验证重复 symbol、同 ticker 不同交易所、连续期货和 OTC 别名不会误合并；
- 验证 Cookie/CSRF/用户名/列表内容不进入日志、异常、健康 DTO 或 Provider receipt；
- 验证 Session 写入权限、原子替换、锁冲突和损坏文件恢复；
- 验证每日同步幂等、失败保留旧快照、重授权后恢复和不传播远端删除；
- 用显式 Live Smoke 记录 endpoint、响应 schema 指纹和耗时，但不保存原始 payload。

只有 P0 只读路径通过以上验收后，才应进入实施计划；其余功能继续作为本参考文档中的候选，
不因上游项目“已有实现”而自动进入 Trading Partner 的产品边界。
