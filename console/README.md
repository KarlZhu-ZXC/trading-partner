# Trading Partner Local Console

本地操作型前端，展示并操作 Trading Partner 的持久化事实、Monitor、账户、自选、
28 个 MCP 能力和确定性运维服务。它不调用 LLM、不执行订单；只有用户点击同步或运行
按钮时才访问外部 Provider，写入仍受原 MCP 确认、actor 和幂等规则约束。

Monitor 页面可直接解析 A 股/美股标的，新建或编辑 Monitor，并用表单组合价格、
组合风险及九类事实条件。保存修改会生成不可变的新版本，不会修改 Thesis、持仓或订单。
每条条件必须填写人类可读的具体释义；定义卡片同时显示原始创建时间和最近运行时间。
最近 Run 会列出 observation 当时实际使用的标的代码；仅在版本匹配时附带当前 Monitor 名称，
因此后续改标的不会改写历史运行的展示含义。
界面默认使用浅色主题，左下角可切换浅色/深色；选择只保存在本机浏览器。

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
前端默认读取 `http://127.0.0.1:8765`，必要时可用
`NEXT_PUBLIC_TRADING_PARTNER_API` 覆盖。

## 验证

```bash
npm run lint
npm test
```

本控制台依赖本机数据库与机密配置，因此不部署到公共 Sites Hosting。
