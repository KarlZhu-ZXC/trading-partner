"use client";

import type { ComponentProps, ReactNode } from "react";
import Link from "next/link";
import { ArrowRight, ArrowUpRight, LoaderCircle, X } from "lucide-react";
import controlStyles from "./controls.module.css";

export type ButtonVariant = "primary" | "secondary" | "danger";
export type ControlSize = "sm" | "md";
type ButtonProps = ComponentProps<"button"> & { variant?: ButtonVariant; size?: ControlSize; busy?: boolean; busyLabel?: string };

export function Button({ variant = "secondary", size = "md", busy = false, busyLabel = "Working…", disabled, className = "", children, type = "button", ...props }: ButtonProps) {
  return <button {...props} type={type} disabled={disabled || busy} aria-busy={busy || undefined} className={`${controlStyles.button} ${controlStyles[variant]} ${controlStyles[size]} ${className}`}>
    {busy && <LoaderCircle className={controlStyles.spinner} aria-hidden="true" />}{busy ? busyLabel : children}
  </button>;
}

export function LinkButton({ href, children, className = "", size = "md", external = false, ...props }: Omit<ComponentProps<"a">, "href"> & { href: string; size?: ControlSize; external?: boolean }) {
  const classes = `${controlStyles.button} ${controlStyles.secondary} ${controlStyles[size]} ${className}`;
  const content = <><span>{children}</span>{external ? <ArrowUpRight aria-hidden="true" /> : <ArrowRight aria-hidden="true" />}</>;
  // Native hash navigation emits hashchange for existing tab controllers.
  return href.startsWith("#")
    ? <a {...props} href={href} className={classes}>{content}</a>
    : <Link {...props} href={href} className={classes}>{content}</Link>;
}

export function TextLink({ className = "", ...props }: ComponentProps<"a">) {
  return <a {...props} className={`${controlStyles.textLink} ${className}`} />;
}

export function IconButton({ children, className = "", ...props }: ButtonProps & { "aria-label": string }) {
  return <Button {...props} className={`${controlStyles.iconButton} ${className}`}>{children}</Button>;
}

export function Input({ className = "", appearance = "default", ...props }: ComponentProps<"input"> & { appearance?: "default" | "embedded" }) {
  return <input {...props} className={`${controlStyles.input} ${appearance === "embedded" ? controlStyles.embedded : ""} ${className}`} />;
}
export function Select({ className = "", ...props }: ComponentProps<"select">) {
  return <select {...props} className={`${controlStyles.input} ${controlStyles.select} ${className}`} />;
}
export function Textarea({ className = "", appearance = "default", ...props }: ComponentProps<"textarea"> & { appearance?: "default" | "embedded" }) {
  return <textarea {...props} className={`${controlStyles.input} ${controlStyles.textarea} ${appearance === "embedded" ? controlStyles.embedded : ""} ${className}`} />;
}
export function FilterBar({ className = "", children, ...props }: ComponentProps<"section">) {
  return <section {...props} className={`${controlStyles.filterBar} ${className}`}>{children}</section>;
}
export function DateRange({ start, end, onStartChange, onEndChange, invalid = false }: { start: string; end: string; onStartChange: (value: string) => void; onEndChange: (value: string) => void; invalid?: boolean }) {
  return <><label className={controlStyles.field}><span><b className="required-mark" aria-hidden="true">*</b>Start Date</span><Input type="date" required value={start} aria-invalid={invalid} onChange={(e) => onStartChange(e.target.value)} /></label><label className={controlStyles.field}><span><b className="required-mark" aria-hidden="true">*</b>End Date</span><Input type="date" required value={end} aria-invalid={invalid} onChange={(e) => onEndChange(e.target.value)} /></label></>;
}
export function SelectableRow({ selected, className = "", type = "button", ...props }: ComponentProps<"button"> & { selected?: boolean }) {
  return <button {...props} type={type} className={`${controlStyles.row} ${selected ? controlStyles.selected : ""} ${className}`} />;
}
export function Tag({ children }: { children: ReactNode }) { return <span className={controlStyles.tag}>{children}</span>; }
export function FilterChip({ children, onRemove, label, title }: { children: ReactNode; onRemove: () => void; label: string; title?: string }) {
  return <span className={controlStyles.chip} title={title}><span>{children}</span><IconButton size="sm" aria-label={`Remove ${label}`} onClick={(event) => { event.stopPropagation(); onRemove(); }}><X aria-hidden="true" /></IconButton></span>;
}
export function Table({ className = "", ...props }: ComponentProps<"table">) { return <table {...props} className={`${controlStyles.table} ${className}`} />; }

/** Dismiss-only backdrop: never styled or announced as a primary action. */
export function DismissLayer({ className = "", type = "button", ...props }: ComponentProps<"button"> & { "aria-label": string }) {
  return <button {...props} type={type} className={`${controlStyles.dismissLayer} ${className}`} />;
}

export function MenuItem({ className = "", type = "button", ...props }: ComponentProps<"button">) {
  return <button {...props} type={type} role="menuitem" className={`${controlStyles.menuItem} ${className}`} />;
}
export function SortButton({ className = "", active = false, type = "button", ...props }: ComponentProps<"button"> & { active?: boolean }) {
  return <button {...props} type={type} className={`${controlStyles.sortButton} ${active ? controlStyles.sortActive : ""} ${className}`} />;
}

/** Focusable separator with pointer/keyboard resizing supplied by its owner. */
export function ResizeHandle({ className = "", type = "button", ...props }: ComponentProps<"button"> & { "aria-label": string }) {
  return <button {...props} type={type} role="separator" className={`${controlStyles.resizeHandle} ${className}`} />;
}
