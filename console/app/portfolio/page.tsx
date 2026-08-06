"use client";

import { useEffect, useState, type ReactNode } from "react";
import { ConsoleShell } from "../components/console-shell";
import {
  ActionButton,
  Badge,
  Card,
  DataBoundary,
  Empty,
  RefreshButton,
  displayJson,
  formatDate,
  formatDecimal,
  shortId,
} from "../components/ui";
import { listOf, postApi, useApi } from "../lib/api";

type Dict = Record<string, unknown>;
type Tab = "holdings" | "activity" | "performance" | "risk" | "watchlist";
type PositionSortKey =
  | "instrument_id"
  | "market_price"
  | "side"
  | "quantity"
  | "average_cost"
  | "market_value"
  | "unrealized_pnl"
  | "currency";
type PositionSort = { key: PositionSortKey | null; direction: "asc" | "desc" };

const TABS: Array<{ id: Tab; label: string }> = [
  { id: "holdings", label: "Holdings" },
  { id: "activity", label: "Activity" },
  { id: "performance", label: "Performance" },
  { id: "risk", label: "Risk" },
  { id: "watchlist", label: "Watchlist" },
];
const DEFAULT_POSITION_SORT: PositionSort = { key: null, direction: "asc" };
const NUMERIC_POSITION_KEYS = new Set<PositionSortKey>([
  "market_price",
  "quantity",
  "average_cost",
  "market_value",
  "unrealized_pnl",
]);

function asDict(value: unknown): Dict {
  return value && typeof value === "object" ? (value as Dict) : {};
}

/** Invocation POSTs are {tool_name,result}; direct aggregate fields are envelopes. */
function invocationResult(value: unknown): Dict {
  const record = asDict(value);
  return asDict(record.result ?? record);
}

function envelope(value: unknown): Dict {
  const result = invocationResult(value);
  return result && ("ok" in result || "data" in result || "errors" in result) ? result : {};
}

function data<T extends Dict = Dict>(value: unknown): T | null {
  return envelope(value).data && typeof envelope(value).data === "object"
    ? envelope(value).data as T
    : null;
}

