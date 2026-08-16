# Trading Partner Local Console

本地操作型前端，展示并操作 Trading Partner 的持久化事实、Monitor、账户、自选、
27 个 MCP vNext 能力和确定性运维服务。可选 Shared Agent 以可折叠右侧栏常驻在各工作台旁；
它默认关闭且不执行订单。只有用户点击同步或
运行按钮时才访问外部 Provider。若某个 Monitor 已显式启用复合判断策略，运行该 Monitor
可以调用配置好的服务端 LLM；模型没有状态写入或订单端口。其他写入仍受原 MCP 确认、actor
和幂等规则约束。

Monitor 页面可直接解析 A 股/美股标的，新建或编辑 Monitor，并用表单组合价格、
组合风险及九类事实条件。保存修改会生成不可变的新版本，不会修改 Thesis、持仓或订单。
每条条件必须填写人类可读的具体释义；定义卡片同时显示原始创建时间和最近运行时间。
最近 Run 会列出 observation 当时实际使用的标的代码；仅在版本匹配时附带当前 Monitor 名称，
因此后续改标的不会改写历史运行的展示含义。
Provider 调用失败时，Run 详情会展示结构化诊断，包括 Provider、阶段、错误码、HTTP 状态、
尝试次数和是否可重试。诊断不会展示 URL、代理地址、请求头、响应正文或异常原文；迁移前的
历史 Run 没有诊断记录时会明确显示“无结构化诊断”，不会反推或伪造原因。
界面默认使用浅色主题，左下角可切换浅色/深色；选择只保存在本机浏览器。Agent 首次发送会
自动创建 durable conversation；当前路由和用户明确选中的文本只作为本轮临时上下文，不进入长期
记忆。页面上的持仓、账户与研究数据不会被自动抓取发送，当前事实仍通过受控工具读取。
Research、Monitor、Portfolio 与 Decision Workbench 会额外注册 navigation-only 页面上下文，
用于让右侧栏理解当前所选对象；回复以安全的标题、列表、代码、表格和实体链接排版。浏览器刷新后，
右侧栏会恢复 durable Agent Turn 状态并轮询仍在运行的回合；已展示但丢失一次性 token 的 Pending
Action 必须由用户点击 `Resume confirmation` 重新换发，系统不会自动确认。Telegram handoff 与
会话归档也可直接在右侧栏显式触发。默认 Bailian 端点启用原生 Web Search 与正文抽取，
输入栏可依次选择 Provider、该 Provider 实时目录中的文本模型及对应思考强度；目录由后端
凭据代拉并缓存，浏览器不会收到 API key 或完整 endpoint，拉取失败时回退配置默认模型。
每次使用都会保留搜索回执和来源 URL；网页背景不能覆盖 Trading Partner 返回的价格、持仓、
点位、收益或数量事实。不支持原生搜索的端点仍显示为 disabled。真实订单仍未开放。

总览的 Review Queue 把 Catalyst 逾期、Trade Retro 复核/行动项、Scorecard 持续缺口以及
Agent/Broker 未决状态物化为内部持久事项。用户可以确认、设置期限或填写闭环依据后关闭；
数据源读取失败不会自动关闭事项，来源恢复后消失才自动关闭。Decision Workbench 使用同一
闭环状态，但旧 Research、Monitor、Agenda、Retro、Scorecard 页面仍是完整操作入口。

## 启动

先在项目根目录启动 loopback API：

```bash
uv sync --extra console
uv run trading-partner-console
```

再启动前端：

```bash
cd console
npm ci
npm run dev
```

访问 `http://127.0.0.1:3000`。API 固定只接受 `127.0.0.1` / `localhost`；
前端默认通过同源 `/api/console` 代理读取 `http://127.0.0.1:8765`，必要时可用
`NEXT_PUBLIC_TRADING_PARTNER_API` 覆盖。

## 可信局域网访问（可选）

后端照常以 `uv run trading-partner-console` 启动，不要改变它的 loopback 绑定。另一个终端：

```bash
cd console
read -rs "TRADING_PARTNER_CONSOLE_LAN_PASSWORD?LAN password: " && echo
export TRADING_PARTNER_CONSOLE_LAN_PASSWORD
npm run dev:lan
```

脚本仅开放密码保护的前端；后端仍只监听 `127.0.0.1:8765`。LAN 登录使用 12 小时
HttpOnly、SameSite Cookie，连续失败会被限流。此模式只适用于可信局域网的普通 HTTP，
不能用于公网、端口映射或访客网络。密码至少 16 个字符，且不要放入 URL、Git 或
`NEXT_PUBLIC_*` 环境变量。生产构建可使用 `npm run build && npm run start:lan`。

## 验证

Console 表单遵循统一的必填字段规范：当前操作所需的可编辑字段在 label 开头显示红色 `*`，
并同时使用原生 `required` 或 `aria-required` 语义；条件必填只在条件成立时显示，either/or
约束标记字段组。占位符、帮助文字、错误提示和 `(Required)` 后缀不能替代红星；disabled 的
不可变元数据在编辑态不标记为必填。该约束由 UI convention test 检查。

```bash
npm run lint
npm test
```

本控制台依赖本机数据库与机密配置，因此不部署到公共 Sites Hosting。
