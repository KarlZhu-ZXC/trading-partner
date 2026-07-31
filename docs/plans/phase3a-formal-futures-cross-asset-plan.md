# Phase 3A 评审稿 — 正式期货、现货与跨资产基差

> 状态：**Implemented free-provider scope（2026-07-25）**
>
> 调研截止：2026-07-25
>
> 用户已确认免费方案。本文保留为实施/验收记录；稳定能力边界已同步到
> `docs/phases/phase3.md`、能力指南、Agent Guide 与项目 Skill。
>
> **工具面历史口径：**本文中的“52 个公共工具”记录 Phase 3A 实施时的验收基线。
> 后续 MCP surface reduction 已删除该兼容工具面；当前唯一运行时工具面是
> `compact_28`。这些历史数字不代表当前可调用工具。
>
> **成本约束（2026-07-25 追加）：Phase 3A 的默认数据链必须长期零数据订阅费。**
> 允许匿名公开端点、免费注册/免费 API key 和用户本地 Broker/OpenD 已有权限；不允许把
> trial credits、限时试用或付费套餐作为完成条件，也不自动回退到收费源。

## 1. 结论先行

Phase 3A 不应继续围绕 Yahoo `GC=F` 一类连续合约代理追加特例。它应建立一套可同时承载
CME 金属、DCE 生猪、OTC 贵金属和后续 LME 数据的跨资产事实层，并严格区分四种对象：

1. **交易所具体期货合约**：例如 `GCZ26`、`LH2609`，有明确到期日、乘数、最小跳动、
   交易时段和交割/结算规则；
2. **有规则的连续期货序列**：明确选择哪一张真实合约、按什么规则换月，并保留映射；
3. **聚合 OTC 现货报价**：例如 XAU/USD、XAG/USD，来源可能是做市商聚合，不等于官方基准；
4. **官方基准/参考价格**：例如 LBMA Gold Price、LME Cash/3M，具有独立授权和发布时间。

推荐实施路线：

| 优先级 | 交付 | 推荐数据路径 | 结论 |
|---|---|---|---|
| P0 | 正式期货领域模型、合约链、期限结构 | Provider 无关 | 先冻结模型，避免继续把期货塞进 US 股票模型 |
| P0 | CME 金属具体合约 | CME 公开网页/CSV + Yahoo active-contract symbols | 官方 reference/EOD 与免费逐合约 bars 分工；不要求付费 key |
| P0 | 受控连续序列 | CME 到期日/VOI + 项目自有映射 | 保持原始、未回溯调整；每次换月能追溯到具体合约 |
| P1 | XAU/USD、XAG/USD 分钟/小时线 | Dukascopy 免费 current/historical API | Broker/SWFX 口径；返回 bid/ask side，不能标成 LBMA benchmark |
| P1 | DCE 生猪具体合约和日频期限结构 | DCE 官方日行情/历史数据 | 先做官方 EOD；稳定端点和使用条件须在编码前通过 discovery gate |
| P1 | 期现/跨合约基差 | 同时点双腿事实 | 只有单位、时间和口径可比时才计算，否则返回 `NOT_COMPARABLE` |
| P2 | LME 延迟/EOD | LME 免费 reference、15 分钟透明度及 next-day 数据 | 只在零费用注册和用途许可核验通过后接入；不承诺实时 |
| Out | LBMA 正式 benchmark、DCE 实时授权源 | 需要商业授权 | 免费约束下不实施，也不作为 Phase 3A 完成门槛 |

现有 `future:US:GC=F` 等六个 Yahoo 代理继续兼容，但固定标为
`continuous_proxy`。它们不是正式合约、不是现货，也不参与无披露的自动换月或基差计算。

## 2. 用户问题与产品输出

完成后，Trading Partner 应能可靠回答以下类型的问题：

- “GC 当前有哪些可交易月份？近月与次近月的结算、持仓量、到期日分别是什么？”
- “黄金期限结构目前是升水还是贴水？判断基于 last、settlement 还是 mid？”
- “XAU/USD 过去 20 天的 1 小时 K 线如何？数据是聚合现货还是交易所价格？”
- “GC 与 XAU/USD 当前差多少？两个观察值相隔多久，单位和交割口径是否真的可比？”
- “DCE 生猪各月份合约的价格、持仓量和期限结构如何？”
- “当前监控的是 `GC=F` 代理还是某一张 `GCZ26` 合约？发生换月了吗？”

MCP 只返回结构化事实、可比性结论和风险披露。Codex 负责解释这些事实；MCP 不生成交易
建议、不决定主力合约、不修改 Investment Case/Thesis，也不创建订单。

## 3. 当前基础与问题

