"use client";

import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { ChevronLeft, ChevronRight, Search, X } from "lucide-react";

export type EntityBrowserOption = {
  value: string;
  label: string;
};

type SearchFilter = {
  value: string;
  onChange: (value: string) => void;
  label: string;
  placeholder: string;
  ariaLabel: string;
};

type StatusFilter = {
  value: string;
  onChange: (value: string) => void;
  label: string;
  ariaLabel: string;
  options: readonly EntityBrowserOption[];
};

type ResponsivePageSize = {
  initial: number;
  getSize: (width: number) => number;
  target?: "container" | "viewport";
};

export type EntityBrowserProps<T> = {
  items: readonly T[];
  filteredItems: readonly T[];
  selectedId: string | null;
  getId: (item: T) => string;
  onSelect: (id: string) => void;
  onClearSelection?: () => void;
  hashToId?: (hash: string) => string | null;
  search?: SearchFilter;
  status?: StatusFilter;
  onClearFilters: () => void;
  clearDisabled: boolean;
  selectedIsFilteredOut?: boolean;
  filteredNotice?: ReactNode;
  resultLabel?: (filteredCount: number, totalCount: number) => ReactNode;
  emptyMessage: ReactNode;
  noMatchesMessage: ReactNode;
  renderItem: (item: T, selected: boolean, select: (id: string) => void) => ReactNode;
  listAriaLabel: string;
  previousAriaLabel: string;
  nextAriaLabel: string;
  rangeAriaLabel: (start: number, end: number, total: number) => string;
  showRange?: boolean;
  responsivePageSize: ResponsivePageSize;
  hashKey?: string;
  className?: string;
};

