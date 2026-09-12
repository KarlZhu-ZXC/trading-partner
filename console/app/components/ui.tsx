import { Button, LinkButton, Input, Textarea, IconButton, MenuItem, SortButton } from "./ui/controls";
import patternStyles from "./ui/patterns.module.css";
export { Button, LinkButton, IconButton, TextLink, Input, Select, Textarea, DateRange, FilterBar, FilterChip, Tag, Table, SelectableRow } from "./ui/controls";
import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import { ArrowDown, ArrowUp, ChevronDown, ChevronsUpDown, EllipsisVertical } from "lucide-react";

export type PageActionItem = {
  id: string;
  label: ReactNode;
  description?: ReactNode;
  icon?: ReactNode;
  disabled?: boolean;
  onSelect: () => void;
};

export function QuickLink({
  href,
  children,
  className = "",
}: {
  href: string;
  children: ReactNode;
  className?: string;
}) {
  return <LinkButton className={`quick-link ${className}`.trim()} href={href}>{children}</LinkButton>;
}

export function SortableTableHeader<Key extends string>({
  label,
  column,
  activeColumn,
  direction,
  onSort,
}: {
  label: string;
  column: Key;
  activeColumn: Key | null;
  direction: "asc" | "desc";
  onSort: (column: Key) => void;
}) {
  const active = activeColumn === column;
  return <th aria-sort={active ? (direction === "asc" ? "ascending" : "descending") : "none"}>
    <SortButton active={active} className="sort-header" type="button" onClick={() => onSort(column)}>
      <span className="sort-label">{label}</span>
      <span className={`sort-indicator${active ? " active" : ""}`} aria-hidden="true">
        {active ? (direction === "asc" ? <ArrowUp /> : <ArrowDown />) : <ChevronsUpDown />}
      </span>
    </SortButton>
  </th>;
}

export function Disclosure({
  title,
  description,
  meta,
  children,
  defaultOpen = false,
  variant = "panel",
  className = "",
  onToggle,
}: {
  title: ReactNode;
  description?: ReactNode;
  meta?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  variant?: "panel" | "compact" | "code";
  className?: string;
  onToggle?: (open: boolean) => void;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return <details className={`${patternStyles.disclosure} disclosure disclosure-${variant} ${className}`.trim()} open={open} onToggle={(event) => { const next = event.currentTarget.open; setOpen(next); onToggle?.(next); }}>
    <summary><span className="disclosure-heading"><strong>{title}</strong>{description != null ? <small>{description}</small> : null}</span><span className="disclosure-meta">{meta}<ChevronDown aria-hidden="true" /></span></summary>
    <div className="disclosure-body">{children}</div>
  </details>;
}

/** Compact page-level action collector. View controls stay in the page body;
 * infrequent create/sync/refresh actions live in this shared Header menu. */
export function PageActionMenu({
  ariaLabel,
  items,
}: {
  ariaLabel: string;
  items: ReadonlyArray<PageActionItem>;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function closeOnOutsidePointer(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function closeOnEscape(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    window.addEventListener("pointerdown", closeOnOutsidePointer);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("pointerdown", closeOnOutsidePointer);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return <div className="page-action-menu" ref={rootRef}>
    <IconButton className="page-action-trigger" type="button" aria-label={`Open ${ariaLabel}`} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
      <EllipsisVertical aria-hidden="true" />
    </IconButton>
    {open ? <div className="page-action-list" role="menu" aria-label={ariaLabel}>{items.map((item) => <MenuItem key={item.id} type="button" role="menuitem" disabled={item.disabled} onClick={() => { item.onSelect(); setOpen(false); }}>
      {item.icon ?? <span aria-hidden="true" />}
      <span><strong>{item.label}</strong>{item.description != null && <small>{item.description}</small>}</span>
    </MenuItem>)}</div> : null}
  </div>;
}

export function RequiredMark() {
  return <b className="required-mark" aria-hidden="true">*</b>;
}

export function FieldLabel({ children, required = false }: { children: ReactNode; required?: boolean }) {
  return <span>{required && <RequiredMark />}{children}</span>;
}

/** Labeled form control matching the Console label convention: the wrapper
 * is the accessibility label, the span carries the visible text plus the
 * required mark. */
export function FormField({
  label,
  required = false,
  children,
  className,
}: {
  label: ReactNode;
  required?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={`${patternStyles.field} ${className ?? ""}`}>
      <span>{required && <RequiredMark />}{label}</span>
      {children}
    </label>
  );
}

/** Renders nothing when the message is empty, so callers can pass state
 * directly instead of conditional JSX. */
export function ErrorNote({ children, role }: { children: ReactNode; role?: string }) {
  if (!children) return null;
  return <div className="inline-error" role={role}>{children}</div>;
}

/** Shared action row that preserves each owning form's layout class. */
export function FormActions({ children, className = "form-actions" }: { children: ReactNode; className?: string }) {
  return <div className={className}>{children}</div>;
}

/** Reusable label/value/supporting-copy shape for summary and metric grids. */
export function MetricTile({
  label,
  value,
  detail,
  valueClassName,
}: {
  label: ReactNode;
  value: ReactNode;
  detail?: ReactNode;
  valueClassName?: string;
}) {
  return <div className={patternStyles.metric}><span>{label}</span><strong className={valueClassName}>{value}</strong>{detail != null && <small>{detail}</small>}</div>;
}

type DialogProps = {
  open: boolean;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  busy?: boolean;
  tone?: "default" | "warning";
  onConfirm: () => void;
  onCancel: () => void;
};

/**
 * Small, keyboard-accessible confirmation surface used for actions with an
 * external or destructive effect.  Keeping this in the shared UI layer makes
 * the confirmation gate visible in the DOM instead of hiding it in a browser
 * native prompt.
 */
export function ConfirmationDialog({
  open,
  title,
  description,
  children,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  busy = false,
  tone = "default",
  onConfirm,
  onCancel,
}: DialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCancelRef = useRef(onCancel);

  useEffect(() => {
    onCancelRef.current = onCancel;
  }, [onCancel]);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    dialogRef.current?.focus();
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onCancelRef.current();
      if (event.key === "Tab" && dialogRef.current) {
        const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], summary[tabindex]'
        )).filter((element) => element.getClientRects().length > 0);
        if (focusable.length === 0) {
          event.preventDefault();
          dialogRef.current.focus();
          return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previous?.focus?.();
    };
  }, [busy, open]);

  if (!open) return null;
  return (
    <div className={`${patternStyles.dialog} dialog-backdrop`} role="presentation">
      <div
        ref={dialogRef}
        className="dialog-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
      >
        <header className="dialog-header">
          <h2 id={titleId}>{title}</h2>
          <IconButton onClick={onCancel} disabled={busy} aria-label="Close Dialog">×</IconButton>
        </header>
        {description && <p id={descriptionId} className="dialog-description">{description}</p>}
        {children}
        <div className="dialog-actions">
          <Button onClick={onCancel} disabled={busy}>{cancelLabel}</Button>
          <Button variant={tone === "warning" ? "danger" : "primary"} onClick={onConfirm} busy={busy}>{confirmLabel}</Button>
        </div>
      </div>
    </div>
  );
}