已经实现的六个 Yahoo 连续金属期货代理，以及 Sina quote / Eastmoney 日线 fallback，适合：

- 快速查看金、银、铜、铂、钯的连续代理走势；
- 获取 1 分钟至月线的 best-effort bars；
- 运行 Technical Engine 和价格监控；
- 在免费数据源失效时显式降级。

它们不能回答：

- 当前代理到底映射到哪张具体合约；
- 合约最后交易日、交割、乘数、tick、官方结算、成交量和持仓量；
- 完整合约链、期限结构和可审计的换月；
- XAU/USD、XAG/USD 或 LME 铜现货；
- 同时点、同单位、同口径的期现基差。

当前代码还把 `Market` 限制为 `A_SHARE|US`，并由 US DTO/Coordinator 校验所有期货请求。
继续沿用该路径会让 DCE、OTC 和 LME 的语义越来越错误，因此 Phase 3A 必须先做一次受控的
通用化，而不是增加更多 `future:US:*` 特判。

## 4. 数据源调研与取舍

### 4.1 CME 正式合约：免费组合源

| 来源 | 可提供内容 | 成本/访问 | 评估 |
|---|---|---|---|
| CME Product/Expiration Browser、contract specs | 产品代码、规格、活动到期日和 CSV 导出 | 免费公开网页 | 合约定义 authority；低频同步、版本化保存 |
| CME Delayed Quotes | 逐合约 quote/chain，至少延迟 10 分钟 | 免费公开网页 | 官方 reference quote；固定 `delayed/degraded`，不可称实时 |
| CME product settlements / Daily Bulletin | 次日 settlement、volume、open interest | 免费公开网页/PDF | EOD authority；官网明确仅供参考，不替代 MDP validation |
| Yahoo active-contract symbols | `GCZ26.CMX` 等逐合约 quote、60m/1d bars 和 futures chain | 免费、无 SLA | 市场 bars 主源；只接受 CME calendar 验证过的活动合约 |
| Yahoo / Sina / Eastmoney continuous symbols | 连续代理、有限 fallback | 免费、无 SLA | 继续兼容，但不得升级成具体合约事实 |

2026-07-25 本机 discovery smoke 已确认：Yahoo 可返回 `GCZ26.CMX`、`GCG27.CMX`、
`SIZ26.CMX`、`HGZ26.CMX` 的活动合约数据；GC/SI/HG 三个样本均可取得 60 分钟和日线 OHLCV。
`GCZ24.CMX` 已无法取得，说明 Yahoo 不能承担到期合约长期历史。实施时必须先从 CME 活动
到期日列表得到候选，再验证 Yahoo symbol，不能按月份盲猜或把 Yahoo futures chain 单独当
作 exchange master。

CME 公开报价至少延迟 10 分钟；Daily Bulletin 的 preliminary/final 版本分别在下一营业日
发布。官网同时明确网页市场数据只供 reference，不应用作 CME MDP 的验证或补充。因此免费
路径足以支持个人研究、合约链、EOD 期限结构和延迟监控，但不满足低延迟交易或正式清算数据
validation。所有响应固定披露 `CME_PUBLIC_REFERENCE_ONLY` 和实际 `data_delay_seconds`。

商业 Reference Data/历史行情服务不进入 Provider chain、配置、完成定义和测试；以后只有
用户主动改变成本约束时才重新评审。

### 4.2 DCE 生猪

DCE 官方页面公开日行情、周/月行情、历史数据、延时行情和行情授权说明；生猪合约官方规格
包括 `LH`、16 吨/手、5 元/吨最小变动价位及 1/3/5/7/9/11 合约月。当前尚未找到一个可以
直接承诺为长期稳定、无需授权的官方 JSON API。

因此采用两级方案：

1. **MVP：DCE 官方 EOD 文件/页面**。编码前先做 1 个 discovery spike，确认下载地址、
   字段、交易日、历史跨度、访问频率和使用条件；成功后只按交易日抓一次并缓存；
2. **免费边界**：若官方公开端点只支持 EOD，则 Phase 3A 就只承诺 EOD。DCE 实时/分钟
   授权分发商不进入本计划，也不靠匿名抓取冒充正式接入。

Discovery gate 失败时，DCE Phase 3A 返回明确的 provider/entitlement error；不能自动把
东方财富“主连”或新浪连续行情当作正式合约链。第三方公开源可继续作为明确降级的观察源，
但不满足 P1 正式验收。

### 4.3 XAU/USD、XAG/USD 与铜代理：免费组合源

