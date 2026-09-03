"use client";

import { useEffect, useMemo, useState } from "react";
import { ActionButton, Badge, Disclosure, ErrorNote, formatDate, shortId } from "../components/ui";
import { MultiSelectAutosuggest, type AutosuggestOption } from "../components/multi-select-autosuggest";
import { listOf, postApi } from "../lib/api";

type Dict = Record<string, unknown>;
type Operation = "SPLIT" | "MERGE" | "RELINK";
type SplitGroup = "A" | "B";

function asDict(value: unknown): Dict {
  return value && typeof value === "object" ? value as Dict : {};
}

function text(value: unknown, fallback = "—"): string {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function number(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function cycleOption(cycle: Dict): AutosuggestOption {
  return {
    value: text(cycle.cycle_id, ""),
    label: `${shortId(cycle.instrument_id)} · ${formatDate(cycle.opened_at)} → ${cycle.closed_at ? formatDate(cycle.closed_at) : "Open"}`,
    description: `${text(cycle.status, "UNKNOWN")} · ${text(cycle.quality, "UNKNOWN")} · ${shortId(cycle.account_ref)}`,
  };
}

export function CycleAdjustmentEditor({
  cycles,
  transactions,
  overrideRevisions,
  onApplied,
}: {
  cycles: Dict[];
  transactions: Dict[];
  overrideRevisions: Dict[];
  onApplied: () => void;
}) {
  const [operation, setOperation] = useState<Operation>("SPLIT");
  const [selectedCycleIds, setSelectedCycleIds] = useState<string[]>([]);
  const [targetCycleId, setTargetCycleId] = useState("");
  const [splitAssignments, setSplitAssignments] = useState<Record<string, SplitGroup>>({});
  const [splitNames, setSplitNames] = useState<Record<SplitGroup, string>>({ A: "Earlier Cycle", B: "Later Cycle" });
  const [relinkActivityIds, setRelinkActivityIds] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState<Dict | null>(null);
  const [busy, setBusy] = useState<"preview" | "apply" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cycleOptions = useMemo(() => cycles.map(cycleOption), [cycles]);
  const selectedCycles = cycles.filter((cycle) => selectedCycleIds.includes(text(cycle.cycle_id, "")));
  const sourceActivityIds = Array.from(new Set(selectedCycles.flatMap((cycle) => listOf<string>(cycle, "activity_ids"))));
  const activityById = new Map(transactions.map((transaction) => [text(transaction.provider_transaction_id, ""), transaction]));
  const targetOptions = cycleOptions.filter((option) => !selectedCycleIds.includes(option.value));
  const groupA = sourceActivityIds.filter((activityId) => splitAssignments[activityId] === "A");
  const groupB = sourceActivityIds.filter((activityId) => splitAssignments[activityId] === "B");
  const unassigned = sourceActivityIds.filter((activityId) => !splitAssignments[activityId]);

  useEffect(() => {
    setSplitAssignments((current) => Object.fromEntries(
      Object.entries(current).filter(([activityId]) => sourceActivityIds.includes(activityId)),
    ));
    setRelinkActivityIds((current) => current.filter((activityId) => sourceActivityIds.includes(activityId)));
    setPreview(null);
  }, [selectedCycleIds.join("|")]);

  useEffect(() => {
    const validIds = new Set(cycles.map((cycle) => text(cycle.cycle_id, "")));
    setSelectedCycleIds((current) => current.filter((cycleId) => validIds.has(cycleId)));
    if (targetCycleId && !validIds.has(targetCycleId)) setTargetCycleId("");
  }, [cycles, targetCycleId]);

  function changeOperation(value: Operation) {
    setOperation(value);
    setSelectedCycleIds([]);
    setTargetCycleId("");
    setSplitAssignments({});
    setRelinkActivityIds([]);
    setPreview(null);
    setError(null);
  }

  function activityPresentation(activityId: string): { title: string; detail: string } {
    const activity = activityById.get(activityId);
    if (!activity) return { title: shortId(activityId), detail: "Durable activity reference" };
    return {
      title: `${text(activity.side, text(activity.kind, "ACTIVITY")).toUpperCase()} · ${text(activity.quantity)} @ ${text(activity.price)} ${text(activity.currency, "")}`,
      detail: `${shortId(activity.instrument_id)} · ${formatDate(activity.occurred_at)} · ${shortId(activity.account_ref)}`,
    };
  }

  function assignChronologicalHalves() {
    const ordered = [...sourceActivityIds].sort((left, right) => {
      const leftTime = Date.parse(text(activityById.get(left)?.occurred_at, "")) || 0;
      const rightTime = Date.parse(text(activityById.get(right)?.occurred_at, "")) || 0;
      return leftTime - rightTime;
    });
    const boundary = Math.max(1, Math.floor(ordered.length / 2));
    setSplitAssignments(Object.fromEntries(ordered.map((activityId, index) => [activityId, index < boundary ? "A" : "B"])));
    setPreview(null);
  }

  const rootCycleId = selectedCycleIds[0] ?? "";
  const canPreview = operation === "SPLIT"
    ? selectedCycleIds.length === 1 && sourceActivityIds.length >= 2 && groupA.length > 0 && groupB.length > 0 && unassigned.length === 0
    : operation === "MERGE"
      ? selectedCycleIds.length >= 2
      : selectedCycleIds.length >= 1 && Boolean(targetCycleId) && relinkActivityIds.length > 0;

  function payload() {
    const latestVersion = overrideRevisions
      .filter((item) => text(item.root_cycle_id, "") === rootCycleId)
      .reduce((current, item) => Math.max(current, number(item.version)), 0);
    const cycleIds = operation === "RELINK"
      ? [...selectedCycleIds, targetCycleId]
      : selectedCycleIds;
    return {
      root_cycle_id: rootCycleId,
      operation,
      cycle_ids: cycleIds,
      activity_ids: operation === "RELINK" ? relinkActivityIds : [],
      split_groups: operation === "SPLIT" ? [groupA, groupB] : [],
      target_cycle_id: operation === "RELINK" ? targetCycleId : null,
      note: note.trim() || null,
      expected_version: latestVersion,
      idempotency_key: `console-trade-cycle-override-${crypto.randomUUID()}`,
      authorization_note: `User confirmed the ${operation} impact for the exact Trade Cycle projection.`,
    };
  }

  async function previewImpact() {
    if (!canPreview) {
      setError(operation === "SPLIT"
        ? "Assign every source activity to Group A or Group B; both groups must contain at least one activity."
        : operation === "MERGE"
          ? "Select at least two Cycles to merge."
          : "Select one or more source Cycles, at least one activity, and a target Cycle.");
      return;
    }
    setBusy("preview");
    setError(null);
    setPreview(null);
    try {
      setPreview(asDict(await postApi<Dict>("/api/trade-cycle-overrides/preview", payload())));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Trade Cycle preview failed.");
    } finally {
      setBusy(null);
    }
  }

  async function applyRevision() {
    if (!preview) return;
    setBusy("apply");
    setError(null);
    try {
      await postApi<Dict>("/api/trade-cycle-overrides", payload());
      setPreview(null);
      setSelectedCycleIds([]);
      setTargetCycleId("");
      setSplitAssignments({});
      setRelinkActivityIds([]);
      setNote("");
      onApplied();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Trade Cycle revision failed.");
    } finally {
      setBusy(null);
    }
  }

  const previewImpacts = listOf<Dict>(preview, "impacts");
  return <Disclosure
    className="cycle-adjustment-disclosure"
    title="Cycle Adjustments"
    description="Split one Cycle, merge comparable Cycles, or relink exact activities. Preview is mandatory."
    meta={preview ? <Badge value="PREVIEW READY" tone="good" /> : <Badge value="APPEND ONLY" tone="neutral" />}
  >
    <div className="cycle-adjustment-editor">
      <div className="cycle-adjustment-operations" role="tablist" aria-label="Cycle Adjustment Operation">
        {(["SPLIT", "MERGE", "RELINK"] as Operation[]).map((item) => <button type="button" role="tab" aria-selected={operation === item} className={operation === item ? "selected" : ""} key={item} onClick={() => changeOperation(item)}>{item.charAt(0) + item.slice(1).toLowerCase()}</button>)}
      </div>

      <section className="cycle-adjustment-step">
        <header><span>1</span><div><strong>{operation === "SPLIT" ? "Choose One Source Cycle" : "Choose Source Cycles"}</strong><small>{operation === "MERGE" ? "Select at least two Cycles from the same account, Instrument, provider, and currency." : operation === "RELINK" ? "Select the Cycles that currently contain the activities to move." : "The selected Cycle will be partitioned into two derived Cycles."}</small></div></header>
        <MultiSelectAutosuggest label={operation === "SPLIT" ? "Source Cycle" : "Source Cycles"} placeholder="Search by Instrument or date" options={cycleOptions} value={selectedCycleIds} onChange={(values) => { setSelectedCycleIds(operation === "SPLIT" ? values.slice(-1) : values); setPreview(null); }} maxSuggestions={8} closeOnSelect={operation === "SPLIT"} />
      </section>

      {operation === "SPLIT" && rootCycleId ? <section className="cycle-adjustment-step">
        <header><span>2</span><div><strong>Partition Every Activity</strong><small>Name both destinations, then assign every fill. Unassigned activities block Preview.</small></div><div className="cycle-adjustment-counts"><Badge value={`${groupA.length} ${splitNames.A}`} /><Badge value={`${groupB.length} ${splitNames.B}`} /><Badge value={`${unassigned.length} UNASSIGNED`} tone={unassigned.length ? "warn" : "good"} /></div></header>
        <div className="cycle-split-destinations"><label><span>Group A Destination</span><input value={splitNames.A} onChange={(event) => setSplitNames((current) => ({ ...current, A: event.target.value }))} /></label><label><span>Group B Destination</span><input value={splitNames.B} onChange={(event) => setSplitNames((current) => ({ ...current, B: event.target.value }))} /></label><div className="page-actions"><button type="button" onClick={assignChronologicalHalves}>Split Chronologically</button><button type="button" onClick={() => { setSplitAssignments({}); setPreview(null); }}>Reset Assignments</button></div></div>
        <div className="cycle-activity-assignment-list">{sourceActivityIds.map((activityId) => { const presentation = activityPresentation(activityId); const assignment = splitAssignments[activityId]; return <article key={activityId}><div><strong>{presentation.title}</strong><small>{presentation.detail}</small></div><div className="cycle-assignment-buttons" role="group" aria-label={`Assign ${presentation.title}`}><button type="button" className={assignment === "A" ? "selected" : ""} onClick={() => { setSplitAssignments((current) => ({ ...current, [activityId]: "A" })); setPreview(null); }}>{splitNames.A || "Group A"}</button><button type="button" className={assignment === "B" ? "selected" : ""} onClick={() => { setSplitAssignments((current) => ({ ...current, [activityId]: "B" })); setPreview(null); }}>{splitNames.B || "Group B"}</button></div></article>; })}</div>
      </section> : null}

      {operation === "RELINK" && selectedCycleIds.length > 0 ? <section className="cycle-adjustment-step">
        <header><span>2</span><div><strong>Select Activities to Move</strong><small>Only exact activities from the selected source Cycles are eligible.</small></div></header>
        <div className="cycle-activity-checklist">{sourceActivityIds.map((activityId) => { const presentation = activityPresentation(activityId); const checked = relinkActivityIds.includes(activityId); return <label key={activityId}><input type="checkbox" checked={checked} onChange={() => { setRelinkActivityIds((current) => checked ? current.filter((item) => item !== activityId) : [...current, activityId]); setPreview(null); }} /><span><strong>{presentation.title}</strong><small>{presentation.detail}</small></span></label>; })}</div>
        <MultiSelectAutosuggest label="Target Cycle" placeholder="Search target Cycle" options={targetOptions} value={targetCycleId ? [targetCycleId] : []} onChange={(values) => { setTargetCycleId(values.at(-1) ?? ""); setPreview(null); }} maxSuggestions={8} closeOnSelect />
      </section> : null}

      <section className="cycle-adjustment-step cycle-adjustment-review">
        <header><span>{operation === "MERGE" ? "2" : "3"}</span><div><strong>Review and Preview</strong><small>The algorithm projection remains retained. Applying appends an immutable manual revision.</small></div></header>
        <label className="cycle-adjustment-note"><span>Revision Note</span><textarea rows={3} value={note} onChange={(event) => { setNote(event.target.value); setPreview(null); }} placeholder="Why is this structural correction needed?" /></label>
        {preview ? <div className="cycle-adjustment-preview"><header><strong>Proposed Effective Projection</strong><Badge value={`${previewImpacts.length} IMPACT${previewImpacts.length === 1 ? "" : "S"}`} /></header>{previewImpacts.map((impact, index) => { const resultIds = listOf<string>(impact, "result_cycle_ids"); const effectiveCycles = listOf<Dict>(asDict(preview.effective_projection), "cycles").filter((cycle) => resultIds.includes(text(cycle.cycle_id, ""))); return <div key={`${text(impact.operation)}-${index}`}><span>{text(impact.operation)} · {text(impact.before_activity_count, "0")} → {text(impact.after_activity_count, "0")} activities</span><small>Source: {listOf<string>(impact, "source_cycle_ids").map(shortId).join(", ")} · Result: {resultIds.map(shortId).join(", ")}</small>{effectiveCycles.map((cycle, cycleIndex) => <article key={text(cycle.cycle_id)}><strong>{operation === "SPLIT" ? (cycleIndex === 0 ? splitNames.A : splitNames.B) : shortId(cycle.cycle_id)}</strong><span>{formatDate(cycle.opened_at)} → {cycle.closed_at ? formatDate(cycle.closed_at) : "Open"} · {listOf<string>(cycle, "activity_ids").length} activities</span><small>{listOf<string>(cycle, "activity_ids").map((activityId) => activityPresentation(activityId).title).join(" · ")}</small><em>P/L and quantities require deterministic recomputation after this structural revision.</em></article>)}<small>{listOf<string>(impact, "warning_codes").join(" · ") || "No additional warning code."}</small></div>; })}</div> : null}
        <ErrorNote>{error}</ErrorNote>
        <div className="cycle-adjustment-actions"><ActionButton busy={busy === "preview"} disabled={!canPreview} onClick={() => { void previewImpact(); }}>Preview Impact</ActionButton><ActionButton busy={busy === "apply"} disabled={!preview} tone={preview ? "warning" : "default"} onClick={() => { void applyRevision(); }}>Apply Revision</ActionButton><small>{preview ? "Preview is current. Applying appends the reviewed structural revision." : "Apply stays disabled until the current selection has a successful Preview."}</small></div>
      </section>
    </div>
  </Disclosure>;
}