function text(value: unknown, fallback = "—"): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value !== "string") return fallback;
  return value.trim() || fallback;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function idempotencyKey(prefix: string): string {
  return `console-${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function dateInput(value: Date): string {
  return value.toISOString().slice(0, 10);
}

function yearStart(): string {
  return `${new Date().getUTCFullYear()}-01-01`;
}

function errorMessage(value: unknown, fallback: string): string {
  const env = envelope(value);
  const first = Array.isArray(env.errors) ? asDict(env.errors[0]) : {};
  return `${text(first.code, "REQUEST_FAILED")} · ${text(first.message, fallback)}`;
}

function warningCodes(value: unknown): string[] {
  const direct = invocationResult(value);
  const env = envelope(value);
  const source = Object.keys(env).length > 0 ? env : direct;
  const warnings = Array.isArray(source.warnings)
    ? source.warnings.flatMap((item) => {
        if (typeof item === "string") return [item];
        const code = asDict(item).code;
        return typeof code === "string" ? [code] : [];
      })
    : [];
  return warnings.concat(stringList(source.warning_codes));
}

function formatRatioPercent(value: unknown): string {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? formatDecimal(numeric * 100) : "—";
}

function sortedPositions(positions: Dict[], sort: PositionSort): Dict[] {
  if (sort.key === null) return positions;
  const key = sort.key;
  return positions
    .map((position, index) => ({ position, index }))
    .sort((left, right) => {
      const leftValue = left.position[key];
      const rightValue = right.position[key];
      const leftMissing = leftValue === null || leftValue === undefined || leftValue === "";
      const rightMissing = rightValue === null || rightValue === undefined || rightValue === "";
      if (leftMissing || rightMissing) {
        if (leftMissing && rightMissing) return left.index - right.index;
        return leftMissing ? 1 : -1;
      }
      const comparison = NUMERIC_POSITION_KEYS.has(key)
        ? Number(leftValue) - Number(rightValue)
        : String(leftValue).localeCompare(String(rightValue), "zh-CN", { numeric: true, sensitivity: "base" });
      return (sort.direction === "asc" ? comparison : -comparison) || left.index - right.index;
    })
    .map(({ position }) => position);
}

function SortableHeader({
  label,
  column,
  sort,
  onSort,
}: {
  label: string;
  column: PositionSortKey;
  sort: PositionSort;
  onSort: (column: PositionSortKey) => void;
}) {
  const active = sort.key === column;
  return (
    <th aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}>
      <button className="sort-header" type="button" onClick={() => onSort(column)}>
        {label}<span aria-hidden="true">{active ? (sort.direction === "asc" ? "↑" : "↓") : "↕"}</span>
      </button>
    </th>
  );
}

function accountLabel(account: Dict, index: number): string {
  const provider = text(account.provider, "ACCOUNT").toUpperCase();
  const reference = text(account.account_ref, "");
  const suffix = reference.slice(-6);
  return `${provider} account ${index + 1}${suffix ? ` · ${suffix}` : ""}`;
}

function Field({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return <label className={`portfolio-field ${className}`}><span>{label}</span>{children}</label>;
}

function WarningList({ value }: { value: unknown }) {
  const codes = warningCodes(value);
  if (codes.length === 0) return null;
  return <div className="portfolio-warning-line"><span>数据提示</span><strong>{[...new Set(codes)].join(" · ")}</strong></div>;
}

function PositionCard({ position }: { position: Dict }) {
  const pnl = Number(position.unrealized_pnl ?? 0);
  return (
    <article className="portfolio-position-card">
      <header><strong>{shortId(position.instrument_id)}</strong><Badge value={text(position.currency, "UNKNOWN")} /></header>
      <small className="mono">{text(position.instrument_id)}</small>
      <dl className="portfolio-position-metrics">
        <div><dt>快照价格</dt><dd>{position.market_price == null ? "—" : formatDecimal(position.market_price, 4)}</dd></div>
        <div><dt>数量</dt><dd>{formatDecimal(position.quantity, 4)}</dd></div>
        <div><dt>成本</dt><dd>{formatDecimal(position.average_cost, 4)}</dd></div>
        <div><dt>市值（非 NAV）</dt><dd>{formatDecimal(position.market_value)}</dd></div>
        <div><dt>未实现 P/L</dt><dd className={pnl > 0 ? "text-green" : pnl < 0 ? "text-red" : ""}>{formatDecimal(position.unrealized_pnl)}</dd></div>
        <div><dt>方向</dt><dd>{text(position.side)}</dd></div>
      </dl>
      <small className="table-sub">价格时间：{formatDate(position.market_price_at)}{position.market_price == null ? " · 未提供带时间价格" : ""}</small>
    </article>
  );
}

function HoldingsTab({
  accounts,
  exposure,
  positionSorts,
  onSort,
}: {
  accounts: Dict[];
  exposure: Dict | null;
  positionSorts: Record<string, PositionSort>;
  onSort: (tableId: string, column: PositionSortKey) => void;
}) {
  const exposureData = data<Dict>(exposure);
  const exposures = listOf<Dict>(exposureData, "exposures");
  const total = exposureData?.total_value;
  const missing = stringList(exposureData?.missing_instrument_ids);
  return (
    <div className="portfolio-tab-stack">
      <Card kicker="HOLDINGS · DURABLE SNAPSHOTS" title="账户与持仓">
        <p className="card-note">只展示最新持久化账户快照。市值是按原币种的估值，不是账户净值；页面不会因查看而连接券商。</p>
        {accounts.length === 0 ? <Empty>没有持久化账户快照。请在 Activity 中显式同步账户。</Empty> : <div className="portfolio-account-grid">{accounts.map((account, index) => {
          const positions = listOf<Dict>(account, "positions");
          const openOrders = listOf<Dict>(account, "open_orders");
          const tableId = text(account.snapshot_id, `account-${index}`);
          const sort = positionSorts[tableId] ?? DEFAULT_POSITION_SORT;
          const visible = sortedPositions(positions, sort);
          return <article className="portfolio-account-card" key={tableId}>
            <header className="portfolio-account-header"><div><p className="card-kicker">{text(account.provider, "ACCOUNT").toUpperCase()}</p><h3>{accountLabel(account, index)}</h3><span className="mono">{text(account.account_ref)}</span></div><Badge value={account.degraded ? "DEGRADED" : "DURABLE"} /></header>
            <dl className="portfolio-account-facts">
              <div><dt>account_as_of</dt><dd>{formatDate(account.account_as_of)}</dd></div>
              <div><dt>fetched_at</dt><dd>{formatDate(account.fetched_at)}</dd></div>
              <div><dt>现金</dt><dd>{formatDecimal(account.cash)} {text(account.base_currency)}</dd></div>
              <div><dt>Buying power</dt><dd>{formatDecimal(account.buying_power)} {text(account.base_currency)}</dd></div>
              <div><dt>净资产 / NAV</dt><dd>{formatDecimal(account.net_assets)} {text(account.base_currency)}</dd></div>
              <div><dt>已用保证金</dt><dd>{formatDecimal(account.margin_used)} {text(account.base_currency)}</dd></div>
              <div><dt>账户环境</dt><dd>{text(account.environment)}</dd></div>
              <div><dt>未成交订单</dt><dd>{openOrders.length}</dd></div>
            </dl>
            <WarningList value={{ warnings: account.warning_codes }} />
            {positions.length === 0 ? <Empty>该账户没有持仓。</Empty> : <>
              <div className="table-wrap portfolio-desktop-table"><table><thead><tr>
                <SortableHeader label="标的" column="instrument_id" sort={sort} onSort={(column) => onSort(tableId, column)} />
                <SortableHeader label="快照价格" column="market_price" sort={sort} onSort={(column) => onSort(tableId, column)} />
                <SortableHeader label="方向" column="side" sort={sort} onSort={(column) => onSort(tableId, column)} />
                <SortableHeader label="数量" column="quantity" sort={sort} onSort={(column) => onSort(tableId, column)} />
                <SortableHeader label="成本" column="average_cost" sort={sort} onSort={(column) => onSort(tableId, column)} />
                <SortableHeader label="市值（非 NAV）" column="market_value" sort={sort} onSort={(column) => onSort(tableId, column)} />
                <SortableHeader label="未实现 P/L" column="unrealized_pnl" sort={sort} onSort={(column) => onSort(tableId, column)} />
                <SortableHeader label="币种" column="currency" sort={sort} onSort={(column) => onSort(tableId, column)} />
              </tr></thead><tbody>{visible.map((position) => <tr key={`${tableId}-${text(position.instrument_id)}`}><td><strong>{shortId(position.instrument_id)}</strong><small className="table-sub mono">{text(position.instrument_id)}</small></td><td>{position.market_price == null ? <><span>—</span><small className="table-sub">无带时间价格</small></> : <><strong>{formatDecimal(position.market_price, 4)}</strong><small className="table-sub">{formatDate(position.market_price_at)}</small></>}</td><td>{text(position.side)}</td><td>{formatDecimal(position.quantity, 4)}</td><td>{formatDecimal(position.average_cost, 4)}</td><td>{formatDecimal(position.market_value)}</td><td className={Number(position.unrealized_pnl ?? 0) > 0 ? "text-green" : Number(position.unrealized_pnl ?? 0) < 0 ? "text-red" : ""}>{formatDecimal(position.unrealized_pnl)}</td><td>{text(position.currency)}</td></tr>)}</tbody></table></div>
              <div className="portfolio-mobile-cards">{visible.map((position) => <PositionCard key={`${tableId}-${text(position.instrument_id)}`} position={position} />)}</div>
            </>}
          </article>;
        })}</div>}
      </Card>
      <Card kicker="EXPOSURE · NATIVE CURRENCY" title="组合暴露">
        <div className="portfolio-exposure-summary"><div><span>组合 total_value</span><strong>{total == null ? "—" : formatDecimal(total)}</strong><small>仅为估值，不代表账户净值/NAV</small></div><div><span>缺少估值的标的</span><strong>{missing.length}</strong><small>{missing.length > 0 ? missing.map(shortId).join(" · ") : "无"}</small></div><div><span>状态</span><strong><Badge value={exposureData?.degraded ? "DEGRADED" : "DURABLE"} /></strong><small>{formatDate(exposureData?.as_of)}</small></div></div>
        {exposures.length === 0 ? <Empty>暂无按维度暴露数据。</Empty> : <div className="portfolio-exposure-grid">{exposures.map((item, index) => <div key={`${text(item.dimension)}-${text(item.key)}-${index}`}><span>{text(item.dimension)}</span><strong>{text(item.key)}</strong><small>{formatDecimal(item.value)} · 权重 {formatRatioPercent(item.weight)}%</small></div>)}</div>}
        <WarningList value={exposure} />
      </Card>
    </div>
  );
}

function ActivityTab({
  transactions,
  coverage,
}: {
  transactions: Dict | null;
  coverage: Dict | null;
}) {
  const transactionData = data<Dict>(transactions);
  const coverageData = data<Dict>(coverage);
  const transactionRows = listOf<Dict>(transactionData, "transactions");
  const receipts = listOf<Dict>(coverageData, "receipts").length > 0 ? listOf<Dict>(coverageData, "receipts") : listOf<Dict>(transactionData, "coverage_receipts");
  return <div className="portfolio-tab-stack">
    <Card kicker="ACTIVITY · DURABLE LEDGER" title="交易记录">
      <p className="card-note">下方记录只来自数据库。同步交易是显式动作，页面加载不会刷新券商，也不会将缺失费用当成 0。</p>
      {transactionRows.length === 0 ? <Empty>暂无持久化交易记录。点击上方“同步交易”获取最新活动。</Empty> : <>
        <div className="table-wrap portfolio-desktop-table"><table><thead><tr><th>时间</th><th>Provider / Account</th><th>标的</th><th>类型</th><th>方向</th><th>数量</th><th>价格</th><th>现金</th><th>费用</th><th>币种</th></tr></thead><tbody>{transactionRows.map((row, index) => <tr key={`${text(row.provider_transaction_id, "tx")}-${index}`}><td>{formatDate(row.occurred_at)}</td><td><strong>{text(row.provider)}</strong><small className="table-sub mono">{text(row.account_ref)}</small></td><td>{row.instrument_id ? <><strong>{shortId(row.instrument_id)}</strong><small className="table-sub mono">{text(row.instrument_id)}</small></> : "现金活动"}</td><td>{text(row.kind)}</td><td>{text(row.side)}</td><td>{formatDecimal(row.quantity, 4)}</td><td>{formatDecimal(row.price, 4)}</td><td>{formatDecimal(row.cash_amount)}</td><td>{formatDecimal(row.fees)}</td><td>{text(row.currency)}</td></tr>)}</tbody></table></div>
        <div className="portfolio-mobile-cards">{transactionRows.map((row, index) => <article className="portfolio-activity-card" key={`mobile-${text(row.provider_transaction_id, "tx")}-${index}`}><header><strong>{row.instrument_id ? shortId(row.instrument_id) : "现金活动"}</strong><Badge value={text(row.kind)} /></header><small>{formatDate(row.occurred_at)} · {text(row.provider)} · {text(row.account_ref)}</small><dl className="portfolio-position-metrics"><div><dt>方向 / 数量</dt><dd>{text(row.side)} · {formatDecimal(row.quantity, 4)}</dd></div><div><dt>价格</dt><dd>{formatDecimal(row.price, 4)}</dd></div><div><dt>现金 / 费用</dt><dd>{formatDecimal(row.cash_amount)} / {formatDecimal(row.fees)}</dd></div><div><dt>币种</dt><dd>{text(row.currency)}</dd></div></dl></article>)}</div>
      </>}
      <WarningList value={transactions} />
    </Card>
    <Card kicker="COVERAGE · RECEIPTS" title="活动覆盖">
      <div className="coverage-overview"><div><span>Overall</span><strong><Badge value={text(coverageData?.overall_status, "UNKNOWN")} /></strong></div><div><span>Receipts</span><strong>{receipts.length}</strong></div><div><span>Unavailable providers</span><strong>{stringList(transactionData?.unavailable_providers).length}</strong></div></div>
      {receipts.length === 0 ? <Empty>暂无 coverage receipt。</Empty> : <div className="coverage-list">{receipts.map((receipt, index) => <article key={`${text(receipt.receipt_id, "receipt")}-${index}`}><header><strong>{text(receipt.provider)} · {text(receipt.account_ref)}</strong><Badge value={text(receipt.status, "UNKNOWN")} /></header><div className="coverage-grid"><span>窗口<strong>{formatDate(receipt.effective_start)} → {formatDate(receipt.effective_end)}</strong></span><span>事件 / snapshot<strong>{text(receipt.event_count, "0")} / {text(receipt.snapshot_count, "0")}</strong></span><span>新增 / 重复<strong>{text(receipt.inserted_count, "0")} / {text(receipt.duplicate_count, "0")}</strong></span><span>缺失类别<strong>{stringList(receipt.unavailable_kinds).join(" · ") || "无"}</strong></span></div><small className="table-sub">gap: {stringList(receipt.gap_codes).join(" · ") || "无"} · fetched {formatDate(receipt.fetched_at)}</small></article>)}</div>}
    </Card>
  </div>;
}

function PerformanceTab() {
  const [start, setStart] = useState(yearStart);
  const [end, setEnd] = useState(() => dateInput(new Date()));
  const [method, setMethod] = useState<"FIFO" | "BROKER_REPORTED">("FIFO");
  const [result, setResult] = useState<Dict | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function calculate() {
    setLoading(true); setError(null);
    try {
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: "portfolio_analyze", arguments: { request: { operation: "performance_summary", start: `${start}T00:00:00Z`, end: `${end}T23:59:59.999999Z`, cost_basis_method: method } } });
      const resultEnvelope = envelope(invocationResult(response));
      if (resultEnvelope.ok === false) throw new Error(errorMessage(resultEnvelope, "业绩归因失败"));
      setResult(resultEnvelope);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "业绩归因失败"); }
    finally { setLoading(false); }
  }
  const performance = data<Dict>(result);
  const accounts = listOf<Dict>(performance, "accounts");
  return <Card kicker="PERFORMANCE · DURABLE ATTRIBUTION" title="实际损益账本" action={result ? <Badge value={text(performance?.status, "LOADED")} /> : undefined}>
    <p className="card-note">按原币种重建 FIFO 或读取券商报告口径；不会隐式换汇，也不会把累计 P/L 冒充区间收益。</p>
    <div className="performance-controls"><Field label="开始日期（UTC）"><input type="date" value={start} onChange={(event) => setStart(event.target.value)} /></Field><Field label="结束日期（UTC）"><input type="date" value={end} onChange={(event) => setEnd(event.target.value)} /></Field><Field label="成本口径"><select value={method} onChange={(event) => setMethod(event.target.value as "FIFO" | "BROKER_REPORTED")}><option value="FIFO">FIFO 事件重建</option><option value="BROKER_REPORTED">券商报告</option></select></Field><ActionButton onClick={() => { void calculate(); }} busy={loading}>计算归因</ActionButton></div>
    {error && <div className="inline-error" role="alert">{error}</div>}
    <WarningList value={result} />
    {performance && accounts.length === 0 ? <Empty>当前区间没有可归因的持久化账户事实。</Empty> : <div className="performance-results">{accounts.map((account, index) => { const instruments = listOf<Dict>(account, "instruments"); return <article className="performance-account" key={`${text(account.account_ref)}-${index}`}><header><div><strong>{accountLabel(account, index)}</strong><span>{text(account.currency)} · {text(account.cost_basis_method)}</span></div><Badge value={text(account.status, "UNKNOWN")} /></header><div className="account-summary"><article><span>已实现 P/L（费后）</span><strong>{formatDecimal(account.realized_pnl_after_fees)}</strong><small>费前 {formatDecimal(account.realized_pnl_before_fees)}</small></article><article><span>未实现 P/L</span><strong>{formatDecimal(account.unrealized_pnl_before_fees)}</strong><small>估值快照 {formatDate(account.snapshot_as_of)}</small></article><article><span>股息 / 利息</span><strong>{formatDecimal(account.dividends)} / {formatDecimal(account.interest)}</strong><small>已知费用 {formatDecimal(account.known_fees)}</small></article><article><span>外部净现金流</span><strong>{formatDecimal(account.net_external_cash_flow)}</strong><small>{instruments.length} 个标的事实</small></article></div><details><summary>下钻标的与事件</summary><div className="table-wrap"><table><thead><tr><th>标的</th><th>已实现费前</th><th>已实现费后</th><th>未实现</th><th>期末数量</th><th>事件</th><th>Warning</th></tr></thead><tbody>{instruments.map((instrument, instrumentIndex) => <tr key={`${text(instrument.instrument_id)}-${instrumentIndex}`}><td><strong>{shortId(instrument.instrument_id)}</strong><small className="table-sub mono">{text(instrument.instrument_id)}</small></td><td>{formatDecimal(instrument.realized_pnl_before_fees)}</td><td>{formatDecimal(instrument.realized_pnl_after_fees)}</td><td>{formatDecimal(instrument.unrealized_pnl_before_fees)}</td><td>{formatDecimal(instrument.ending_quantity, 4)}</td><td>{Array.isArray(instrument.activity_ids) ? instrument.activity_ids.length : 0}</td><td>{stringList(instrument.warning_codes).join(" · ") || "—"}</td></tr>)}</tbody></table></div></details></article>; })}</div>}
  </Card>;
}

const POLICY_FIELDS: Array<{ key: string; label: string; step?: string }> = [
  { key: "single_position_max_percent", label: "单一持仓上限 %" },
  { key: "gross_exposure_max_percent", label: "总暴露上限 %" },
  { key: "minimum_cash_percent", label: "最低现金 %" },
  { key: "margin_usage_max_percent", label: "保证金使用上限 %" },
  { key: "max_account_age_seconds", label: "账户最大年龄（秒）" },
  { key: "max_price_age_seconds", label: "价格最大年龄（秒）" },
  { key: "risk_budget_max_percent", label: "风险预算上限 %" },
  { key: "theme_exposure_max_percent", label: "主题暴露上限 %" },
  { key: "drawdown_max_percent", label: "最大回撤 %" },
  { key: "liquidity_participation_max_percent", label: "流动性参与上限 %" },
  { key: "correlation_max_absolute", label: "相关性绝对值上限", step: "0.01" },
  { key: "event_blackout_days", label: "事件 blackout 天数" },
];

function PolicyForm({ policy, onSaved }: { policy: Dict; onSaved: () => void }) {
  const initial = Object.fromEntries(POLICY_FIELDS.map(({ key }) => [key, text(policy[key], "0")])) as Record<string, string>;
  const [values, setValues] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    if (!window.confirm("确认追加新的风险政策版本？历史版本不会被覆盖，也不会下单。")) return;
    setBusy(true); setError(null);
    try {
      const request: Dict = { ...Object.fromEntries(POLICY_FIELDS.map(({ key }) => [key, Number(values[key])])), expected_version: Number(policy.version), confirmed_by: "user", idempotency_key: idempotencyKey("risk-policy") };
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: "risk_policy_update", arguments: request, confirmation: "risk_policy_update" });
      if (envelope(invocationResult(response)).ok === false) throw new Error(errorMessage(response, "Risk Policy 更新失败"));
      onSaved();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Risk Policy 更新失败"); }
    finally { setBusy(false); }
  }
  return <details className="portfolio-policy-editor"><summary>新建 Policy 版本（expected_version={text(policy.version)}）</summary><div className="portfolio-policy-form">{POLICY_FIELDS.map(({ key, label, step }) => <Field label={label} key={key}><input type="number" step={step ?? "0.01"} value={values[key]} onChange={(event) => setValues((current) => ({ ...current, [key]: event.target.value }))} /></Field>)}</div>{error && <div className="inline-error">{error}</div>}<div className="portfolio-form-actions"><ActionButton onClick={() => { void save(); }} busy={busy}>确认追加版本</ActionButton><small className="muted">用户确认、幂等键和 expected_version 均写入审计记录。</small></div></details>;
}

function RiskChecks({ result }: { result: Dict | null }) {
  const payload = data<Dict>(result);
  const checks = listOf<Dict>(payload, "checks");
  if (!result) return <Empty>尚未运行 Risk check。</Empty>;
  return <div className="risk-result"><header><strong>Risk check · {text(payload?.overall_status, "UNKNOWN")}</strong><Badge value={payload?.execution_effect === true ? "EXECUTION_EFFECT" : "READ_ONLY"} /></header><div className="table-wrap"><table><thead><tr><th>规则</th><th>状态</th><th>实际</th><th>上限</th><th>范围</th><th>说明</th></tr></thead><tbody>{checks.map((check, index) => <tr key={`${text(check.rule_code)}-${index}`}><td>{text(check.rule_code)}</td><td><Badge value={text(check.status)} /></td><td>{formatDecimal(check.actual)}</td><td>{formatDecimal(check.limit)}</td><td>{text(check.scope)}</td><td>{text(check.message)}</td></tr>)}</tbody></table></div>{payload?.hypothetical ? <div className="portfolio-hypothetical"><span>假设新增</span><strong>{shortId(asDict(payload.hypothetical).instrument_id)} · {formatDecimal(asDict(payload.hypothetical).quantity, 4)} · {formatDecimal(asDict(payload.hypothetical).assumed_price)} {text(asDict(payload.hypothetical).currency)}</strong></div> : null}{payload?.position_sizing ? <details><summary>Position Sizing / constraints</summary><pre className="portfolio-json">{displayJson(payload.position_sizing)}</pre></details> : null}<WarningList value={result} /></div>;
}

function RiskTab({ policyEnvelope, riskEnvelope, onRefresh }: { policyEnvelope: Dict | null; riskEnvelope: Dict | null; onRefresh: () => void }) {
  const policy = data<Dict>(policyEnvelope);
  const [localRiskEnvelope, setLocalRiskEnvelope] = useState<Dict | null>(riskEnvelope);
  const [whatIfMode, setWhatIfMode] = useState<"manual" | "trade_plan">("manual");
  const [instrument, setInstrument] = useState("");
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [tradePlan, setTradePlan] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setLocalRiskEnvelope(riskEnvelope), [riskEnvelope]);
  async function check() {
    setBusy(true); setError(null);
    try {
      const request: Dict = { operation: "check" };
      if (whatIfMode === "manual") Object.assign(request, { hypothetical_instrument_id: instrument.trim(), hypothetical_quantity: Number(quantity), hypothetical_assumed_price: Number(price), hypothetical_currency: currency.trim() });
      else request.trade_plan_id = tradePlan.trim();
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: "portfolio_risk_get", arguments: { request } });
      const result = invocationResult(response);
      if (envelope(result).ok === false) throw new Error(errorMessage(result, "Risk check 失败"));
      setLocalRiskEnvelope(result);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Risk check 失败"); }
    finally { setBusy(false); }
  }
  // The parent owns the aggregate refresh. Risk check is intentionally local to this tab;
  // a custom event lets the page retain the durable aggregate while replacing this result.
  return <div className="portfolio-tab-stack">
    <Card kicker="RISK POLICY · APPEND ONLY" title="当前风险政策" action={policy ? <Badge value={policy.is_system_default ? "SYSTEM_DEFAULT" : `VERSION ${text(policy.version)}`} /> : undefined}>
      {!policy ? <Empty>Risk Policy 不可用。</Empty> : <><div className="policy-grid">{POLICY_FIELDS.map(({ key, label }) => <div key={key}><span>{label}</span><strong>{text(policy[key])}</strong></div>)}</div><p className="card-note">确认人：{text(policy.confirmed_by)} · 创建于 {formatDate(policy.created_at)} · schema {text(policy.schema_version)}</p><PolicyForm policy={policy} onSaved={onRefresh} /></>}
    </Card>
    <Card kicker="RISK CHECK · READ ONLY" title="当前风险检查">
      <p className="card-note">默认只读 durable risk_check。假设新增只计算，不修改账户、Policy、Trade Plan，也不生成订单。</p>
      <RiskChecks result={localRiskEnvelope} />
      <details className="portfolio-what-if"><summary>运行 What-if</summary><div className="portfolio-segmented" role="tablist"><button type="button" className={whatIfMode === "manual" ? "selected" : ""} onClick={() => setWhatIfMode("manual")}>手动假设</button><button type="button" className={whatIfMode === "trade_plan" ? "selected" : ""} onClick={() => setWhatIfMode("trade_plan")}>Trade Plan</button></div>{whatIfMode === "manual" ? <div className="portfolio-form-grid"><Field label="Instrument ID"><input value={instrument} onChange={(event) => setInstrument(event.target.value)} placeholder="equity:US:NVDA" /></Field><Field label="数量"><input type="number" min="0" step="any" value={quantity} onChange={(event) => setQuantity(event.target.value)} /></Field><Field label="假设价格"><input type="number" min="0" step="any" value={price} onChange={(event) => setPrice(event.target.value)} /></Field><Field label="原币种"><input value={currency} onChange={(event) => setCurrency(event.target.value)} /></Field></div> : <Field label="Trade Plan ID"><input value={tradePlan} onChange={(event) => setTradePlan(event.target.value)} placeholder="trade_plan_<uuid7>" /></Field>}<div className="portfolio-form-actions"><ActionButton onClick={() => { void check(); }} busy={busy}>运行只读检查</ActionButton></div>{error && <div className="inline-error">{error}</div>}</details>
    </Card>
  </div>;
}

function WatchlistTab({
  watchlist,
  onRefresh,
  onMessage,
}: {
  watchlist: Dict | null;
  onRefresh: () => void;
  onMessage: (message: string, error?: boolean) => void;
}) {
  const groupsEnvelope = asDict(watchlist?.groups);
  const itemsEnvelope = asDict(watchlist?.items);
  const groupsData = data<Dict>(groupsEnvelope);
  const itemsData = data<Dict>(itemsEnvelope);
  const groups = listOf<Dict>(groupsData, "groups");
  const defaultGroup = text(asDict(watchlist?.scope).group_name, "");
  const [groupName, setGroupName] = useState(defaultGroup);
  const [items, setItems] = useState(listOf<Dict>(itemsData, "items"));
  const [selectedGroup, setSelectedGroup] = useState<Dict | null>(groups.find((item) => text(item.name) === groupName) ?? groups[0] ?? null);
  const [busy, setBusy] = useState<string | null>(null);
  const [instrument, setInstrument] = useState("");
  const [displayName, setDisplayName] = useState("");
  const writable = Boolean(selectedGroup?.writable) && Boolean(selectedGroup?.active);
  useEffect(() => {
    setItems(listOf<Dict>(itemsData, "items"));
  }, [itemsData]);
  async function selectGroup(value: string) {
    setGroupName(value); const next = groups.find((group) => text(group.name) === value) ?? null; setSelectedGroup(next);
    if (!value || value === defaultGroup) { setItems(listOf<Dict>(itemsData, "items")); return; }
    setBusy("read");
    try {
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: "watchlist_get", arguments: { request: { operation: "items", group_name: value, limit: 500, offset: 0 } } });
      const result = invocationResult(response); if (envelope(result).ok === false) throw new Error(errorMessage(result, "读取 Watchlist 失败"));
      setItems(listOf<Dict>(data<Dict>(result), "items"));
    } catch (cause) { onMessage(cause instanceof Error ? cause.message : "读取 Watchlist 失败", true); }
    finally { setBusy(null); }
  }
  async function mutate(operation: "add" | "remove", item?: Dict) {
    if (!writable) { onMessage("当前分组不可写，不能修改 Watchlist。", true); return; }
    const label = operation === "add" ? instrument.trim() : text(item?.display_name, text(item?.provider_code));
    if (!window.confirm(`确认${operation === "add" ? "添加" : "移除"} ${label}？`)) return;
    setBusy(operation);
    try {
      const request: Dict = operation === "add"
        ? { operation: "add", instrument_id: instrument.trim(), group_name: text(selectedGroup?.name, ""), display_name: displayName.trim() || null, confirmed_by: "user", idempotency_key: idempotencyKey("watchlist-add") }
        : { operation: "remove", membership_id: text(item?.membership_id), confirmed_by: "user", idempotency_key: idempotencyKey("watchlist-remove") };
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: "watchlist_manage", arguments: { request }, confirmation: "watchlist_manage" });
      const result = invocationResult(response); if (envelope(result).ok === false) throw new Error(errorMessage(result, "Watchlist 写入失败"));
      onMessage("Watchlist 已更新，正在刷新 durable state。"); setInstrument(""); setDisplayName(""); onRefresh();
      await selectGroup(text(selectedGroup?.name, ""));
    } catch (cause) { onMessage(cause instanceof Error ? cause.message : "Watchlist 写入失败", true); }
    finally { setBusy(null); }
  }
  return <div className="portfolio-tab-stack">
    <Card kicker="WATCHLIST · DURABLE HUB" title="自选清单" action={<Badge value={text(groupsData?.source, "UNKNOWN")} />}>
      <p className="card-note">读取来自数据库的完整分组与成员关系。只有显式同步才接触 Moomoo；研究 Case 不会因外部删除而被删除。</p>
      <div className="watchlist-toolbar"><Field label="分组"><select value={groupName} onChange={(event) => { void selectGroup(event.target.value); }} disabled={busy === "read"}><option value="">选择分组</option>{groups.map((group) => <option key={text(group.group_id)} value={text(group.name)}>{text(group.name)}{group.writable ? " · 可写" : " · 只读"}</option>)}</select></Field><span className="muted">{items.length} 个成员 · {text(itemsData?.total_count, String(items.length))}</span></div>
      {groups.length === 0 ? <Empty>没有持久化 Watchlist 分组。请显式同步自选。</Empty> : <div className="watchlist-groups">{groups.map((group) => <button type="button" key={text(group.group_id)} className={text(group.name) === text(selectedGroup?.name) ? "selected" : ""} onClick={() => { void selectGroup(text(group.name)); }}><strong>{text(group.name)}</strong><small>{group.writable ? "可写" : "只读"} · {group.active ? "active" : "inactive"}</small></button>)}</div>}
      {items.length === 0 ? <Empty>该分组没有成员。</Empty> : <div className="watchlist-items">{items.map((item, index) => <article key={`${text(item.membership_id, "membership")}-${index}`}><div><strong>{text(item.display_name, text(item.provider_code))}</strong><small className="mono">{text(item.instrument_id, "INVALID_INSTRUMENT")} · {text(item.provider_asset_type, "asset type unknown")}</small><div className="watchlist-links">{stringList(item.investment_case_ids).map((caseId) => <a href="/research" key={caseId}>Case {shortId(caseId)}</a>)}{item.research_supported === false && <span className="warning-text">unsupported instrument</span>}</div></div><div className="watchlist-item-action">{Boolean(item.active) && <Badge value={text(item.source, "UNKNOWN")} />}{writable && <button type="button" className="close-button" onClick={() => { void mutate("remove", item); }} disabled={busy === "remove"}>移除</button>}</div></article>)}</div>}
      {writable && <details className="watchlist-editor"><summary>添加成员</summary><div className="portfolio-form-grid"><Field label="Instrument ID"><input value={instrument} onChange={(event) => setInstrument(event.target.value)} placeholder="equity:US:NVDA" /></Field><Field label="显示名称（可选）"><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="NVDA" /></Field></div><div className="portfolio-form-actions"><ActionButton onClick={() => { void mutate("add"); }} busy={busy === "add"}>添加到当前分组</ActionButton></div></details>}
      <WarningList value={watchlist} />
    </Card>
  </div>;
}

export default function PortfolioPage() {
  const result = useApi<Dict>("/api/portfolio?transaction_limit=500&coverage_limit=100");
  const [tab, setTab] = useState<Tab>("holdings");
  const [syncing, setSyncing] = useState<string | null>(null);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const [positionSorts, setPositionSorts] = useState<Record<string, PositionSort>>({});
  const aggregate = result.data ?? {};
  const accountsEnvelope = asDict(aggregate.accounts);
  const exposureEnvelope = asDict(aggregate.exposure);
  const transactionsEnvelope = asDict(aggregate.transactions);
  const coverageEnvelope = asDict(aggregate.coverage);
  const riskPolicyEnvelope = asDict(aggregate.risk_policy);
  const riskCheckEnvelope = asDict(aggregate.risk_check);
  const watchlist = asDict(aggregate.watchlist);
  const accountsData = data<Dict>(accountsEnvelope);
  const accounts = listOf<Dict>(accountsData, "accounts");

  useEffect(() => {
    const update = () => {
      const value = window.location.hash.replace(/^#/, "") as Tab;
      if (TABS.some((item) => item.id === value)) setTab(value);
    };
    update(); window.addEventListener("hashchange", update); return () => window.removeEventListener("hashchange", update);
  }, []);
  function selectTab(next: Tab) {
    setTab(next); window.history.replaceState(null, "", `#${next}`);
  }
  function changePositionSort(tableId: string, column: PositionSortKey) {
    setPositionSorts((current) => { const previous = current[tableId] ?? DEFAULT_POSITION_SORT; return { ...current, [tableId]: { key: column, direction: previous.key === column && previous.direction === "asc" ? "desc" : "asc" } }; });
  }
  async function refreshAggregate() { result.refresh(); }
  async function sync(operation: "accounts" | "transactions" | "watchlist") {
    const labels = { accounts: "账户", transactions: "交易", watchlist: "Watchlist" };
    if (!window.confirm(`确认连接已配置上游并同步${labels[operation]}？`)) return;
    setSyncing(operation); setMessage(null);
    try {
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: "external_state_sync", arguments: { request: { operation } }, confirmation: "external_state_sync" });
      const resultEnvelope = envelope(invocationResult(response)); if (resultEnvelope.ok === false) throw new Error(errorMessage(resultEnvelope, "同步失败"));
      setMessage({ text: `${labels[operation]}同步成功，正在刷新 durable state。` }); result.refresh();
    } catch (cause) { setMessage({ text: cause instanceof Error ? cause.message : "同步失败", error: true }); }
    finally { setSyncing(null); }
  }
  return <ConsoleShell active="portfolio" eyebrow="Durable portfolio hub" title="Portfolio">
    <DataBoundary loading={result.loading} error={result.error}>
      <div className="toolbar portfolio-toolbar"><p>页面加载只读取持久化快照、活动、Risk 和 Watchlist；不会隐式刷新 Provider。账户、交易、Watchlist 同步均需显式确认。</p><div className="toolbar-actions"><ActionButton onClick={() => { void sync("accounts"); }} busy={syncing === "accounts"}>同步账户</ActionButton><ActionButton onClick={() => { void sync("transactions"); }} busy={syncing === "transactions"}>同步交易</ActionButton><ActionButton onClick={() => { void sync("watchlist"); }} busy={syncing === "watchlist"}>同步 Watchlist</ActionButton><RefreshButton onClick={refreshAggregate} loading={result.loading} /></div></div>
      {message && <div className={message.error ? "inline-error" : "inline-success"} role="status">{message.text}</div>}
      <nav className="portfolio-tabs" aria-label="Portfolio sections" role="tablist">{TABS.map((item) => <button type="button" role="tab" aria-selected={tab === item.id} className={tab === item.id ? "selected" : ""} onClick={() => selectTab(item.id)} key={item.id}>{item.label}</button>)}</nav>
      <div className="portfolio-tab-content">
        {tab === "holdings" && <HoldingsTab accounts={accounts} exposure={exposureEnvelope} positionSorts={positionSorts} onSort={changePositionSort} />}
        {tab === "activity" && <ActivityTab transactions={transactionsEnvelope} coverage={coverageEnvelope} />}
        {tab === "performance" && <PerformanceTab />}
        {tab === "risk" && <RiskTab policyEnvelope={riskPolicyEnvelope} riskEnvelope={riskCheckEnvelope} onRefresh={result.refresh} />}
        {tab === "watchlist" && <WatchlistTab watchlist={watchlist} onRefresh={refreshAggregate} onMessage={(textValue, error) => setMessage({ text: textValue, error })} />}
      </div>
    </DataBoundary>
  </ConsoleShell>;
}