| 来源 | 粒度/能力 | 数据口径 | 评估 |
|---|---|---|---|
| Dukascopy Jetta（按当前 dukascopy-node 策略） | 1m、1h、1d 的 BID/ASK 分桶历史；最新共同分钟收盘组成 quote proxy | Dukascopy/SWFX 或其 CFD 报价口径 | **已接入主链**；keyless，10 请求/批、跨批 1 秒；旧 Trading Tools key API 仅兼容回退 |
| Gold-API | XAU/XAG/HG 当前价免费、无认证；禁止高频滥用 | 上游来源不透明，无 bid/ask | 仅低可靠 supplemental；不能用于 monitor/basis 主腿 |
| LBMA/IBA | Gold/Silver 官方拍卖基准 | 金每日两次、银每日一次；历史/实时使用需要许可 | 是 benchmark，不是连续 XAU/USD 分钟行情；本轮 deferred |
| LME | 免费 reference files、15 分钟延迟透明度、next-day/current-year data | 交易所数据；可能要求零费用注册/用途许可 | P2 discovery；只接明确允许的免费内部研究数据 |

Dukascopy 官方 Historical Data Export 明确免费，并覆盖 forex、commodities 和 indices；其
JForex 事实库原生保存 tick、1 minute、1 hour、1 day，其他周期由这些基础周期聚合。XAU/USD
和 XAG/USD 是 rolling spot metals/broker feed，不是 LBMA 拍卖价。输出需要保存 bid/ask side、
UTC day boundary 和其 volume 为 best bid/ask volume 汇总的特殊口径。

Dukascopy 的 `COPPER.CMD/USD` 是非到期 commodity CFD，价格受相应近月期货影响并在换月时
做 monthly adjustment；它必须使用独立 `cfd:OTC:COPPER_CMD_USD` 身份，不能命名为铜现货、
LME Cash 或 COMEX HG。免费版可以把它作为 P2 观察代理，真正铜现货仍保持 unavailable。

Gold-API 免费 current endpoint 经 2026-07-25 smoke 可返回 XAU、XAG、HG 和 `updatedAt`，
但周末也会持续刷新 `updatedAt`。该时间更像服务缓存更新时间，不能当作市场成交时间。
若后续接入，只能返回 `quote_at=null`、`freshness=unknown`、
`PROVIDER_UPDATE_TIME_NOT_MARKET_TIME`，且不得触发 Monitor 或参与 basis calculation。

LBMA Gold Price 是伦敦未分配黄金的每日拍卖基准，不是全天候 XAU/USD tick feed。金基准
每日 10:30 和 15:00 伦敦时间形成，银基准每日 12:00；其使用需要 IBA 许可。LME 虽提供
免费参考文件、15 分钟延迟透明度数据和 next-day feed，但正式 Cash/3M 价格及衍生分析存在
明确授权边界。因此免费 P2 slice 必须先核验零费用许可和允许的内部研究用途；LBMA 不接，
也不把 Dukascopy/Gold-API 价格重命名为 LBMA/LME。

## 5. 冻结的领域边界

### 5.1 标识与市场命名

建议 append-only 扩展：

```text
Market:    A_SHARE | US | CME | DCE | OTC | LME
AssetType: equity | etf | index | option | future | commodity_spot | cfd | benchmark
```

示例 identity：

```text
future:CME:GCZ26                 # 具体 COMEX Gold 合约
future:CME:GC.v.0                # 按成交量排名的未调整连续序列
future:DCE:LH2609                # DCE 生猪具体合约
commodity_spot:OTC:XAUUSD        # 聚合 OTC gold/USD
commodity_spot:OTC:XAGUSD        # 聚合 OTC silver/USD
cfd:OTC:COPPER_CMD_USD           # Dukascopy rolling copper CFD；不是铜现货
benchmark:OTC:LBMA_GOLD_PM       # 仅在取得许可后启用
```

`exchange` 字段继续保存 `COMEX`、`NYMEX`、`DCE`、`LME` 等实际 venue。`Market.CME`
是 Provider/identity namespace，不把 COMEX 或 NYMEX 丢失掉。

`FuturesProductDefinition` 是内部实体，使用
`futures_product_<uuid7>`；同时保存唯一、稳定、可读的 `product_key`（例如 `CME:GC`、
`DCE:LH`）。具体合约和连续序列是可查询 instrument，继续使用
`asset_type:market:symbol` 业务身份，不再额外暴露第二套 UUID instrument ID。若定义版本需要
独立寻址，使用 `futures_product_version_<uuid7>` 和
`futures_contract_version_<uuid7>`，均只做 append-only 扩展。

兼容规则：

- 已存在的 `future:US:GC=F` 等 ID 永不静默重写；
- 新建 Case/Monitor 可显式选择 proxy、ruled continuous 或 specific contract；
- 旧 Monitor 继续跟踪原代理，除非用户明确迁移；
- 同一 symbol 的 proxy 与正式合约必须能同时存在，不能按名称误合并。

