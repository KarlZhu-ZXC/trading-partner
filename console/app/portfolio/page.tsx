"use client";

import { useEffect, useState, type KeyboardEvent, type ReactNode } from "react";
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
type Tab = "holdings" | "activity" | "performance" | "risk";
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
  return <div className="portfolio-warning-line"><span>Data note</span><strong>{[...new Set(codes)].join(" · ")}</strong></div>;
}

function PositionCard({ position }: { position: Dict }) {
  const pnl = Number(position.unrealized_pnl ?? 0);
  return (
    <article className="portfolio-position-card">
      <header><strong>{shortId(position.instrument_id)}</strong><Badge value={text(position.currency, "UNKNOWN")} /></header>
      <small className="mono">{text(position.instrument_id)}</small>
      <dl className="portfolio-position-metrics">
        <div><dt>Snapshot price</dt><dd>{position.market_price == null ? "—" : formatDecimal(position.market_price, 4)}</dd></div>
        <div><dt>Quantity</dt><dd>{formatDecimal(position.quantity, 4)}</dd></div>
        <div><dt>Cost</dt><dd>{formatDecimal(position.average_cost, 4)}</dd></div>
        <div><dt>Market value (not NAV)</dt><dd>{formatDecimal(position.market_value)}</dd></div>
        <div><dt>Unrealized P/L</dt><dd className={pnl > 0 ? "text-green" : pnl < 0 ? "text-red" : ""}>{formatDecimal(position.unrealized_pnl)}</dd></div>
        <div><dt>Side</dt><dd>{text(position.side)}</dd></div>
      </dl>
      <small className="table-sub">Price time: {formatDate(position.market_price_at)}{position.market_price == null ? " · No timestamped price" : ""}</small>
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
      <Card kicker="HOLDINGS · DURABLE SNAPSHOTS" title="Accounts & holdings">
        <p className="card-note">Shows only the latest durable account snapshots. Market value is in native currency, not account NAV; viewing this page never connects to a broker.</p>
        {accounts.length === 0 ? <Empty>No durable account snapshots. Explicitly sync accounts from Activity.</Empty> : <div className="portfolio-account-grid">{accounts.map((account, index) => {
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
              <div><dt>Cash</dt><dd>{formatDecimal(account.cash)} {text(account.base_currency)}</dd></div>
              <div><dt>Buying power</dt><dd>{formatDecimal(account.buying_power)} {text(account.base_currency)}</dd></div>
              <div><dt>Net assets / NAV</dt><dd>{formatDecimal(account.net_assets)} {text(account.base_currency)}</dd></div>
              <div><dt>Margin used</dt><dd>{formatDecimal(account.margin_used)} {text(account.base_currency)}</dd></div>
              <div><dt>Account environment</dt><dd>{text(account.environment)}</dd></div>
              <div><dt>Open orders</dt><dd>{openOrders.length}</dd></div>
            </dl>
            <WarningList value={{ warnings: account.warning_codes }} />
            {positions.length === 0 ? <Empty>This account has no positions.</Empty> : <>
              <div className="table-wrap portfolio-desktop-table"><table><thead><tr>
                <SortableHeader label="Instrument" column="instrument_id" sort={sort} onSort={(column) => onSort(tableId, column)} />
                <SortableHeader label="Snapshot price" column="market_price" sort={sort} onSort={(column) => onSort(tableId, column)} />
                <SortableHeader label="Side" column="side" sort={sort} onSort={(column) => onSort(tableId, column)} />
                <SortableHeader label="Quantity" column="quantity" sort={sort} onSort={(column) => onSort(tableId, column)} />
                <SortableHeader label="Cost" column="average_cost" sort={sort} onSort={(column) => onSort(tableId, column)} />
                <SortableHeader label="Market value (not NAV)" column="market_value" sort={sort} onSort={(column) => onSort(tableId, column)} />
                <SortableHeader label="Unrealized P/L" column="unrealized_pnl" sort={sort} onSort={(column) => onSort(tableId, column)} />
                <SortableHeader label="Currency" column="currency" sort={sort} onSort={(column) => onSort(tableId, column)} />
              </tr></thead><tbody>{visible.map((position) => <tr key={`${tableId}-${text(position.instrument_id)}`}><td><strong>{shortId(position.instrument_id)}</strong><small className="table-sub mono">{text(position.instrument_id)}</small></td><td>{position.market_price == null ? <><span>—</span><small className="table-sub">No timestamped price</small></> : <><strong>{formatDecimal(position.market_price, 4)}</strong><small className="table-sub">{formatDate(position.market_price_at)}</small></>}</td><td>{text(position.side)}</td><td>{formatDecimal(position.quantity, 4)}</td><td>{formatDecimal(position.average_cost, 4)}</td><td>{formatDecimal(position.market_value)}</td><td className={Number(position.unrealized_pnl ?? 0) > 0 ? "text-green" : Number(position.unrealized_pnl ?? 0) < 0 ? "text-red" : ""}>{formatDecimal(position.unrealized_pnl)}</td><td>{text(position.currency)}</td></tr>)}</tbody></table></div>
              <div className="portfolio-mobile-cards">{visible.map((position) => <PositionCard key={`${tableId}-${text(position.instrument_id)}`} position={position} />)}</div>
            </>}
          </article>;
        })}</div>}
      </Card>
      <Card kicker="EXPOSURE · NATIVE CURRENCY" title="Portfolio exposure">
        <div className="portfolio-exposure-summary"><div><span>Portfolio total_value</span><strong>{total == null ? "—" : formatDecimal(total)}</strong><small>Valuation only; not account NAV</small></div><div><span>Instruments missing valuation</span><strong>{missing.length}</strong><small>{missing.length > 0 ? missing.map(shortId).join(" · ") : "None"}</small></div><div><span>Status</span><strong><Badge value={exposureData?.degraded ? "DEGRADED" : "DURABLE"} /></strong><small>{formatDate(exposureData?.as_of)}</small></div></div>
        {exposures.length === 0 ? <Empty>No exposure data by dimension yet.</Empty> : <div className="portfolio-exposure-grid">{exposures.map((item, index) => <div key={`${text(item.dimension)}-${text(item.key)}-${index}`}><span>{text(item.dimension)}</span><strong>{text(item.key)}</strong><small>{formatDecimal(item.value)} · Weight {formatRatioPercent(item.weight)}%</small></div>)}</div>}
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
    <Card kicker="ACTIVITY · DURABLE LEDGER" title="Transaction history">
      <p className="card-note">These records come only from the database. Transaction sync is explicit; loading the page never refreshes the broker or treats missing fees as zero.</p>
      {transactionRows.length === 0 ? <Empty>No durable transaction records. Click “Sync transactions” above to fetch the latest activity.</Empty> : <>
        <div className="table-wrap portfolio-desktop-table"><table><thead><tr><th>Time</th><th>Provider / Account</th><th>Instrument</th><th>Type</th><th>Side</th><th>Quantity</th><th>Price</th><th>Cash</th><th>Fees</th><th>Currency</th></tr></thead><tbody>{transactionRows.map((row, index) => <tr key={`${text(row.provider_transaction_id, "tx")}-${index}`}><td>{formatDate(row.occurred_at)}</td><td><strong>{text(row.provider)}</strong><small className="table-sub mono">{text(row.account_ref)}</small></td><td>{row.instrument_id ? <><strong>{shortId(row.instrument_id)}</strong><small className="table-sub mono">{text(row.instrument_id)}</small></> : "Cash activity"}</td><td>{text(row.kind)}</td><td>{text(row.side)}</td><td>{formatDecimal(row.quantity, 4)}</td><td>{formatDecimal(row.price, 4)}</td><td>{formatDecimal(row.cash_amount)}</td><td>{formatDecimal(row.fees)}</td><td>{text(row.currency)}</td></tr>)}</tbody></table></div>
        <div className="portfolio-mobile-cards">{transactionRows.map((row, index) => <article className="portfolio-activity-card" key={`mobile-${text(row.provider_transaction_id, "tx")}-${index}`}><header><strong>{row.instrument_id ? shortId(row.instrument_id) : "Cash activity"}</strong><Badge value={text(row.kind)} /></header><small>{formatDate(row.occurred_at)} · {text(row.provider)} · {text(row.account_ref)}</small><dl className="portfolio-position-metrics"><div><dt>Side / Quantity</dt><dd>{text(row.side)} · {formatDecimal(row.quantity, 4)}</dd></div><div><dt>Price</dt><dd>{formatDecimal(row.price, 4)}</dd></div><div><dt>Cash / Fees</dt><dd>{formatDecimal(row.cash_amount)} / {formatDecimal(row.fees)}</dd></div><div><dt>Currency</dt><dd>{text(row.currency)}</dd></div></dl></article>)}</div>
      </>}
      <WarningList value={transactions} />
    </Card>
    <Card kicker="COVERAGE · RECEIPTS" title="Activity coverage">
      <div className="coverage-overview"><div><span>Overall</span><strong><Badge value={text(coverageData?.overall_status, "UNKNOWN")} /></strong></div><div><span>Receipts</span><strong>{receipts.length}</strong></div><div><span>Unavailable providers</span><strong>{stringList(transactionData?.unavailable_providers).length}</strong></div></div>
      {receipts.length === 0 ? <Empty>No coverage receipts yet.</Empty> : <div className="coverage-list">{receipts.map((receipt, index) => <article key={`${text(receipt.receipt_id, "receipt")}-${index}`}><header><strong>{text(receipt.provider)} · {text(receipt.account_ref)}</strong><Badge value={text(receipt.status, "UNKNOWN")} /></header><div className="coverage-grid"><span>Window<strong>{formatDate(receipt.effective_start)} → {formatDate(receipt.effective_end)}</strong></span><span>Events / snapshots<strong>{text(receipt.event_count, "0")} / {text(receipt.snapshot_count, "0")}</strong></span><span>Inserted / duplicates<strong>{text(receipt.inserted_count, "0")} / {text(receipt.duplicate_count, "0")}</strong></span><span>Missing categories<strong>{stringList(receipt.unavailable_kinds).join(" · ") || "None"}</strong></span></div><small className="table-sub">gap: {stringList(receipt.gap_codes).join(" · ") || "None"} · fetched {formatDate(receipt.fetched_at)}</small></article>)}</div>}
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
      if (resultEnvelope.ok === false) throw new Error(errorMessage(resultEnvelope, "Performance attribution failed"));
      setResult(resultEnvelope);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Performance attribution failed"); }
    finally { setLoading(false); }
  }
  const performance = data<Dict>(result);
  const accounts = listOf<Dict>(performance, "accounts");
  return <Card kicker="PERFORMANCE · DURABLE ATTRIBUTION" title="Performance ledger" action={result ? <Badge value={text(performance?.status, "LOADED")} /> : undefined}>
    <p className="card-note">Rebuilds FIFO in native currency or uses broker-reported figures; no implicit FX conversion, and cumulative P/L is not presented as period returns.</p>
    <div className="performance-controls"><Field label="Start date (UTC)"><input type="date" value={start} onChange={(event) => setStart(event.target.value)} /></Field><Field label="End date (UTC)"><input type="date" value={end} onChange={(event) => setEnd(event.target.value)} /></Field><Field label="Cost basis"><select value={method} onChange={(event) => setMethod(event.target.value as "FIFO" | "BROKER_REPORTED")}><option value="FIFO">FIFO event reconstruction</option><option value="BROKER_REPORTED">Broker-reported</option></select></Field><ActionButton onClick={() => { void calculate(); }} busy={loading}>Calculate attribution</ActionButton></div>
    {error && <div className="inline-error" role="alert">{error}</div>}
    <WarningList value={result} />
    {performance && accounts.length === 0 ? <Empty>No durable account facts can be attributed in the selected period.</Empty> : <div className="performance-results">{accounts.map((account, index) => { const instruments = listOf<Dict>(account, "instruments"); return <article className="performance-account" key={`${text(account.account_ref)}-${index}`}><header><div><strong>{accountLabel(account, index)}</strong><span>{text(account.currency)} · {text(account.cost_basis_method)}</span></div><Badge value={text(account.status, "UNKNOWN")} /></header><div className="account-summary"><article><span>Realized P/L (after fees)</span><strong>{formatDecimal(account.realized_pnl_after_fees)}</strong><small>Before fees {formatDecimal(account.realized_pnl_before_fees)}</small></article><article><span>Unrealized P/L</span><strong>{formatDecimal(account.unrealized_pnl_before_fees)}</strong><small>Valuation snapshot {formatDate(account.snapshot_as_of)}</small></article><article><span>Dividends / interest</span><strong>{formatDecimal(account.dividends)} / {formatDecimal(account.interest)}</strong><small>Known fees {formatDecimal(account.known_fees)}</small></article><article><span>Net external cash flow</span><strong>{formatDecimal(account.net_external_cash_flow)}</strong><small>{instruments.length} instrument facts</small></article></div><details><summary>Drill into instruments & events</summary><div className="table-wrap"><table><thead><tr><th>Instrument</th><th>Realized before fees</th><th>Realized after fees</th><th>Unrealized</th><th>Ending quantity</th><th>Events</th><th>Warning</th></tr></thead><tbody>{instruments.map((instrument, instrumentIndex) => <tr key={`${text(instrument.instrument_id)}-${instrumentIndex}`}><td><strong>{shortId(instrument.instrument_id)}</strong><small className="table-sub mono">{text(instrument.instrument_id)}</small></td><td>{formatDecimal(instrument.realized_pnl_before_fees)}</td><td>{formatDecimal(instrument.realized_pnl_after_fees)}</td><td>{formatDecimal(instrument.unrealized_pnl_before_fees)}</td><td>{formatDecimal(instrument.ending_quantity, 4)}</td><td>{Array.isArray(instrument.activity_ids) ? instrument.activity_ids.length : 0}</td><td>{stringList(instrument.warning_codes).join(" · ") || "—"}</td></tr>)}</tbody></table></div></details></article>; })}</div>}
  </Card>;
}

