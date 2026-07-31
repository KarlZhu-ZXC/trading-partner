"use client";

import { useState } from "react";
import { ConsoleShell } from "../components/console-shell";
import { ActionButton, Badge, Card, DataBoundary, Empty, RefreshButton, displayJson, formatDate, formatDecimal, shortId } from "../components/ui";
import { envelopeData, listOf, postApi, useApi } from "../lib/api";

type Dict = Record<string, unknown>;
type PositionSortKey = "instrument_id" | "market_price" | "side" | "quantity" | "average_cost" | "market_value" | "unrealized_pnl" | "currency";
type PositionSort = { key: PositionSortKey | null; direction: "asc" | "desc" };

const DEFAULT_POSITION_SORT: PositionSort = { key: null, direction: "asc" };
const NUMERIC_POSITION_KEYS = new Set<PositionSortKey>(["market_price", "quantity", "average_cost", "market_value", "unrealized_pnl"]);

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
  const provider = String(account.provider ?? "ACCOUNT").toUpperCase();
  const reference = String(account.account_ref ?? "");
  const suffix = reference.slice(-6);
  return `${provider} 账户 ${index + 1}${suffix ? ` · ${suffix}` : ""}`;
}

function positionSummaries(positions: Dict[]): Array<{ currency: string; marketValue: number; unrealizedPnl: number; count: number }> {
  const summaries = new Map<string, { currency: string; marketValue: number; unrealizedPnl: number; count: number }>();
  for (const position of positions) {
    const currency = String(position.currency ?? "UNKNOWN");
    const current = summaries.get(currency) ?? { currency, marketValue: 0, unrealizedPnl: 0, count: 0 };
    current.marketValue += Number(position.market_value ?? 0);
    current.unrealizedPnl += Number(position.unrealized_pnl ?? 0);
    current.count += 1;
    summaries.set(currency, current);
  }
  return [...summaries.values()];
}

function dateInput(value: Date): string {
  return value.toISOString().slice(0, 10);
}

function yearStart(): string {
  const now = new Date();
  return `${now.getUTCFullYear()}-01-01`;
}