### 5.2 不把所有字段塞入 Instrument

`Instrument` 保留稳定身份、币种、时区、multiplier/tick 等通用字段。新增独立领域对象：

```text
FuturesProductDefinition
  product_id, root, market, exchange, commodity, currency, price_unit,
  multiplier, tick_size, settlement_method, session_calendar_id,
  source, valid_from, valid_to

FuturesContractDefinition
  instrument_id, product_id, contract_month, listed_at, first_trade_at,
  last_trade_at, expiration_at, first_notice_at, delivery_start/end,
  settlement_at, status, definition_as_of

FuturesContractStatistics
  instrument_id, trade_date, settlement, settlement_status,
  session_volume, open_interest, published_at, source

ContinuousSeriesDefinition
  instrument_id, product_id, roll_rule(calendar|volume|open_interest),
  rank, adjustment(none), provider_methodology_version

ContinuousContractMapping
  continuous_instrument_id, contract_instrument_id,
  effective_from, effective_to, mapping_source

FuturesCurveSnapshot
  product_id, as_of, price_basis(last|mid|settlement), contracts[],
  front_next_spread, curve_shape, completeness

SpotObservation
  instrument_id, bid, ask, mid, last, currency, unit, quote_at,
  venue_basis, delivery_location, source

BasisSnapshot
  left_leg, right_leg, normalized_unit, observation_lag_seconds,
  absolute_spread, percentage_spread, comparability, formula_version
```

所有 money/market values 使用 `Decimal`；所有时间带时区。Provider 原始 payload 仍不得越过
infrastructure boundary。

### 5.3 连续合约和主力定义

“主力”不是单一事实。接口必须要求或回显选择规则：

- `calendar`：最近未到期；
- `volume`：成交量排名；
- `open_interest`：持仓量排名。

Phase 3A 只支持 `adjustment=none`。每根连续 bar 必须能追溯到当天真实合约；换月日返回
mapping transition。回溯平滑、比例调整、Panama adjustment 和研究数据版本归入 Phase 3C，
不在 Phase 3A 内重复做一个小型回测数据平台。

### 5.4 期限结构与基差

期限结构默认使用同一 Provider、同一 `as_of`、同一 price basis。排序依据是有效到期日，
不是字符串。相邻价差固定定义为 `far_price - near_price`，`curve_shape` 只做机械分类：

- 相邻价差全部非负：`CONTANGO`；
- 相邻价差全部非正：`BACKWARDATION`；
- 其余：`MIXED`；
- 数据不足：`NOT_EVALUATED`。

基差只在以下条件全部满足时计算：

- 两腿币种和单位已明确并可无损换算；
- 两腿 observation lag 不超过请求阈值；
- 合约、交割地点、品质和价格 basis 已披露；
- 两腿都是真实观测，不使用推断价格。

`GC - XAUUSD` 只能称为“COMEX 合约与聚合 OTC gold 的观察价差”，不能称为无条件的
cash-and-carry basis。`LH - 全国生猪均价` 的品级、地区、发布时间和交割口径不同，默认
`comparability=INDICATIVE_ONLY`，不得输出伪精确套利结论。

Phase 3A 只返回当前/指定 `as_of` 的 basis snapshot 及缓存。长期、可复现的 durable basis
dataset 属于未来可选的历史数据计划，不在当前 Phase 3 范围内；现有 Phase 3 文档不再把
durable basis series 列为 3A 或 3C 的退出条件。

### 5.5 代码目录、类名与方法名

本轮不新建第二层项目 package，也不把跨资产代码继续放进 `us_market`：

```text
src/
├── domain/cross_asset/
│   ├── enums.py
│   ├── futures_models.py
│   ├── spot_models.py
│   └── basis_service.py
├── application/
│   ├── dto/cross_asset.py
│   ├── ports/futures_reference_provider.py
│   ├── ports/futures_statistics_provider.py
│   ├── ports/commodity_spot_provider.py
│   ├── ports/futures_definition_repository.py
│   └── services/
│       ├── futures_contract_service.py
│       ├── futures_curve_service.py
│       ├── continuous_series_service.py
│       ├── basis_comparison_service.py
│       └── cross_asset_tool_coordinator.py
└── infrastructure/
    ├── providers/cross_asset/
    │   ├── cme_public_client.py
    │   ├── cme_public_codecs.py
    │   ├── dce_official_client.py
    │   ├── dce_official_codecs.py
    │   ├── dukascopy_client.py
    │   ├── dukascopy_codecs.py
    │   ├── gold_api_client.py
    │   └── gold_api_codecs.py
    └── persistence/sqlalchemy_futures_definition_repository.py
```

冻结的核心 port 方法：