function replaceHash(hash: string): void {
  const url = new URL(window.location.href);
  url.hash = hash.replace(/^#/, "");
  window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

/**
 * Shared master-list browser used by Research Subjects and Monitors.
 * Pages still own domain filtering and selection side effects; this component
 * owns only the repeated browser mechanics and accessible filter controls.
 */
export function EntityBrowser<T>({
  items,
  filteredItems,
  selectedId,
  getId,
  onSelect,
  onClearSelection,
  hashToId,
  search,
  status,
  onClearFilters,
  clearDisabled,
  selectedIsFilteredOut = false,
  filteredNotice,
  resultLabel,
  emptyMessage,
  noMatchesMessage,
  renderItem,
  listAriaLabel,
  previousAriaLabel,
  nextAriaLabel,
  rangeAriaLabel,
  showRange = true,
  responsivePageSize,
  hashKey = "entity",
  className = "",
}: EntityBrowserProps<T>) {
  const browserRef = useRef<HTMLDivElement>(null);
  const [page, setPage] = useState(0);
  const [pageDirection, setPageDirection] = useState<"previous" | "next">("next");
  const [pageSize, setPageSize] = useState(responsivePageSize.initial);

  const pageCount = Math.max(1, Math.ceil(filteredItems.length / pageSize));
  const visibleItems = filteredItems.slice(page * pageSize, (page + 1) * pageSize);

  useEffect(() => {
    const target = responsivePageSize.target ?? "container";
    const update = (width: number) => setPageSize(responsivePageSize.getSize(width));
    if (target === "viewport") {
      update(window.innerWidth);
      const onResize = () => update(window.innerWidth);
      window.addEventListener("resize", onResize);
      return () => window.removeEventListener("resize", onResize);
    }
    const element = browserRef.current;
    if (!element) return undefined;
    update(element.getBoundingClientRect().width);
    const observer = new ResizeObserver((entries) => {
      update(entries[0]?.contentRect.width ?? element.getBoundingClientRect().width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [responsivePageSize]);

  useEffect(() => {
    if (items.length === 0) {
      onClearSelection?.();
      return;
    }
    const parsedId = hashToId?.(window.location.hash);
    if (parsedId && items.some((item) => getId(item) === parsedId)) {
      if (selectedId !== parsedId) onSelect(parsedId);
      return;
    }
    if (!selectedId || !items.some((item) => getId(item) === selectedId)) {
      const first = filteredItems[0] ?? items[0];
      const firstId = getId(first);
      if (firstId) onSelect(firstId);
    }
  }, [filteredItems, getId, hashToId, items, onClearSelection, onSelect, selectedId]);

  useEffect(() => {
    const selectedIndex = filteredItems.findIndex((item) => getId(item) === selectedId);
    if (selectedIndex >= 0) {
      setPage(Math.floor(selectedIndex / pageSize));
      return;
    }
    setPage((current) => Math.min(current, pageCount - 1));
  }, [filteredItems, getId, pageCount, pageSize, selectedId]);

  function selectEntity(id: string): void {
    onSelect(id);
    replaceHash(`${hashKey}-${id}`);
  }

  const selectedItemExists = selectedId !== null && items.some((item) => getId(item) === selectedId);
  const isSelectedOutsideFilter = selectedIsFilteredOut || (selectedItemExists && !filteredItems.some((item) => getId(item) === selectedId));
  const rangeStart = filteredItems.length === 0 ? 0 : page * pageSize + 1;
  const rangeEnd = filteredItems.length === 0 ? 0 : Math.min((page + 1) * pageSize, filteredItems.length);

  return (
    <div className={`entity-browser-shell ${className}`.trim()} ref={browserRef}>
      {(search || status || resultLabel) && (
        <div className="entity-filters">
          {search && (
            <label className="entity-filter-search">
              <span>{search.label}</span>
              <span className="entity-filter-control">
                <Search aria-hidden="true" />
                <input
                  type="search"
                  value={search.value}
                  onChange={(event) => search.onChange(event.target.value)}
                  placeholder={search.placeholder}
                  aria-label={search.ariaLabel}
                />
              </span>
            </label>
          )}
          {status && (
            <label className="entity-filter-status">
              <span>{status.label}</span>
              <select value={status.value} onChange={(event) => status.onChange(event.target.value)} aria-label={status.ariaLabel}>
                {status.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
          )}
          <button className="entity-filter-clear" type="button" disabled={clearDisabled} onClick={onClearFilters}>
            <X aria-hidden="true" /> Clear Filters
          </button>
          {resultLabel && <span className="entity-filter-results" aria-live="polite">{resultLabel(filteredItems.length, items.length)}</span>}
        </div>
      )}
      {isSelectedOutsideFilter && filteredNotice}
      {items.length === 0 ? emptyMessage : filteredItems.length === 0 ? noMatchesMessage : (
        <div className="entity-browser">
          <button className="entity-browser-arrow" type="button" disabled={page === 0} onClick={() => { setPageDirection("previous"); setPage((current) => Math.max(0, current - 1)); }} aria-label={previousAriaLabel}>
            <ChevronLeft aria-hidden="true" />
          </button>
          <div className={`entity-index-list slide-${pageDirection}`} key={`${page}-${pageSize}`} style={{ "--entity-per-page": pageSize } as CSSProperties} role="listbox" aria-label={listAriaLabel}>
            {visibleItems.map((item) => renderItem(item, selectedId === getId(item), selectEntity))}
          </div>
          <div className="entity-browser-next-rail">
            <button className="entity-browser-arrow" type="button" disabled={page >= pageCount - 1} onClick={() => { setPageDirection("next"); setPage((current) => Math.min(pageCount - 1, current + 1)); }} aria-label={nextAriaLabel}>
              <ChevronRight aria-hidden="true" />
            </button>
          </div>
          {showRange && <span className="entity-browser-range" aria-label={rangeAriaLabel(rangeStart, rangeEnd, filteredItems.length)}>
            <strong>{rangeStart}–{rangeEnd}</strong><span>/ {filteredItems.length}</span>
          </span>}
        </div>
      )}
    </div>
  );
}