export default function PortfolioPage() {
  const accountsResult = useApi<Dict>("/api/accounts");
  const [syncing, setSyncing] = useState<string | null>(null);
  const [syncResult, setSyncResult] = useState<unknown>(null);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [positionSorts, setPositionSorts] = useState<Record<string, PositionSort>>({});
  const [performanceStart, setPerformanceStart] = useState(yearStart);
  const [performanceEnd, setPerformanceEnd] = useState(() => dateInput(new Date()));
  const [costBasisMethod, setCostBasisMethod] = useState<"FIFO" | "BROKER_REPORTED">("FIFO");
  const [performanceResult, setPerformanceResult] = useState<Dict | null>(null);
  const [performanceLoading, setPerformanceLoading] = useState(false);
  const [performanceError, setPerformanceError] = useState<string | null>(null);
  const accounts = listOf<Dict>(envelopeData<Dict>(accountsResult.data), "accounts");
  const loading = accountsResult.loading;
  const error = accountsResult.error;

  function changePositionSort(tableId: string, column: PositionSortKey) {
    setPositionSorts((current) => {
      const previous = current[tableId] ?? DEFAULT_POSITION_SORT;
      return {
        ...current,
        [tableId]: {
          key: column,
          direction: previous.key === column && previous.direction === "asc" ? "desc" : "asc",
        },
      };
    });
  }

  async function sync(operation: "accounts" | "transactions") {
    const label = operation === "transactions" ? "交易记录" : "账户";
    if (!window.confirm(`确认连接已配置的上游并同步${label}？`)) return;
    setSyncing(operation);
    setSyncError(null);
    try {
      const value = await postApi<unknown>("/api/tools/invoke", {
        tool_name: "external_state_sync",
        arguments: { request: { operation } },
        confirmation: "external_state_sync",
      });
      setSyncResult(value);
      accountsResult.refresh();
    } catch (cause) {
      setSyncError(cause instanceof Error ? cause.message : "同步失败");
    } finally {
      setSyncing(null);
    }
  }

  async function calculatePerformance() {
    setPerformanceLoading(true);
    setPerformanceError(null);
    try {
      const value = await postApi<Dict>("/api/tools/invoke", {
        tool_name: "portfolio_analyze",
        arguments: {
          request: {
            operation: "performance_summary",
            start: `${performanceStart}T00:00:00Z`,
            end: `${performanceEnd}T23:59:59.999999Z`,
            cost_basis_method: costBasisMethod,
          },
        },
      });
      setPerformanceResult(value);
    } catch (cause) {
      setPerformanceError(cause instanceof Error ? cause.message : "业绩归因失败");
    } finally {
      setPerformanceLoading(false);
    }
  }

  const performance = envelopeData<Dict>(performanceResult);
  const performanceAccounts = listOf<Dict>(performance, "accounts");
  const performanceWarnings = Array.isArray(performanceResult?.warnings) ? performanceResult.warnings as Dict[] : [];

  return (
    <ConsoleShell active="portfolio" eyebrow="Durable state only" title="账户">
      <DataBoundary loading={loading} error={error}>
        <div className="toolbar"><p>页面加载仍只读持久化账户快照；只有点击下方同步按钮才会连接已配置的美股账户。A 股 QMT 不在当前同步范围。</p><div className="toolbar-actions"><ActionButton onClick={() => sync("accounts")} busy={syncing === "accounts"}>同步账户</ActionButton><ActionButton onClick={() => sync("transactions")} busy={syncing === "transactions"}>同步交易</ActionButton><RefreshButton onClick={accountsResult.refresh} loading={loading} /></div></div>
        {syncError && <div className="inline-error">{syncError}</div>}
        {syncResult !== null && <details className="run-receipt"><summary>查看最近同步回执</summary><pre>{displayJson(syncResult)}</pre></details>}
        <Card kicker="A1 · DURABLE ATTRIBUTION" title="实际损益账本" action={performance ? <Badge value={String(performance.status ?? "UNKNOWN")} /> : undefined}>
          <p className="card-note">只使用已持久化的活动与账户快照，按账户和原币种计算。不会联网刷新、隐式换汇或把券商累计 P/L 冒充区间收益。</p>
          <div className="performance-controls">
            <label><span>开始日期（UTC）</span><input type="date" value={performanceStart} onChange={(event) => setPerformanceStart(event.target.value)} /></label>
            <label><span>结束日期（UTC）</span><input type="date" value={performanceEnd} onChange={(event) => setPerformanceEnd(event.target.value)} /></label>
            <label><span>成本口径</span><select value={costBasisMethod} onChange={(event) => setCostBasisMethod(event.target.value as "FIFO" | "BROKER_REPORTED")}><option value="FIFO">FIFO 事件重建</option><option value="BROKER_REPORTED">券商快照口径</option></select></label>
            <ActionButton onClick={calculatePerformance} busy={performanceLoading}>计算归因</ActionButton>
          </div>
          {performanceError && <div className="inline-error">{performanceError}</div>}
          {performanceWarnings.length > 0 && <details className="run-receipt"><summary>为什么结果不完整（{performanceWarnings.length}）</summary><ul className="warning-list">{performanceWarnings.map((warning) => <li key={String(warning.code)}><strong>{String(warning.code)}</strong><span>{String(warning.message ?? "")}</span></li>)}</ul></details>}
          {performance && performanceAccounts.length === 0 && <Empty>当前区间没有可归因的持久化账户事实。</Empty>}
          {performanceAccounts.length > 0 && <div className="performance-results">{performanceAccounts.map((account, index) => {
            const instruments = listOf<Dict>(account, "instruments");
            return <article className="performance-account" key={`${String(account.account_ref)}-${String(account.currency)}`}><header><div><strong>{accountLabel(account, index)}</strong><span>{String(account.currency ?? "—")} · {String(account.cost_basis_method ?? "—")}</span></div><Badge value={String(account.status ?? "UNKNOWN")} /></header><div className="account-summary"><article><span>已实现 P/L（费后）</span><strong>{formatDecimal(account.realized_pnl_after_fees)}</strong><small>费前 {formatDecimal(account.realized_pnl_before_fees)}</small></article><article><span>未实现 P/L</span><strong>{formatDecimal(account.unrealized_pnl_before_fees)}</strong><small>估值快照 {formatDate(account.snapshot_as_of)}</small></article><article><span>股息 / 利息</span><strong>{formatDecimal(account.dividends)} / {formatDecimal(account.interest)}</strong><small>已知费用 {formatDecimal(account.known_fees)}</small></article><article><span>外部净现金流</span><strong>{formatDecimal(account.net_external_cash_flow)}</strong><small>{instruments.length} 个标的事实</small></article></div><details><summary>下钻标的与事件</summary><div className="table-wrap"><table><thead><tr><th>标的</th><th>已实现费前</th><th>已实现费后</th><th>未实现</th><th>期末数量</th><th>事件</th><th>Warning</th></tr></thead><tbody>{instruments.map((instrument) => <tr key={String(instrument.instrument_id)}><td><strong>{shortId(instrument.instrument_id)}</strong><small className="table-sub mono">{String(instrument.instrument_id)}</small></td><td>{formatDecimal(instrument.realized_pnl_before_fees)}</td><td>{formatDecimal(instrument.realized_pnl_after_fees)}</td><td>{formatDecimal(instrument.unrealized_pnl_before_fees)}</td><td>{formatDecimal(instrument.ending_quantity, 4)}</td><td>{Array.isArray(instrument.activity_ids) ? instrument.activity_ids.length : 0}</td><td>{Array.isArray(instrument.warning_codes) ? instrument.warning_codes.join(", ") || "—" : "—"}</td></tr>)}</tbody></table></div></details></article>;
          })}</div>}
        </Card>
        <div className="stack">
          {accounts.length === 0 ? <Empty>没有持久化账户快照。</Empty> : accounts.map((account, accountIndex) => {
            const positions = listOf<Dict>(account, "positions");
            const summaries = positionSummaries(positions);
            const tableId = String(account.snapshot_id ?? `account-${accountIndex}`);
            const positionSort = positionSorts[tableId] ?? DEFAULT_POSITION_SORT;
            const visiblePositions = sortedPositions(positions, positionSort);
            return (
              <Card key={String(account.snapshot_id)} kicker={String(account.provider ?? "ACCOUNT").toUpperCase()} title={accountLabel(account, accountIndex)} action={<span className="muted">快照 {formatDate(account.account_as_of)}</span>}>
                <p className="account-reference mono">{String(account.account_ref ?? "—")}</p>
                <div className="account-summary" aria-label="按原币种汇总的持仓事实">
                  {summaries.map((summary) => <article key={summary.currency}><span>{summary.currency} 持仓市值</span><strong>{formatDecimal(summary.marketValue)}</strong><small className={summary.unrealizedPnl > 0 ? "text-green" : summary.unrealizedPnl < 0 ? "text-red" : ""}>未实现 P/L {formatDecimal(summary.unrealizedPnl)} · {summary.count} 项</small></article>)}
                </div>
                <div className="table-wrap">
                  <table><thead><tr>
                    <SortableHeader label="标的" column="instrument_id" sort={positionSort} onSort={(column) => changePositionSort(tableId, column)} />
                    <SortableHeader label="快照价格" column="market_price" sort={positionSort} onSort={(column) => changePositionSort(tableId, column)} />
                    <SortableHeader label="方向" column="side" sort={positionSort} onSort={(column) => changePositionSort(tableId, column)} />
                    <SortableHeader label="数量" column="quantity" sort={positionSort} onSort={(column) => changePositionSort(tableId, column)} />
                    <SortableHeader label="成本" column="average_cost" sort={positionSort} onSort={(column) => changePositionSort(tableId, column)} />
                    <SortableHeader label="市值" column="market_value" sort={positionSort} onSort={(column) => changePositionSort(tableId, column)} />
                    <SortableHeader label="未实现 P/L" column="unrealized_pnl" sort={positionSort} onSort={(column) => changePositionSort(tableId, column)} />
                    <SortableHeader label="币种" column="currency" sort={positionSort} onSort={(column) => changePositionSort(tableId, column)} />
                  </tr></thead>
                    <tbody>{visiblePositions.map((position) => {
                      const pnl = Number(position.unrealized_pnl ?? 0);
                      return <tr key={String(position.instrument_id)}><td><strong>{shortId(position.instrument_id)}</strong><small className="table-sub mono">{String(position.instrument_id)}</small></td><td>{position.market_price === null || position.market_price === undefined ? <><span>—</span><small className="table-sub">未提供带时间价格</small></> : <><strong>{formatDecimal(position.market_price, 4)}</strong><small className="table-sub">快照 {formatDate(position.market_price_at)}</small></>}</td><td>{String(position.side ?? "—")}</td><td>{formatDecimal(position.quantity, 4)}</td><td>{formatDecimal(position.average_cost, 4)}</td><td>{formatDecimal(position.market_value)}</td><td className={pnl > 0 ? "text-green" : pnl < 0 ? "text-red" : ""}>{formatDecimal(position.unrealized_pnl)}</td><td>{String(position.currency ?? "—")}</td></tr>;
                    })}</tbody>
                  </table>
                </div>
              </Card>
            );
          })}
        </div>
      </DataBoundary>
    </ConsoleShell>
  );
}