```text
FuturesReferenceProvider.get_product_definition(product_key, as_of)
FuturesReferenceProvider.list_contract_definitions(product_key, as_of)
FuturesReferenceProvider.resolve_continuous_mapping(series, start, end, as_of)

FuturesStatisticsProvider.get_contract_statistics(instrument_ids, trade_date, as_of)

CommoditySpotProvider.get_quote(instrument, as_of)
CommoditySpotProvider.get_bars(instrument, start, end, interval, as_of)

FuturesDefinitionRepository.get_product(product_key, as_of)
FuturesDefinitionRepository.list_contracts(product_id, as_of)
FuturesDefinitionRepository.save_definition_batch(batch)
```

Application service 只组合这些 ports；Provider SDK、HTTP payload、SQLAlchemy model 和 MCP
schema 都不得进入 `domain/cross_asset`。现有通用 quote/bar Provider Router 可以复用时通过
adapter 接入，不能让 domain model 反向依赖当前 US DTO。

## 6. MCP 公共面设计

公共工具总数继续保持 **52**，不新增 `futures_*` 或 `spot_*` 工具森林。

### 6.1 一次一换的命名修正

建议把语义已经过窄的：

```text
us_get_market -> market_get_snapshot
```

这是一换一，不改变工具总数。`operation="quote"` 支持股票、指数、正式期货、连续序列和
商品现货；`operation="composite"` 仍只允许当前 US equity composite，并在 schema 中明确。
由于项目已公开，该 rename 必须写入 release notes；不保留第二个 alias 工具，否则会变成
53。用户已接受该 breaking rename，旧名称仅保留在 retired inventory 中防止误注册。

### 6.2 复用现有工具

| 工具 | Phase 3A 扩展 |
|---|---|
| `instrument_resolve` | 支持 `CME|DCE|OTC|LME` namespace；本地 miss 只缓存唯一、已验证候选 |
| `market_get_snapshot` | `quote|composite`；quote 返回 instrument kind、basis 和 contract metadata 摘要 |
| `market_get_bars` | 支持 specific/continuous/spot；futures/spot 固定 `adjustment=none` |
| `market_data_get` | closed operation：`us_market|futures_curve|spot_future_basis` |
| `technical_get_snapshot` | 有足够真实 OHLCV 时支持具体合约、连续序列和 spot；不对 point series 造 K 线 |
| `technical_render_chart` | 同上，图表固定显示 symbol、contract/spot basis 和 roll markers |
| `monitor_*` | 不新增工具；价格规则扩展到 CME/DCE/OTC，并使用各自 session/freshness policy |

建议输入示例：

```json
{
  "operation": "futures_curve",
  "instrument_id": "future:CME:GC.v.0",
  "price_basis": "settlement",
  "contract_limit": 6,
  "as_of": "2026-07-24T23:00:00Z"
}
```

```json
{
  "operation": "spot_future_basis",
  "left_instrument_id": "commodity_spot:OTC:XAUUSD",
  "right_instrument_id": "future:CME:GCZ26",
  "max_observation_lag_seconds": 300,
  "as_of": "2026-07-24T15:00:00Z"
}
```

`market_data_get` 的 US 调用显式使用 `request.operation="us_market"`；期限结构与现货/期货
基差分别使用自己的 closed request variant，其他 operation 的字段会被 schema 拒绝。

## 7. Provider Router、缓存与降级

推荐 chain：

```text
CME definitions/lifecycle/EOD statistics:
  cme_public -> unavailable

CME specific-contract quote:
  yfinance active contract -> cme_public delayed quote -> unavailable

CME specific-contract bars:
  yfinance active contract -> unavailable

CME continuous proxy quote/bars:
  yfinance proxy -> current Sina/Eastmoney fallback

DCE specific daily/curve:
  dce_official_eod -> unavailable

OTC metals quote/bars:
  dukascopy -> unavailable

Low-reliability current-price supplement:
  gold_api (never monitor/basis eligible)
```

关键规则：

- Provider chain 按 market + asset type + capability 路由，不能复用 US 股票链的隐式假设；
- CME/Yahoo specific-contract 不可用时，不能退化成 `GC=F` 冒充；
- Dukascopy 不可用时，XAU/XAG bars 明确不可用，不能退化成 GC/SI；
- contract definitions 每日或 lifecycle 变化时刷新；curve/settlement、bars、spot 分开缓存；
- 每个新 Provider 使用共享 cross-process limiter、熔断和 redaction；
- 所有响应继续提供 source、as_of、freshness、`data_delay_seconds`、warnings 和 errors；
- 关闭市场时按 session-aware 规则判断新鲜度，避免把正式收盘值仅因超过 2 小时判为失效；
- 免费端点变更、免费 key 缺失或用途许可错误不可标 `retryable=true`，429/临时连接错误才可重试；
- 免费来源全部低频缓存：CME quote 不快于 10 分钟，CME/DCE EOD 每交易日一次，Dukascopy
  quote/bars 按 interval 缓存；不得用高并发轮询把免费端点当 streaming feed。

