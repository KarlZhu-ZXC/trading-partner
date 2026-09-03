"use client";

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Plus, Search, X } from "lucide-react";

export type AutosuggestOption = {
  value: string;
  label: string;
  description?: string;
};

export function MultiSelectAutosuggest({
  label,
  placeholder,
  options,
  value,
  onChange,
  maxSuggestions = 10,
  closeOnSelect = false,
}: {
  label: string;
  placeholder: string;
  options: ReadonlyArray<AutosuggestOption>;
  value: ReadonlyArray<string>;
  onChange: (value: string[]) => void;
  maxSuggestions?: number;
  closeOnSelect?: boolean;
}) {
  const generatedId = useId().replaceAll(":", "");
  const listboxId = `autosuggest-${generatedId}`;
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const selected = useMemo(
    () => value.flatMap((selectedValue) => {
      const option = options.find((candidate) => candidate.value === selectedValue);
      return option ? [option] : [];
    }),
    [options, value],
  );
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const suggestions = useMemo(
    () => options
      .filter((option) => !value.includes(option.value))
      .filter((option) => {
        if (!normalizedQuery) return true;
        return [option.label, option.value, option.description ?? ""]
          .some((candidate) => candidate.toLocaleLowerCase().includes(normalizedQuery));
      })
      .slice(0, maxSuggestions),
    [maxSuggestions, normalizedQuery, options, value],
  );

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

  useEffect(() => {
    if (activeIndex >= suggestions.length) setActiveIndex(0);
  }, [activeIndex, suggestions.length]);

  function select(option: AutosuggestOption) {
    onChange([...value, option.value]);
    setQuery("");
    setActiveIndex(0);
    setOpen(!closeOnSelect);
    if (!closeOnSelect) inputRef.current?.focus();
  }

  function remove(selectedValue: string) {
    onChange(value.filter((item) => item !== selectedValue));
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((index) => suggestions.length ? (index + 1) % suggestions.length : 0);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((index) => suggestions.length ? (index - 1 + suggestions.length) % suggestions.length : 0);
      return;
    }
    if (event.key === "Enter" && open && suggestions[activeIndex]) {
      event.preventDefault();
      select(suggestions[activeIndex]);
      return;
    }
    if (event.key === "Backspace" && !query && value.length > 0) {
      remove(value[value.length - 1]);
    }
  }

  return <div className="multi-autosuggest" ref={rootRef}>
    <span className="multi-autosuggest-label">{label}</span>
    <div className={`multi-autosuggest-control${open ? " open" : ""}`} onClick={() => inputRef.current?.focus()}>
      {selected.map((option) => <span className="multi-autosuggest-chip" key={option.value} title={option.description}><span>{option.label}</span><button type="button" aria-label={`Remove ${option.label}`} onClick={(event) => { event.stopPropagation(); remove(option.value); }}><X aria-hidden="true" /></button></span>)}
      <span className="multi-autosuggest-input-wrap"><Search aria-hidden="true" /><input ref={inputRef} role="combobox" aria-label={label} aria-autocomplete="list" aria-expanded={open} aria-controls={listboxId} aria-activedescendant={open && suggestions[activeIndex] ? `${listboxId}-${activeIndex}` : undefined} autoComplete="off" value={query} placeholder={selected.length ? "Add…" : placeholder} onFocus={() => setOpen(true)} onChange={(event) => { setQuery(event.target.value); setActiveIndex(0); setOpen(true); }} onKeyDown={handleKeyDown} /></span>
    </div>
    {open ? <div className="multi-autosuggest-menu" id={listboxId} role="listbox" aria-label={`${label} Suggestions`}>
      {suggestions.length ? suggestions.map((option, index) => <button id={`${listboxId}-${index}`} type="button" role="option" aria-selected={false} className={index === activeIndex ? "active" : ""} key={option.value} onPointerDown={(event) => event.preventDefault()} onClick={() => select(option)}><span><strong>{option.label}</strong>{option.description ? <small>{option.description}</small> : null}</span><Plus aria-hidden="true" /></button>) : <p>No matching suggestions.</p>}
      {query.trim() ? <small className="multi-autosuggest-hint">Typed text is not applied until a suggestion is selected.</small> : null}
    </div> : null}
  </div>;
}
