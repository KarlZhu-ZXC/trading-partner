"use client";

import { asObject as asDict, textStringified as text, numberOrZero as number } from "../lib/coerce";

import { Button, Input, Textarea } from "../components/ui/controls";

import { useAccountLabel } from "../components/account-aliases";

import { useEffect, useMemo, useRef, useState } from "react";
import { ActionButton, Badge, Disclosure, ErrorNote, formatDate, shortId } from "../components/ui";
import { MultiSelectAutosuggest, type AutosuggestOption } from "../components/multi-select-autosuggest";
import { listOf, postApi } from "../lib/api";
import { requireCycleOverridePreview, requireCycleOverrideReceipt } from "./cycle-adjustment-response";

type Dict = Record<string, unknown>;
type Operation = "SPLIT" | "MERGE" | "RELINK";
type SplitGroup = "A" | "B";

function cycleOption(cycle: Dict, accountLabel: (account: Dict) => string): AutosuggestOption {
  return {
    value: text(cycle.cycle_id, ""),
    label: `${shortId(cycle.instrument_id)} · ${formatDate(cycle.opened_at)} → ${cycle.closed_at ? formatDate(cycle.closed_at) : "Open"}`,
    description: `${text(cycle.status, "UNKNOWN")} · ${text(cycle.quality, "UNKNOWN")} · ${accountLabel(cycle)}`,
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
  const accountLabel = useAccountLabel();
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
  const active = useRef(true);
  const generation = useRef(0);
  const requestLock = useRef<object | null>(null);
  type ReviewedRequest = { body: ReturnType<typeof payload>; scope: string };
  type UnconfirmedRequest = { request: ReviewedRequest; generation: number };
  const reviewedRequest = useRef<ReviewedRequest | null>(null);
  const [unconfirmed, setUnconfirmed] = useState<UnconfirmedRequest | null>(null);
  const unconfirmedRequest = useRef<UnconfirmedRequest | null>(null);

  function rememberUnconfirmed(value: UnconfirmedRequest | null) {
    unconfirmedRequest.current = value;
    setUnconfirmed(value);
  }
  const scopeSignature = JSON.stringify([cycles, transactions, overrideRevisions]);
  const currentScope = useRef(scopeSignature);
  currentScope.current = scopeSignature;
  const cycleOptions = useMemo(() => cycles.map((cycle) => cycleOption(cycle, accountLabel)), [cycles, accountLabel]);
  const selectedCycles = cycles.filter((cycle) => selectedCycleIds.includes(text(cycle.cycle_id, "")));
  const sourceActivityIds = Array.from(new Set(selectedCycles.flatMap((cycle) => listOf<string>(cycle, "activity_ids"))));
  const activityById = new Map(transactions.map((transaction) => [text(transaction.provider_transaction_id, ""), transaction]));
  const targetOptions = cycleOptions.filter((option) => !selectedCycleIds.includes(option.value));
  const groupA = sourceActivityIds.filter((activityId) => splitAssignments[activityId] === "A");
  const groupB = sourceActivityIds.filter((activityId) => splitAssignments[activityId] === "B");
  const unassigned = sourceActivityIds.filter((activityId) => !splitAssignments[activityId]);

  function invalidatePreview() {
    generation.current += 1;
    reviewedRequest.current = null;
    setPreview(null);
  }

  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      generation.current += 1;
      reviewedRequest.current = null;
    };
  }, []);

  useEffect(() => { invalidatePreview(); }, [scopeSignature]);

  useEffect(() => {
    if (!unconfirmed) return;
    for (const revision of overrideRevisions) {
      if (revision.idempotency_key !== unconfirmed.request.body.idempotency_key) continue;
      try {
        requireCycleOverrideReceipt(revision, unconfirmed.request.body);
        rememberUnconfirmed(null);
        setError(null);
        return;
      } catch { /* A foreign or incomplete durable receipt cannot resolve uncertainty. */ }
    }
  }, [overrideRevisions, unconfirmed]);

  useEffect(() => {
    setSplitAssignments((current) => Object.fromEntries(
      Object.entries(current).filter(([activityId]) => sourceActivityIds.includes(activityId)),
    ));
    setRelinkActivityIds((current) => current.filter((activityId) => sourceActivityIds.includes(activityId)));
    invalidatePreview();
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
    invalidatePreview();
    setError(null);
  }

  function activityPresentation(activityId: string): { title: string; detail: string } {
    const activity = activityById.get(activityId);
    if (!activity) return { title: shortId(activityId), detail: "Durable activity reference" };
    return {
      title: `${text(activity.side, text(activity.kind, "ACTIVITY")).toUpperCase()} · ${text(activity.quantity)} @ ${text(activity.price)} ${text(activity.currency, "")}`,
      detail: `${shortId(activity.instrument_id)} · ${formatDate(activity.occurred_at)} · ${accountLabel(activity)}`,
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
    invalidatePreview();
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
    if (requestLock.current || unconfirmedRequest.current) return;
    if (!canPreview) {
      setError(operation === "SPLIT"
        ? "Assign every source activity to Group A or Group B; both groups must contain at least one activity."
        : operation === "MERGE"
          ? "Select at least two Cycles to merge."
          : "Select one or more source Cycles, at least one activity, and a target Cycle.");
      return;
    }
    const sequence = ++generation.current;
    const request = payload();
    const lock = {};
    requestLock.current = lock;
    reviewedRequest.current = null;
    setBusy("preview");
    setError(null);
    setPreview(null);
    try {
      const response = requireCycleOverridePreview(await postApi<unknown>("/api/trade-cycle-overrides/preview", request), request);
      if (!active.current || sequence !== generation.current || currentScope.current !== scopeSignature) return;
      reviewedRequest.current = { body: request, scope: scopeSignature };
      setPreview(response);
    } catch (cause) {
      if (active.current && sequence === generation.current && currentScope.current === scopeSignature) {
        setError(cause instanceof Error ? cause.message : "Trade Cycle preview failed.");
      }
    } finally {
      if (active.current && requestLock.current === lock) {
        requestLock.current = null;
        setBusy(null);
      }
    }
  }

  async function applyRevision(original?: UnconfirmedRequest) {
    const reviewed = original?.request ?? reviewedRequest.current;
    const pending = unconfirmedRequest.current;
    if ((!original && !preview) || !reviewed || requestLock.current || reviewed.scope !== currentScope.current
      || (pending && pending.request.body.idempotency_key !== reviewed.body.idempotency_key)) return;
    const request = reviewed.body;
    const sequence = original?.generation ?? generation.current;
    const lock = {};
    requestLock.current = lock;
    setBusy("apply");
    setError(null);
    try {
      // Submit the exact draft and key whose projection the user reviewed.
      requireCycleOverrideReceipt(await postApi<unknown>("/api/trade-cycle-overrides", request), request);
      if (active.current) rememberUnconfirmed(null);
      if (!active.current || currentScope.current !== scopeSignature) return;
      if (sequence === generation.current) {
        invalidatePreview();
        setSelectedCycleIds([]);
        setTargetCycleId("");
        setSplitAssignments({});
        setRelinkActivityIds([]);
        setNote("");
      }
      onApplied();
    } catch (cause) {
      if (active.current) {
        rememberUnconfirmed({ request: reviewed, generation: sequence });
        if (sequence === generation.current && currentScope.current === scopeSignature) {
          setError(cause instanceof Error ? cause.message : "Trade Cycle revision failed.");
        }
      }
    } finally {
      if (active.current && requestLock.current === lock) {
        requestLock.current = null;
        setBusy(null);
      }
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
        {(["SPLIT", "MERGE", "RELINK"] as Operation[]).map((item) => <Button variant={operation === item ? "primary" : "secondary"} type="button" role="tab" aria-selected={operation === item} className={operation === item ? "selected" : ""} key={item} onClick={() => changeOperation(item)}>{item.charAt(0) + item.slice(1).toLowerCase()}</Button>)}
      </div>

      <section className="cycle-adjustment-step">
        <header><span>1</span><div><strong>{operation === "SPLIT" ? "Choose One Source Cycle" : "Choose Source Cycles"}</strong><small>{operation === "MERGE" ? "Select at least two Cycles from the same account, Instrument, provider, and currency." : operation === "RELINK" ? "Select the Cycles that currently contain the activities to move." : "The selected Cycle will be partitioned into two derived Cycles."}</small></div></header>
        <MultiSelectAutosuggest label={operation === "SPLIT" ? "Source Cycle" : "Source Cycles"} placeholder="Search by Instrument or date" options={cycleOptions} value={selectedCycleIds} onChange={(values) => { setSelectedCycleIds(operation === "SPLIT" ? values.slice(-1) : values); invalidatePreview(); }} maxSuggestions={8} closeOnSelect={operation === "SPLIT"} />
      </section>

      {operation === "SPLIT" && rootCycleId ? <section className="cycle-adjustment-step">
        <header><span>2</span><div><strong>Partition Every Activity</strong><small>Name both destinations, then assign every fill. Unassigned activities block Preview.</small></div><div className="cycle-adjustment-counts"><Badge value={`${groupA.length} ${splitNames.A}`} /><Badge value={`${groupB.length} ${splitNames.B}`} /><Badge value={`${unassigned.length} UNASSIGNED`} tone={unassigned.length ? "warn" : "good"} /></div></header>
        <div className="cycle-split-destinations"><label><span>Group A Destination</span><Input value={splitNames.A} onChange={(event) => { setSplitNames((current) => ({ ...current, A: event.target.value })); invalidatePreview(); }} /></label><label><span>Group B Destination</span><Input value={splitNames.B} onChange={(event) => { setSplitNames((current) => ({ ...current, B: event.target.value })); invalidatePreview(); }} /></label><div className="page-actions"><Button type="button" onClick={assignChronologicalHalves}>Split Chronologically</Button><Button type="button" onClick={() => { setSplitAssignments({}); invalidatePreview(); }}>Reset Assignments</Button></div></div>
        <div className="cycle-activity-assignment-list">{sourceActivityIds.map((activityId) => { const presentation = activityPresentation(activityId); const assignment = splitAssignments[activityId]; return <article key={activityId}><div><strong>{presentation.title}</strong><small>{presentation.detail}</small></div><div className="cycle-assignment-buttons" role="group" aria-label={`Assign ${presentation.title}`}><Button variant={assignment === "A" ? "primary" : "secondary"} aria-pressed={assignment === "A"} type="button" className={assignment === "A" ? "selected" : ""} onClick={() => { setSplitAssignments((current) => ({ ...current, [activityId]: "A" })); invalidatePreview(); }}>{splitNames.A || "Group A"}</Button><Button variant={assignment === "B" ? "primary" : "secondary"} aria-pressed={assignment === "B"} type="button" className={assignment === "B" ? "selected" : ""} onClick={() => { setSplitAssignments((current) => ({ ...current, [activityId]: "B" })); invalidatePreview(); }}>{splitNames.B || "Group B"}</Button></div></article>; })}</div>
      </section> : null}

      {operation === "RELINK" && selectedCycleIds.length > 0 ? <section className="cycle-adjustment-step">
        <header><span>2</span><div><strong>Select Activities to Move</strong><small>Only exact activities from the selected source Cycles are eligible.</small></div></header>
        <div className="cycle-activity-checklist">{sourceActivityIds.map((activityId) => { const presentation = activityPresentation(activityId); const checked = relinkActivityIds.includes(activityId); return <label key={activityId}><Input type="checkbox" checked={checked} onChange={() => { setRelinkActivityIds((current) => checked ? current.filter((item) => item !== activityId) : [...current, activityId]); invalidatePreview(); }} /><span><strong>{presentation.title}</strong><small>{presentation.detail}</small></span></label>; })}</div>
        <MultiSelectAutosuggest label="Target Cycle" placeholder="Search target Cycle" options={targetOptions} value={targetCycleId ? [targetCycleId] : []} onChange={(values) => { setTargetCycleId(values.at(-1) ?? ""); invalidatePreview(); }} maxSuggestions={8} closeOnSelect />
      </section> : null}

      <section className="cycle-adjustment-step cycle-adjustment-review">
        <header><span>{operation === "MERGE" ? "2" : "3"}</span><div><strong>Review and Preview</strong><small>The algorithm projection remains retained. Applying appends an immutable manual revision.</small></div></header>
        <label className="cycle-adjustment-note"><span>Revision Note</span><Textarea rows={3} value={note} onChange={(event) => { setNote(event.target.value); invalidatePreview(); }} placeholder="Why is this structural correction needed?" /></label>
        {preview ? <div className="cycle-adjustment-preview"><header><strong>Proposed Effective Projection</strong><Badge value={`${previewImpacts.length} IMPACT${previewImpacts.length === 1 ? "" : "S"}`} /></header>{previewImpacts.map((impact, index) => { const resultIds = listOf<string>(impact, "result_cycle_ids"); const effectiveCycles = listOf<Dict>(asDict(preview.effective_projection), "cycles").filter((cycle) => resultIds.includes(text(cycle.cycle_id, ""))); return <div key={`${text(impact.operation)}-${index}`}><span>{text(impact.operation)} · {text(impact.before_activity_count, "0")} → {text(impact.after_activity_count, "0")} activities</span><small>Source: {listOf<string>(impact, "source_cycle_ids").map(shortId).join(", ")} · Result: {resultIds.map(shortId).join(", ")}</small>{effectiveCycles.map((cycle, cycleIndex) => <article key={text(cycle.cycle_id)}><strong>{operation === "SPLIT" ? (cycleIndex === 0 ? splitNames.A : splitNames.B) : shortId(cycle.cycle_id)}</strong><span>{formatDate(cycle.opened_at)} → {cycle.closed_at ? formatDate(cycle.closed_at) : "Open"} · {listOf<string>(cycle, "activity_ids").length} activities</span><small>{listOf<string>(cycle, "activity_ids").map((activityId) => activityPresentation(activityId).title).join(" · ")}</small><em>P/L and quantities require deterministic recomputation after this structural revision.</em></article>)}<small>{listOf<string>(impact, "warning_codes").join(" · ") || "No additional warning code."}</small></div>; })}</div> : null}
        <ErrorNote>{error}</ErrorNote>
        {unconfirmed && <div role="alert" aria-label="Apply Result Unconfirmed">
          <strong>Apply Result Unconfirmed</strong>
          <p>The original Apply result is unconfirmed. It may have been recorded. Your current draft is retained; resolve this original request before previewing another correction.</p>
          <p>{unconfirmed.request.body.operation} · {shortId(unconfirmed.request.body.root_cycle_id)} · Expected Version {unconfirmed.request.body.expected_version}</p>
          <p>Original Revision Note: {unconfirmed.request.body.note ?? "No note"}</p>
          <p>Original Request Key: <code>{unconfirmed.request.body.idempotency_key}</code></p>
          {unconfirmed.request.scope !== scopeSignature
            ? <p>Durable Cycle context changed. Inspect the durable Cycle records for this exact request key; retry is disabled for changed context.</p>
            : <p>Inspect the durable Cycle records or explicitly retry the original reviewed request with the same key.</p>}
          {(!preview || reviewedRequest.current?.body.idempotency_key !== unconfirmed.request.body.idempotency_key) && <ActionButton busy={busy === "apply"} disabled={busy !== null || unconfirmed.request.scope !== scopeSignature} onClick={() => { void applyRevision(unconfirmed); }}>Retry Original Request</ActionButton>}
        </div>}
        <div className="cycle-adjustment-actions"><ActionButton busy={busy === "preview"} disabled={busy !== null || Boolean(unconfirmed) || !canPreview} onClick={() => { void previewImpact(); }}>Preview Impact</ActionButton><ActionButton busy={busy === "apply"} disabled={busy !== null || !preview} tone={preview ? "warning" : "default"} onClick={() => { void applyRevision(); }}>Apply Revision</ActionButton><small>{preview ? "Preview is current. Applying appends the reviewed structural revision." : "Apply stays disabled until the current selection has a successful Preview."}</small></div>
      </section>
    </div>
  </Disclosure>;
}