“免费”不等于“所有 capability 可用”。Health 和响应逐项披露 `quote`、`bars`、`contract_chain`、
`settlement_oi`、`spot_bid_ask` 能力；不存在的实时、历史或授权能力保持 unavailable。

建议新增的稳定错误/警告语义：

| Code | 用途 |
|---|---|
| `PROVIDER_ENTITLEMENT_REQUIRED` | 缺 key、套餐或市场权限；非 retryable |
| `CONTRACT_DEFINITION_UNAVAILABLE` | 无法得到可验证的具体合约定义 |
| `FUTURES_CHAIN_UNAVAILABLE` | 无法形成完整、同口径的合约链 |
| `ROLL_MAPPING_UNAVAILABLE` | 连续序列无法追溯到真实合约 |
| `BASIS_NOT_COMPARABLE` | 单位、时间、交割或 basis 不可比 |
| `AGGREGATED_OTC_NOT_BENCHMARK` | 聚合 OTC 数据不可当 LBMA/LME benchmark |
| `OFFICIAL_SETTLEMENT_NOT_LAST_TRADE` | settlement 与 last trade 明确分离 |
| `MARKET_DATA_LICENSE_RESTRICTED` | Provider 明确禁止当前使用方式；非 retryable |
| `CME_PUBLIC_REFERENCE_ONLY` | CME 免费网页事实只适合研究参考，不替代 MDP validation |
| `PROVIDER_UPDATE_TIME_NOT_MARKET_TIME` | Provider 更新时间无法证明是市场观察时间 |
| `ROLLING_CFD_NOT_SPOT` | rolling commodity CFD 不得称为现货或交易所 Cash price |

现有 `FUTURES_CONTRACT_NOT_SPOT` 和 `CONTINUOUS_FUTURES_ROLL_RISK` 继续保留。

## 8. 数据库和迁移

新增表建议：

```text
futures_products
futures_product_versions
futures_contracts
futures_contract_versions
continuous_series_definitions
continuous_contract_mappings
```

`FuturesContractStatistics`、curve 和 spot quote 首轮使用 Provider cache/response，不把每次只读
请求自动变成永久写入。显式 EOD sync 可持久化 settlement/OI 的最小日频事实；完整历史 bars
和 basis series 仍由 Phase 3C 管理。

迁移必须满足：

- `Market`/`AssetType` 只 append，不改变既有 wire value；
- 不批量重写 `future:US:*=F`；
- 不自动迁移 Case、Thesis、Monitor 或 Journal 的 instrument；
- definition version append-only，Provider 修订可追踪；
- 删除/到期合约只改变 lifecycle status，不删除历史身份；
- Alembic upgrade 在现有真实 SQLite 副本和空库都通过。

## 9. 配置原则

实施时只向 `.env.example` 和本地 `.env` 增加用户真正需要选择或填写的项；不把每个 TTL、
roll 参数都暴露成环境变量。

默认实现不需要任何付费 key，也不加入付费 Provider 的 secret。Jetta 主链 keyless；只有
用户已有旧 Trading Tools key 并希望保留兼容回退时才填写：

```dotenv
# Optional legacy Trading Tools compatibility fallback
DUKASCOPY_API_KEY=
```

内部缓存时长、roll algorithm version、最大曲线长度和默认 basis lag 属于代码内的版本化策略，
Provider chain 也先按上述免费固定顺序写入 composition/config defaults，不为一次性选择制造三个
环境变量。除非出现真实运维需求，不进入 `.env`。所有 secret 为空模板，真实值只在
gitignored `.env`。

## 10. 分阶段实施计划

### 3A-0 — 契约冻结与通用化（完成）

交付：

- append-only `Market`/`AssetType`；
- futures product/contract/continuous/curve/spot/basis domain models；
- Provider ports 和通用 market DTO/Coordinator；
- 一换一 `us_get_market -> market_get_snapshot`；
- migration 与兼容层。

退出条件：不接 live Provider 也能用 fixtures 完整表达 `GCZ26`、`LH2609`、XAUUSD 以及
连续映射；公共工具仍为 52；现有股票和 Yahoo proxy 测试不退化。

### 3A-1 — CME 免费具体合约（完成；本机 live endpoint 受网络条件影响）

交付：