type TextInputDialogProps = Omit<DialogProps, "children" | "onConfirm"> & {
  label: string;
  value: string;
  onChange: (value: string) => void;
  onSubmit: (value: string) => void;
  required?: boolean;
  inputType?: "text" | "date";
  multiline?: boolean;
  placeholder?: string;
  helperText?: string;
  error?: string | null;
};

/** A labelled required/optional input dialog for the few flows that need user text. */
export function TextInputDialog({
  open,
  title,
  description,
  label,
  value,
  onChange,
  onSubmit,
  onCancel,
  required = false,
  inputType = "text",
  multiline = false,
  placeholder,
  helperText,
  error,
  confirmLabel = "Submit",
  cancelLabel = "Cancel",
  busy = false,
  tone = "default",
}: TextInputDialogProps) {
  const labelId = useId();
  const [localError, setLocalError] = useState<string | null>(null);
  useEffect(() => {
    if (open) setLocalError(null);
  }, [open]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = value.trim();
    if (required && !normalized) {
      setLocalError(`${label} is required.`);
      return;
    }
    setLocalError(null);
    onSubmit(normalized);
  }

  return (
    <ConfirmationDialog
      open={open}
      title={title}
      description={description}
      confirmLabel={confirmLabel}
      cancelLabel={cancelLabel}
      busy={busy}
      tone={tone}
      onConfirm={() => {
        const form = document.getElementById(labelId)?.closest("form") as HTMLFormElement | null;
        form?.requestSubmit();
      }}
      onCancel={onCancel}
    >
      <form className="dialog-form" onSubmit={submit}>
        <label htmlFor={labelId}><span>{required && <b className="required-mark" aria-hidden="true">*</b>}{label}</span>
          {multiline ? <Textarea id={labelId} required={required} aria-required={required || undefined} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} rows={5} autoFocus /> : <Input id={labelId} required={required} aria-required={required || undefined} type={inputType} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} autoFocus />}
        </label>
        {helperText && <small className="dialog-helper">{helperText}</small>}
        <ErrorNote role="alert">{error ?? localError}</ErrorNote>
      </form>
    </ConfirmationDialog>
  );
}