const POLICY_FIELDS: Array<{ key: string; label: string; step?: string }> = [
  { key: "single_position_max_percent", label: "Single-position cap %" },
  { key: "gross_exposure_max_percent", label: "Gross exposure cap %" },
  { key: "minimum_cash_percent", label: "Minimum cash %" },
  { key: "margin_usage_max_percent", label: "Margin usage cap %" },
  { key: "max_account_age_seconds", label: "Max account age (seconds)" },
  { key: "max_price_age_seconds", label: "Max price age (seconds)" },
  { key: "risk_budget_max_percent", label: "Risk budget cap %" },
  { key: "theme_exposure_max_percent", label: "Theme exposure cap %" },
  { key: "drawdown_max_percent", label: "Max drawdown %" },
  { key: "liquidity_participation_max_percent", label: "Liquidity participation cap %" },
  { key: "correlation_max_absolute", label: "Max absolute correlation", step: "0.01" },
  { key: "event_blackout_days", label: "Event blackout days" },
];

function PolicyForm({ policy, onSaved }: { policy: Dict; onSaved: () => void }) {
  const initial = Object.fromEntries(POLICY_FIELDS.map(({ key }) => [key, text(policy[key], "0")])) as Record<string, string>;
  const [values, setValues] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    if (!window.confirm("Append a new risk policy version? Existing versions will not be overwritten, and no orders will be placed.")) return;
    setBusy(true); setError(null);
    try {
      const request: Dict = { ...Object.fromEntries(POLICY_FIELDS.map(({ key }) => [key, Number(values[key])])), expected_version: Number(policy.version), confirmed_by: "user", idempotency_key: idempotencyKey("risk-policy") };
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: "risk_policy_update", arguments: request, confirmation: "risk_policy_update" });
      if (envelope(invocationResult(response)).ok === false) throw new Error(errorMessage(response, "Risk Policy update failed"));
      onSaved();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Risk Policy update failed"); }
    finally { setBusy(false); }
  }
  return <details className="portfolio-policy-editor"><summary>Create Policy version (expected_version={text(policy.version)})</summary><div className="portfolio-policy-form">{POLICY_FIELDS.map(({ key, label, step }) => <Field label={label} key={key}><input type="number" step={step ?? "0.01"} value={values[key]} onChange={(event) => setValues((current) => ({ ...current, [key]: event.target.value }))} /></Field>)}</div>{error && <div className="inline-error">{error}</div>}<div className="portfolio-form-actions"><ActionButton onClick={() => { void save(); }} busy={busy}>Confirm append</ActionButton><small className="muted">User confirmation, idempotency key, and expected_version are recorded in the audit trail.</small></div></details>;
}

