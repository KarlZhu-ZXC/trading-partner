import type { ReactNode } from "react";

export function Card({
  title,
  kicker,
  action,
  id,
  className = "",
  children,
}: {
  title?: string;
  kicker?: string;
  action?: ReactNode;
  id?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`card ${className}`} id={id}>
      {(title || kicker || action) && (
        <header className="card-head">
          <div>
            {kicker && <p className="card-kicker">{kicker}</p>}
            {title && <h2>{title}</h2>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function Badge({ value }: { value: string | null | undefined }) {
  const label = value ?? "UNKNOWN";
  const tone = ["OK", "HEALTHY", "SUCCEEDED", "ACTIVE", "QUIET", "FRESH", "RECOVERED", "RESOLVE", "SUPPORTED"].includes(
    label.toUpperCase(),
  )
    ? "good"
    : ["TRIGGERED", "EXPIRING", "DEGRADED", "WARNING", "ACKNOWLEDGE", "NOT_EVALUATED", "UNSUPPORTED", "MEDIUM"].includes(label.toUpperCase())
      ? "warn"
      : ["FAILED", "ERROR", "DEAD_LETTER", "HIGH"].includes(label.toUpperCase())
        ? "bad"
        : "neutral";
  return <span className={`badge ${tone}`}>{label}</span>;
}

export function RefreshButton({
  onClick,
  loading,
}: {
  onClick: () => void;
  loading: boolean;
}) {
  return (
    <button className="refresh-button" onClick={onClick} disabled={loading} type="button">
      {loading ? "Loading" : "Refresh"}
    </button>
  );
}

export function ActionButton({
  children,
  onClick,
  busy = false,
  busyLabel = "Working…",
  tone = "default",
}: {
  children: ReactNode;
  onClick: () => void;
  busy?: boolean;
  busyLabel?: string;
  tone?: "default" | "warning";
}) {
  return (
    <button
      className={`action-button ${tone}`}
      onClick={onClick}
      disabled={busy}
      type="button"
    >
      {busy ? busyLabel : children}
    </button>
  );
}

export function displayJson(value: unknown): string {
  return JSON.stringify(
    value,
    (key, item: unknown) =>
      key === "data" && typeof item === "string" && item.length > 2000
        ? `[binary/base64 omitted · ${item.length} chars]`
        : item,
    2,
  );
}

export function DataBoundary({
  loading,
  error,
  children,
}: {
  loading: boolean;
  error: string | null;
  children: ReactNode;
}) {
  if (loading) return <div className="state-panel">Loading local facts…</div>;
  if (error)
    return (
      <div className="state-panel error-state">
        <strong>Local API disconnected</strong>
        <span>Run uv run trading-partner-console first ({error})</span>
      </div>
    );
  return <>{children}</>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty-state">{children}</div>;
}

export function formatDate(value: unknown): string {
  if (!value || typeof value !== "string") return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatBytes(value: unknown): string {
  const bytes = Number(value ?? 0);
  if (!Number.isFinite(bytes)) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

export function formatDecimal(value: unknown, maximumFractionDigits = 2): string {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return new Intl.NumberFormat("zh-CN", {
    minimumFractionDigits: 0,
    maximumFractionDigits,
  }).format(numeric);
}

export function shortId(value: unknown): string {
  if (typeof value !== "string") return "—";
  const parts = value.split(":");
  if (parts.length > 1) return parts.at(-1) ?? value;
  return value.length > 18 ? `${value.slice(0, 12)}…` : value;
}

export function monitorAnchorId(value: unknown): string {
  return `monitor-${String(value ?? "unknown")}`;
}