/** Offset pager for durable list endpoints (fixed page size, explicit
 * Previous/Next, caller-owned summary text between the buttons). */
export function Paginator({
  step,
  offset,
  hasMore,
  onOffsetChange,
  summary,
}: {
  step: number;
  offset: number;
  hasMore: boolean;
  onOffsetChange: (offset: number) => void;
  summary?: ReactNode;
}) {
  return (
    <div className="page-actions">
      <ActionButton disabled={offset === 0} onClick={() => onOffsetChange(Math.max(0, offset - step))}>{`Previous ${step}`}</ActionButton>
      {summary}
      <ActionButton disabled={!hasMore} onClick={() => onOffsetChange(offset + step)}>{`Next ${step}`}</ActionButton>
    </div>
  );
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

  return <nav className={`${patternStyles.tabs} horizontal-tabs ${className}`.trim()} aria-label={ariaLabel} role="tablist">{items.map((item) => <button id={`${idPrefix}-${item.id}`} key={item.id} className={`${value === item.id ? "selected" : ""}${item.attention ? " attention" : ""}`} type="button" role="tab" aria-selected={value === item.id} aria-controls={`${panelIdPrefix}-${item.id}`} tabIndex={value === item.id ? 0 : -1} onKeyDown={(event) => move(event, item.id)} onClick={() => onChange(item.id)}><span>{item.label}</span>{item.suffix}</button>)}</nav>;
}

export function SectionHeader({ title, subtitle, kicker, action }: { title?: string; subtitle?: string; kicker?: string; action?: ReactNode }) {
  return <header className={`${patternStyles.cardHead} card-head`}><div className="card-heading-copy">{kicker && <p className="card-kicker">{kicker}</p>}{title && <h2>{title}</h2>}{!kicker && subtitle && <p className="card-subtitle">{subtitle}</p>}</div>{action}</header>;
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
    <section className={`${patternStyles.card} card ${className}`} id={id}>
      {(title || subtitle || kicker || action) && (
        <SectionHeader title={title} subtitle={subtitle} kicker={kicker} action={action} />
      )}
      {bodyDescription && <p className={`${patternStyles.cardDescription} card-description`}>{bodyDescription}</p>}
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
  return <dl className={`${patternStyles.description} description-list columns-${columns} ${className}`}>{items.map((item) => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd>{item.detail != null && <small>{item.detail}</small>}</div>)}</dl>;
}

export function Badge({
  value,
  tone: toneOverride,
}: {
  value: string | null | undefined;
  tone?: "good" | "warn" | "bad" | "neutral";
}) {
  const label = value ?? "UNKNOWN";
  const inferredTone = ["OK", "HEALTHY", "SUCCEEDED", "ACTIVE", "AVAILABLE", "DURABLE", "QUIET", "FRESH", "RECOVERED", "RESOLVE", "SUPPORTED"].includes(
    label.toUpperCase(),
  )
    ? "good"
    : ["TRIGGERED", "EXPIRING", "DEGRADED", "PARTIAL", "INCOMPLETE", "STALE", "LIMITED", "WARNING", "ACKNOWLEDGE", "REVIEW", "NOT_EVALUATED", "UNSUPPORTED", "MEDIUM"].includes(label.toUpperCase())
      ? "warn"
      : ["FAILED", "ERROR", "DEAD_LETTER", "HIGH"].includes(label.toUpperCase())
        ? "bad"
        : "neutral";
  const tone = toneOverride ?? inferredTone;
  return <span className={`${patternStyles.badge} ${patternStyles[tone]} badge ${tone}`}>{label}</span>;
}

export function RefreshButton({
  onClick,
  loading,
}: {
  onClick: () => void;
  loading: boolean;
}) {
  return <Button onClick={onClick} busy={loading} busyLabel="Loading…">Refresh</Button>;
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
  onClick?: () => void;
  busy?: boolean;
  disabled?: boolean;
  busyLabel?: string;
  tone?: "default" | "warning";
}) {
  return <Button variant={tone === "warning" ? "danger" : "secondary"} className="action-button" onClick={onClick} busy={busy} busyLabel={busyLabel} disabled={disabled}>{children}</Button>;
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
  return new Intl.DateTimeFormat("en-US", {
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
  return new Intl.NumberFormat("en-US", {
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

export { MetricTile as Metric, HorizontalTabs as Tabs, ConfirmationDialog as Dialog };
