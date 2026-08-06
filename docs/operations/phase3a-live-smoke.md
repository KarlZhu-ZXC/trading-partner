# Phase 3A 免费数据源 live-smoke runbook

本 runbook 只做只读请求和显式事实同步，不执行订单。默认链不需要付费订阅或 trial。

## 1. 迁移与健康检查

```bash
uv run alembic upgrade head
uv run trading-partner-mcp
```

`system_health` 应列出 `cross_asset.cme_reference_configured`、
`cross_asset.dce_eod_configured` 和 `cross_asset.dukascopy_spot_configured`。
名称中的 `configured` 是刻意的：这些只验证运行时接线/开关，不代表外部域名、TLS 或
上游服务此刻可达。实际连通性必须用下方只读行情请求验证。

若运行环境不能直连这些公开域名，可在 `.env` 设置一个通用
`PROVIDER_PROXY_URL`。它只供 CME、DCE、Dukascopy 和 Polymarket 使用；不设置时直连。
代理地址属于敏感配置，不得进入日志、错误详情或提交历史。

Dukascopy 默认走与当前 `dukascopy-node` 相同的 keyless Jetta 分桶接口：1 分钟按 UTC
日、1 小时按 UTC 月、日线按 UTC 年请求；每批最多 10 个并发请求，跨批等待 1 秒，
完整历史桶进程内缓存，带 `from` 的活动桶不缓存，默认不自动重试。Dukascopy 未公开
可验证的固定每分钟配额，因此这些是客户端节流策略，不是服务端额度声明。

`DUKASCOPY_API_KEY` 不是默认链必需项，只在 Jetta 失败且用户仍持有旧 Trading Tools key
时启用兼容回退。`cross_asset.dukascopy_spot_configured` 只反映开关/接线，真实可用性仍以
只读 quote/bars smoke 为准。

## 2. 代表性 MCP 请求

- `instrument_resolve(market="CME", query="future:CME:GCZ26", asset_type_hint="future")`
- `market_get_snapshot(instrument_id="future:CME:GCZ26", operation="quote")`
- `market_get_bars(instrument_id="commodity_spot:OTC:XAUUSD", interval="60m", ...)`
- `market_data_get(request={"operation":"quote","instrument_id":"cfd:OTC:LIGHT_CMD_USD"})`
- `market_data_get(request={"operation":"bars","instrument_id":"cfd:OTC:LIGHT_CMD_USD","interval":"60m",...})`
- `market_data_get(request={"operation":"futures_curve","product_key":"CME:GC","price_basis":"settlement",...})`
- `market_data_get(request={"operation":"futures_curve","product_key":"DCE:LH","price_basis":"settlement",...})`
- `market_data_get(request={"operation":"spot_future_basis","left_instrument_id":"commodity_spot:OTC:XAUUSD","right_instrument_id":"future:CME:GCZ26",...})`
- `technical_get_snapshot(instrument_id="commodity_spot:OTC:XAGUSD")`

必须检查 `sources`、`as_of`、`freshness`、`data_delay_seconds`、`warnings` 和
`errors`。不得把 Dukascopy 称为 LBMA，也不得把连续代理称为具体合约。

## 3. 显式 EOD 同步

```bash
uv run trading-partner-futures-sync --product CME:GC --trade-date 2026-07-24
uv run trading-partner-futures-sync --product DCE:LH --trade-date 2026-07-24
```

命令刷新合约定义，并把返回的 EOD settlement/volume/open-interest publication vintage
幂等写入 `futures_contract_statistics`。再次运行相同请求不会复制相同 vintage。

## 4. 合理降级

- CME 公开端点 TLS/网络失败：typed provider error；不能退化成 `GC=F` 冒充具体合约。
- DCE 返回 401/403/412：`DCE_OFFICIAL_ACCESS_RESTRICTED`，非自动浏览器绕过。
- Yahoo specific contract 缺失：明确 unavailable，不改查连续代理。
- Dukascopy 不可用：XAU/XAG/铜 CFD/轻质原油 CFD 明确 unavailable，不改用期货代理冒充。
- Dukascopy `network_route=direct|proxy` 只披露采用的网络路径，不披露代理地址。Jetta
  使用固定映射 `XAU-USD`、`XAG-USD`、`COPPER.CMD-USD`、`LIGHT.CMD-USD`；旧接口的整数 instrument id
  解析只属于可选 legacy fallback。
- DCE 没有免费分钟 OHLCV：Technical/price Monitor 保持 `NOT_EVALUATED`。

Live smoke 不进入默认 CI，以免公开端点、网络和反爬造成随机失败。
