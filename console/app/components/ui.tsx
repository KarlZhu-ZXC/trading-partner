import type { KeyboardEvent, ReactNode } from "react";

export function RequiredMark() {
  return <b className="required-mark" aria-hidden="true">*</b>;
}

export function FieldLabel({ children, required = false }: { children: ReactNode; required?: boolean }) {
  return <span>{required && <RequiredMark />}{children}</span>;
}

export type HorizontalTabItem<T extends string> = {
  id: T;
  label: ReactNode;
  suffix?: ReactNode;
  attention?: boolean;
};

export function HorizontalTabs<T extends string>({
  items,
  value,
  onChange,
  ariaLabel,
  idPrefix,
  panelIdPrefix,
  className = "",
}: {
  items: ReadonlyArray<HorizontalTabItem<T>>;
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
  idPrefix: string;
  panelIdPrefix: string;
  className?: string;
}) {
  function move(event: KeyboardEvent<HTMLButtonElement>, current: T) {
    const index = items.findIndex((item) => item.id === current);
    let nextIndex: number;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % items.length;
    else if (event.key === "ArrowLeft") nextIndex = (index - 1 + items.length) % items.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = items.length - 1;
    else return;
    event.preventDefault();
    const next = items[nextIndex];
    onChange(next.id);
    document.getElementById(`${idPrefix}-${next.id}`)?.focus();
  }

  return <nav className={`horizontal-tabs ${className}`.trim()} aria-label={ariaLabel} role="tablist">{items.map((item) => <button id={`${idPrefix}-${item.id}`} key={item.id} className={`${value === item.id ? "selected" : ""}${item.attention ? " attention" : ""}`} type="button" role="tab" aria-selected={value === item.id} aria-controls={`${panelIdPrefix}-${item.id}`} tabIndex={value === item.id ? 0 : -1} onKeyDown={(event) => move(event, item.id)} onClick={() => onChange(item.id)}><span>{item.label}</span>{item.suffix}</button>)}</nav>;
}

export function Card({
  title,
  subtitle,
  description,
  kicker,
  action,
  id,
  className = "",
  children,
}: {
  title?: string;
  subtitle?: string;
  description?: string;
  kicker?: string;
  action?: ReactNode;
  id?: string;
  className?: string;
  children: ReactNode;
}) {
  const bodyDescription = kicker ? (description ?? subtitle) : description;
  return (
    <section className={`card ${className}`} id={id}>
      {(title || subtitle || kicker || action) && (
        <header className="card-head">
          <div className="card-heading-copy">
            {kicker && <p className="card-kicker">{kicker}</p>}
            {title && <h2>{title}</h2>}
            {!kicker && subtitle && <p className="card-subtitle">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      {bodyDescription && <p className="card-description">{bodyDescription}</p>}
      {children}
    </section>
  );
}

export function DescriptionList({
  items,
  columns = 4,
  className = "",
}: {
  items: Array<{ label: string; value: ReactNode; detail?: ReactNode }>;
  columns?: 2 | 3 | 4 | 6;
  className?: string;
}) {
  return <dl className={`description-list columns-${columns} ${className}`}>{items.map((item) => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd>{item.detail != null && <small>{item.detail}</small>}</div>)}</dl>;
}

export function Badge({ value }: { value: string | null | undefined }) {
  const label = value ?? "UNKNOWN";
  const tone = ["OK", "HEALTHY", "SUCCEEDED", "ACTIVE", "AVAILABLE", "DURABLE", "QUIET", "FRESH", "RECOVERED", "RESOLVE", "SUPPORTED"].includes(
    label.toUpperCase(),
  )
    ? "good"
    : ["TRIGGERED", "EXPIRING", "DEGRADED", "LIMITED", "WARNING", "ACKNOWLEDGE", "REVIEW", "NOT_EVALUATED", "UNSUPPORTED", "MEDIUM"].includes(label.toUpperCase())
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
  disabled = false,
  busyLabel = "Working…",
  tone = "default",
}: {
  children: ReactNode;
  onClick: () => void;
  busy?: boolean;
  disabled?: boolean;
  busyLabel?: string;
  tone?: "default" | "warning";
}) {
  return (
    <button
      className={`action-button ${tone}`}
      onClick={onClick}
      disabled={busy || disabled}
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
        <strong>Local API Disconnected</strong>
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