- CME public adapter：contract specs、expiration export、delayed quote、settlement/VOI；
- Yahoo active-contract symbol discovery/validation 和逐合约 OHLCV；
- GC/MGC/SI/HG/PL/PA product seed 和 read-through contract cache；
- reference-only、至少 10 分钟延迟、EOD preliminary/final 状态披露。

退出条件：至少用 GC、SI、HG 各一张真实活动合约完成 anonymous smoke；返回的 expiry、tick、
multiplier、settlement/OI 能追溯到 CME/Yahoo；`GCZ24.CMX` 类到期历史缺口明确可见；没有任何
付费 key 或 trial 依赖。

### 3A-2 — 合约链、期限结构与受控连续序列（核心完成）

交付：

- parent root 枚举和 outright/spread 过滤；
- calendar/volume/OI 三种显式排序/roll 规则；
- `FuturesCurveSnapshot` 和连续到具体合约映射；
- chart roll marker；
- 现有 `GC=F` 标为 legacy continuous proxy。

退出条件：同一 `as_of` 可返回前 6 个 GC 合约、完整字段、curve completeness 和相邻价差；
任意连续 observation 能显示实际 contract；不产生 back-adjusted bars。

实施说明：定义、roll rule 和 continuous→specific mapping 已落库；受免费源到期合约历史
限制，不在本轮拼接长期 continuous OHLCV，也不在图上伪造历史 roll marker。该历史数据集
属于未来可选工作，不影响具体合约、期限结构和现有 `*=F` 兼容代理的使用，也不阻塞当前
Phase 3 收口。

### 3A-3 — 免费 OTC 金银观察源（完成）

交付：

- Dukascopy instrument discovery、免费 key probe、current/historical quote codec；
- XAUUSD/XAGUSD quote、1m/5m/15m/30m/1h/1d bars（只启用 Provider 实际验证的 interval）；
- bid/ask side、SWFX/broker basis、session/timezone、volume 语义和 benchmark 警告；
- Technical Engine/Monitor 的 asset-aware 接入。

退出条件：XAU/XAG 至少完成 quote、1h bars、chart 和 monitor live smoke；每个输出都明确
`venue_basis=dukascopy_swfx`、非 LBMA；Gold-API 不参与该验收。铜若接入，只能以
`cfd:OTC:COPPER_CMD_USD` 通过 `ROLLING_CFD_NOT_SPOT` 验收。

### 3A-4 — DCE 生猪日频正式接入（完成；412 保持 typed degradation）

先执行 discovery gate：

1. 核验 DCE 官方下载端点和访问条件；
2. 保存一份公开样本 fixture，验证字段、编码、单位和交易日；
3. 验证至少 12 个月日行情和当前全部 LH 具体合约；
4. 记录请求频率，设计每交易日一次的缓存/sync。

Gate 通过后交付 DCE product/contract definition、daily settlement/volume/OI、合约链和期限
结构。Gate 不通过则保留 typed unavailable，不购买授权数据，也不降级成主连冒充具体合约。

### 3A-5 — 基差、监控与运维收口（完成）

交付：

- spot/future 和 near/far contract basis snapshot；
- unit/time/delivery comparability gate；
- CME/DCE/OTC session-aware monitor freshness；
- `trading-partner-futures-sync` 显式 CLI，用于 definition + EOD statistics；
- system health、capability docs、release notes 和 live-smoke runbook。

退出条件：GC-XAU 与 LH-national-price 两个样本分别得到 `COMPARABLE`/`INDICATIVE_ONLY` 或
明确 `NOT_COMPARABLE`；闭市后官方 settlement 可评估 monitor；CLI 幂等且无订单副作用。

### 3A-6 — 免费 LME discovery（不阻塞本轮）

- 核验 LME 免费 reference TIF、15 分钟 delayed transparency、next-day XML/current-year files；
- 只有零费用注册、内部研究用途和字段口径全部通过时，才接 LME Cash/3M 延迟/EOD；
- LBMA/IBA benchmark、DCE/SHFE/CZCE 实时授权源继续保持 out；
- 通用 FX/crypto 仅在出现明确研究用例后进入规划。

## 11. 精简 TDD 与验证

不建立 Provider × symbol × interval × error 的全排列矩阵。每个 slice 只保留能阻止真实回归的
最小测试集：

1. **Domain unit**：身份、合约日期、Decimal/unit、连续映射、basis comparability 不变量；
2. **Provider contract**：每个 Provider 一份成功 fixture、一份 malformed/error fixture；
3. **Routing**：每种 capability 一条 primary/fallback/entitlement 关键路径；
4. **MCP integration**：quote、bars、curve、basis 各一条 happy path 和一个 typed failure；
5. **Migration**：空库 + 真实结构副本；
6. **Live smoke**：opt-in、非 CI，只跑 GC/SI/HG、XAU/XAG、LH 的代表样本；
7. **Quality gate**：Ruff、mypy、focused pytest、full pytest、Alembic upgrade、wheel smoke、
   Gitleaks。