function RiskChecks({ result }: { result: Dict | null }) {
  const payload = data<Dict>(result);
  const checks = listOf<Dict>(payload, "checks");
  if (!result) return <Empty>Risk check has not run yet.</Empty>;
  return <div className="risk-result"><header><strong>Risk check · {text(payload?.overall_status, "UNKNOWN")}</strong><Badge value={payload?.execution_effect === true ? "EXECUTION_EFFECT" : "READ_ONLY"} /></header><div className="table-wrap"><table><thead><tr><th>Rule</th><th>Status</th><th>Actual</th><th>Limit</th><th>Scope</th><th>Explanation</th></tr></thead><tbody>{checks.map((check, index) => <tr key={`${text(check.rule_code)}-${index}`}><td>{text(check.rule_code)}</td><td><Badge value={text(check.status)} /></td><td>{formatDecimal(check.actual)}</td><td>{formatDecimal(check.limit)}</td><td>{text(check.scope)}</td><td>{text(check.message)}</td></tr>)}</tbody></table></div>{payload?.hypothetical ? <div className="portfolio-hypothetical"><span>Hypothetical addition</span><strong>{shortId(asDict(payload.hypothetical).instrument_id)} · {formatDecimal(asDict(payload.hypothetical).quantity, 4)} · {formatDecimal(asDict(payload.hypothetical).assumed_price)} {text(asDict(payload.hypothetical).currency)}</strong></div> : null}{payload?.position_sizing ? <details><summary>Position Sizing / constraints</summary><pre className="portfolio-json">{displayJson(payload.position_sizing)}</pre></details> : null}<WarningList value={result} /></div>;
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
      if (envelope(result).ok === false) throw new Error(errorMessage(result, "Risk check failed"));
      setLocalRiskEnvelope(result);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Risk check failed"); }
    finally { setBusy(false); }
  }
  // The parent owns the aggregate refresh. Risk check is intentionally local to this tab;
  // a custom event lets the page retain the durable aggregate while replacing this result.
  return <div className="portfolio-tab-stack">
    <Card kicker="RISK POLICY · APPEND ONLY" title="Current risk policy" action={policy ? <Badge value={policy.is_system_default ? "SYSTEM_DEFAULT" : `VERSION ${text(policy.version)}`} /> : undefined}>
      {!policy ? <Empty>Risk Policy unavailable.</Empty> : <><div className="policy-grid">{POLICY_FIELDS.map(({ key, label }) => <div key={key}><span>{label}</span><strong>{text(policy[key])}</strong></div>)}</div><p className="card-note">Confirmed by: {text(policy.confirmed_by)} · Created {formatDate(policy.created_at)} · schema {text(policy.schema_version)}</p><PolicyForm policy={policy} onSaved={onRefresh} /></>}
    </Card>
    <Card kicker="RISK CHECK · READ ONLY" title="Current risk check">
      <p className="card-note">Read-only durable risk_check by default. Hypothetical additions are calculation-only; they do not change accounts, Policy, Trade Plan, or create orders.</p>
      <RiskChecks result={localRiskEnvelope} />
      <details className="portfolio-what-if"><summary>Run What-if</summary><div className="portfolio-segmented" role="tablist"><button type="button" className={whatIfMode === "manual" ? "selected" : ""} onClick={() => setWhatIfMode("manual")}>Manual hypothetical</button><button type="button" className={whatIfMode === "trade_plan" ? "selected" : ""} onClick={() => setWhatIfMode("trade_plan")}>Trade Plan</button></div>{whatIfMode === "manual" ? <div className="portfolio-form-grid"><Field label="Instrument ID"><input value={instrument} onChange={(event) => setInstrument(event.target.value)} placeholder="equity:US:NVDA" /></Field><Field label="Quantity"><input type="number" min="0" step="any" value={quantity} onChange={(event) => setQuantity(event.target.value)} /></Field><Field label="Assumed price"><input type="number" min="0" step="any" value={price} onChange={(event) => setPrice(event.target.value)} /></Field><Field label="Native currency"><input value={currency} onChange={(event) => setCurrency(event.target.value)} /></Field></div> : <Field label="Trade Plan ID"><input value={tradePlan} onChange={(event) => setTradePlan(event.target.value)} placeholder="trade_plan_<uuid7>" /></Field>}<div className="portfolio-form-actions"><ActionButton onClick={() => { void check(); }} busy={busy}>Run read-only check</ActionButton></div>{error && <div className="inline-error">{error}</div>}</details>
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
  function moveTab(event: KeyboardEvent<HTMLButtonElement>, current: Tab) {
    const index = TABS.findIndex((item) => item.id === current);
    let next: number;
    if (event.key === "ArrowRight") next = (index + 1) % TABS.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + TABS.length) % TABS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = TABS.length - 1;
    else return;
    event.preventDefault();
    selectTab(TABS[next].id);
    document.getElementById(`portfolio-tab-${TABS[next].id}`)?.focus();
  }
  function changePositionSort(tableId: string, column: PositionSortKey) {
    setPositionSorts((current) => { const previous = current[tableId] ?? DEFAULT_POSITION_SORT; return { ...current, [tableId]: { key: column, direction: previous.key === column && previous.direction === "asc" ? "desc" : "asc" } }; });
  }
  async function refreshAggregate() { result.refresh(); }
  async function sync(operation: "accounts" | "transactions") {
    const labels = { accounts: "accounts", transactions: "transactions" };
    if (!window.confirm(`Connect to the configured upstream and sync ${labels[operation]}?`)) return;
    setSyncing(operation); setMessage(null);
    try {
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: "external_state_sync", arguments: { request: { operation } }, confirmation: "external_state_sync" });
      const resultEnvelope = envelope(invocationResult(response)); if (resultEnvelope.ok === false) throw new Error(errorMessage(resultEnvelope, "Sync failed"));
      setMessage({ text: `${labels[operation]} synced successfully; refreshing durable state.` }); result.refresh();
    } catch (cause) { setMessage({ text: cause instanceof Error ? cause.message : "Sync failed", error: true }); }
    finally { setSyncing(null); }
  }
  return <ConsoleShell active="portfolio" eyebrow="Durable portfolio hub" title="Portfolio">
    <DataBoundary loading={result.loading} error={result.error}>
      <div className="toolbar portfolio-toolbar"><p>Page load reads only durable account snapshots, activity, and Risk; it never refreshes a Provider implicitly. Account and transaction syncs both require explicit confirmation.</p><div className="toolbar-actions"><ActionButton onClick={() => { void sync("accounts"); }} busy={syncing === "accounts"}>Sync accounts</ActionButton><ActionButton onClick={() => { void sync("transactions"); }} busy={syncing === "transactions"}>Sync transactions</ActionButton><RefreshButton onClick={refreshAggregate} loading={result.loading} /></div></div>
      {message && <div className={message.error ? "inline-error" : "inline-success"} role="status">{message.text}</div>}
      <nav className="portfolio-tabs" aria-label="Portfolio sections" role="tablist">{TABS.map((item) => <button id={`portfolio-tab-${item.id}`} type="button" role="tab" tabIndex={tab === item.id ? 0 : -1} aria-selected={tab === item.id} aria-controls={`portfolio-panel-${item.id}`} className={tab === item.id ? "selected" : ""} onKeyDown={(event) => moveTab(event, item.id)} onClick={() => selectTab(item.id)} key={item.id}>{item.label}</button>)}</nav>
      <div className="portfolio-tab-content" id={`portfolio-panel-${tab}`} role="tabpanel" tabIndex={0} aria-labelledby={`portfolio-tab-${tab}`}>
        {tab === "holdings" && <HoldingsTab accounts={accounts} exposure={exposureEnvelope} positionSorts={positionSorts} onSort={changePositionSort} />}
        {tab === "activity" && <ActivityTab transactions={transactionsEnvelope} coverage={coverageEnvelope} />}
        {tab === "performance" && <PerformanceTab />}
        {tab === "risk" && <RiskTab policyEnvelope={riskPolicyEnvelope} riskEnvelope={riskCheckEnvelope} onRefresh={result.refresh} />}
      </div>
    </DataBoundary>
  </ConsoleShell>;
}