同类字段 codec 使用参数化单测；不会为六个金属复制六套 service/router/MCP 测试。Provider
真实 API 不进入默认 CI，避免额度、网络和许可导致随机失败。

## 12. Phase 3A 完成定义

本轮 Phase 3A 在满足以下条件时才标记完成：

- 具体 CME 合约和 DCE LH 合约使用同一领域模型；
- contract metadata、官方 settlement、volume/OI 和普通 quote 被明确区分；
- 可返回完整合约链、期限结构和可追溯的 continuous mapping；
- XAU/XAG Dukascopy broker/SWFX 事实可用且从不冒充 LBMA；
- 期现价差只有在可比性 gate 通过时才计算；
- 现有六个 Yahoo proxy 兼容，但 warning 和 identity 不被弱化；
- Monitor 使用 market/session-aware freshness，不把正常闭市值误判为失效；
- MCP 公共面保持 52，无 order/execution 写入；
- 默认 Provider chain 零订阅费、零 trial 依赖、无需付费 secret；
- 免费来源无 SLA、历史缺口、延迟和 reference-only 边界全部可见；
- 无免费凭证、用途许可或 Provider 不可用时显式降级，不自动购买/切换收费源，不伪造数据；
- 文档、配置、migration、health 和精简测试全部通过。

LBMA 正式 benchmark、DCE 分钟行情、过期 CME 合约完整历史、back-adjusted 长历史和通用
crypto/FX **不属于**本轮完成定义。免费 LME discovery 不阻塞主链路。

## 13. 已确认的三个决策

1. **一换一 rename 已接受**：`us_get_market -> market_get_snapshot`，工具数仍为 52；
2. **免费源能力上限已接受**：CME 至少延迟 10 分钟、DCE 只承诺 EOD、
   Yahoo 不保证到期合约历史、所有公开端点无 SLA；系统宁可 unavailable 也不购买数据；
3. **免费 LME 作为非阻塞 discovery**：只在零费用许可核验通过时接延迟/EOD，
   LBMA 继续 out。

三项已按上述方案确认，实现顺序为 `3A-0 -> 3A-1 -> 3A-2 -> 3A-3 -> 3A-4 -> 3A-5`。
每一 slice 达到退出条件后再进入下一项，避免一次性铺开多个未验证 Provider。

## 14. 官方资料

本次方案优先采用交易所、基准管理方或 Provider 官方资料：

- [CME Micro Metals contract overview](https://www.cmegroup.com/markets/microsuite/metals.html)
- [CME public delayed quotes](https://www.cmegroup.com/market-data/browse-data/delayed-quotes.html)
- [CME expiration calendar](https://www.cmegroup.com/tools-information/calendars/expiration-calendar.html)
- [CME Product and Expiration Browser export guide](https://www.cmegroup.com/tools-information/quikstrike/product-and-expiration-browser-tool-user-guide.html)
- [CME Daily Bulletin](https://www.cmegroup.com/market-data/daily-bulletin.html)
- [CME Daily Settlements](https://www.cmegroup.com/market-data/daily-settlements.html)
- [Yahoo GCZ26.CMX active-contract chain](https://finance.yahoo.com/quote/GCZ26.CMX/futures/)
- [DCE 生猪期货/期权](https://www.dce.com.cn/dce/channel/list/129.html)
- [DCE 行情数据](https://www.dce.com.cn/dalianshangpin/xqsj/index.html)
- [DCE 行情信息授权](https://www.dce.com.cn/dalianshangpin/xqsj/xqxxsq/index.html)
- [Dukascopy Quotes API](https://www.dukascopy.com/trading-tools/api/documentation/quotes)
- [Dukascopy free Historical Data Export](https://www.dukascopy.com/swiss/english/marketwatch/historical/)
- [Dukascopy historical-feed basis and volume semantics](https://www.dukascopy.com/swiss/english/about/faq/)
- [Dukascopy commodity CFD definitions](https://www.dukascopy.com/europe/english/cfd/range-of-markets/cfd-commodities/)
- [Gold-API free pricing](https://gold-api.com/pricing)
- [Gold-API terms and accuracy limits](https://gold-api.com/terms)
- [LBMA precious-metal prices and licensing](https://www.lbma.org.uk/prices-and-data)
- [LBMA precious-metal benchmark schedule](https://www.lbma.org.uk/publications/the-otc-guide/precious-metal-benchmarks)
- [LME market data access](https://www.lme.com/market-data/accessing-market-data)
- [LME reference and transparency data](https://www.lme.com/market-data/accessing-market-data/reference-and-transparency-data)
