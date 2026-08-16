"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ChevronDown } from "lucide-react";
import { ActionButton, Badge, Card, Empty, displayJson, formatDate, formatDecimal, shortId } from "../components/ui";
import { envelopeData, listOf, postApi } from "../lib/api";

type Dict = Record<string, unknown>;

function value(value: unknown, fallback = "—"): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return typeof value === "string" && value.trim() ? value : fallback;
}

function key(prefix: string): string {
  return `console-${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function split(value: string): string[] {
  return value.split(/[\n,\uFF0C]/).map((item) => item.trim()).filter(Boolean);
}

function localIso(): string {
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  return now.toISOString().slice(0, 16);
}

function asUtc(value: string): string {
  return new Date(value).toISOString();
}

type Write = (toolName: string, request: Dict, confirmation?: string) => Promise<Dict>;

type ConditionDraft = {
  conditionCode: string;
  phase: string;
  mode: string;
  description: string;
  severity: string;
  factType: string;
  metricKey: string;
  comparator: string;
  threshold: string;
  unit: string;
};

const EMPTY_CONDITION: ConditionDraft = {
  conditionCode: "",
  phase: "REVIEW",
  mode: "MANUAL",
  description: "",
  severity: "MEDIUM",
  factType: "PRICE",
  metricKey: "last_price",
  comparator: "GTE",
  threshold: "",
  unit: "USD",
};

function planCondition(condition: Dict): string {
  if (condition.mode === "MANUAL") return "Manual review";
  const comparator = ({ GT: ">", GTE: "≥", LT: "<", LTE: "≤", EQ: "=", OCCURRED: "Occurred" } as Record<string, string>)[value(condition.comparator, "")];
  return comparator === "Occurred"
    ? `${value(condition.fact_type)} · ${value(condition.metric_key)} · Occurred`
    : `${value(condition.fact_type)} · ${value(condition.metric_key)} ${comparator ?? "—"} ${formatDecimal(condition.threshold, 4)} ${value(condition.unit, "")}`;
}

function TradePlanWorkspace({ subject, state, onWrite, onRefresh, busy }: { subject: Dict; state: Dict; onWrite: Write; onRefresh: () => void; busy: boolean }) {
  const plans = listOf<Dict>(state, "trade_plan_versions");
  const current = state.current_trade_plan && typeof state.current_trade_plan === "object" ? state.current_trade_plan as Dict : null;
  const theses = listOf<Dict>(state, "theses");
  const selectedCandidate = listOf<Dict>(state, "watchlist_items").find((item) => value(item.status, "").toLowerCase() === "selected");
  const liveTheses = theses.filter((item) => ["active", "strengthened", "weakened"].includes(value(item.status, "").toLowerCase()));
  const [editing, setEditing] = useState(false);
  const [compareVersion, setCompareVersion] = useState("");
  const [thesisId, setThesisId] = useState("");
  const [instrument, setInstrument] = useState("");
  const [status, setStatus] = useState("DRAFT");
  const [currency, setCurrency] = useState("USD");
  const [referencePrice, setReferencePrice] = useState("");
  const [referenceAt, setReferenceAt] = useState(localIso());
  const [validFrom, setValidFrom] = useState(localIso());
  const [validUntil, setValidUntil] = useState("");
  const [target, setTarget] = useState("");
  const [maximum, setMaximum] = useState("");
  const [riskBudget, setRiskBudget] = useState("");
  const [stop, setStop] = useState("");
  const [notes, setNotes] = useState("");
  const [conditions, setConditions] = useState<ConditionDraft[]>([]);

  function openEditor() {
    setThesisId(value(current?.thesis_id, value(liveTheses[0]?.thesis_id, "")));
    setInstrument(value(current?.instrument_id, value(subject.primary_instrument_id, value(selectedCandidate?.instrument_id, ""))));
    setStatus(value(current?.status, "DRAFT"));
    setCurrency(value(current?.currency, "USD"));
    setReferencePrice(value(current?.reference_price, ""));
    setReferenceAt(current?.reference_price_at ? new Date(String(current.reference_price_at)).toISOString().slice(0, 16) : localIso());
    setValidFrom(localIso());
    setValidUntil("");
    setTarget(value(current?.target_position_percent, ""));
    setMaximum(value(current?.max_position_percent, ""));
    setRiskBudget(value(current?.risk_budget_percent, ""));
    setStop(value(current?.stop_price, ""));
    setNotes(value(current?.notes, ""));
    setConditions(listOf<Dict>(current ?? {}, "conditions").map((item) => ({
      conditionCode: value(item.condition_code, ""), phase: value(item.phase, "REVIEW"), mode: value(item.mode, "MANUAL"), description: value(item.description, ""), severity: value(item.severity, "MEDIUM"), factType: value(item.fact_type, "PRICE"), metricKey: value(item.metric_key, "last_price"), comparator: value(item.comparator, "GTE"), threshold: value(item.threshold, ""), unit: value(item.unit, "USD"),
    })));
    setEditing(true);
  }

  function updateCondition(index: number, field: keyof ConditionDraft, next: string) {
    setConditions((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, [field]: next } : item));
  }

  async function propose() {
    if (!thesisId || !instrument || !referencePrice || !target || !maximum || !riskBudget || !notes.trim()) {
      window.alert("Thesis, instrument, reference price, position limits, risk budget, and notes are required.");
      return;
    }
    if (status === "ACTIVE" && conditions.length === 0) {
      window.alert("An ACTIVE Trade Plan requires at least one condition.");
      return;
    }
    const invalid = conditions.find((item) => !item.conditionCode.trim() || !item.description.trim() || (item.mode === "MONITORABLE" && (!item.metricKey.trim() || (item.comparator !== "OCCURRED" && !item.threshold))));
    if (invalid) { window.alert("Each condition needs a machine code and clear meaning; monitorable conditions also need a complete fact comparison."); return; }
    const payload: Dict = {
      kind: "trade_plan",
      plan_id: current?.plan_id ?? null,
      expected_version: current?.version ?? null,
      thesis_id: thesisId,
      instrument_id: instrument.trim(),
      status,
      valid_from: asUtc(validFrom),
      valid_until: validUntil ? asUtc(validUntil) : null,
      currency: currency.trim().toUpperCase(),
      reference_price: referencePrice,
      reference_price_at: asUtc(referenceAt),
      target_position_percent: target,
      max_position_percent: maximum,
      risk_budget_percent: riskBudget,
      stop_price: stop || null,
      conditions: conditions.map((item) => item.mode === "MANUAL" ? {
        condition_code: item.conditionCode.trim(), phase: item.phase, mode: item.mode, description: item.description.trim(), severity: item.severity,
      } : {
        condition_code: item.conditionCode.trim(), phase: item.phase, mode: item.mode, description: item.description.trim(), severity: item.severity, fact_type: item.factType, metric_key: item.metricKey.trim(), comparator: item.comparator, threshold: item.comparator === "OCCURRED" ? null : item.threshold, unit: item.unit.trim() || null, instrument_id: instrument.trim(),
      }),
      notes: notes.trim(),
    };
    await onWrite("research_judgment_propose", { operation: "research_state", case_id: value(subject.subject_id), payload, proposed_by: "user", proposed_by_rationale: "Trade Plan version proposed from the local Research workspace", idempotency_key: key("trade-plan") }, "research_judgment_propose");
    setEditing(false);
    onRefresh();
  }

  const compared = plans.find((item) => String(item.version) === compareVersion) ?? null;
  const diffRows = current && compared ? [
    ["Status", compared.status, current.status], ["Reference", compared.reference_price, current.reference_price], ["Target %", compared.target_position_percent, current.target_position_percent], ["Max %", compared.max_position_percent, current.max_position_percent], ["Risk %", compared.risk_budget_percent, current.risk_budget_percent], ["Stop", compared.stop_price, current.stop_price], ["Conditions", listOf<Dict>(compared, "conditions").length, listOf<Dict>(current, "conditions").length],
  ] : [];

  const monitorHandoff = current?.status === "ACTIVE" ? `/monitors?trade_plan_id=${encodeURIComponent(value(current.plan_id, ""))}&trade_plan_version=${encodeURIComponent(value(current.version, ""))}&subject_id=${encodeURIComponent(value(subject.subject_id, ""))}&instrument_id=${encodeURIComponent(value(current.instrument_id, ""))}` : "";

  return <Card id="research-section-plan" className="research-trade-plan-card" kicker="POSITION INTENT" title="Trade Plan" subtitle="Versioned sizing and review conditions · non-executing" action={<div className="research-detail-actions">{current && <Badge value={`V${value(current.version)} · ${value(current.status)}`} />}{monitorHandoff && <Link className="close-button" href={monitorHandoff}>Create Monitor From Plan</Link>}<ActionButton onClick={openEditor}>{current ? "Propose New Version" : "Create Trade Plan"}</ActionButton></div>}>
    {current ? <>
      <div className="trade-plan-identity"><div><span>Instrument</span><strong>{shortId(current.instrument_id)}</strong><small>{value(current.instrument_id)}</small></div><div><span>Plan ID</span><strong>{value(current.plan_id)}</strong><small>{plans.length} persisted versions</small></div><div><span>Thesis</span><strong>{shortId(current.thesis_id)}</strong><small>{value(current.thesis_id)}</small></div><div><span>Confirmed</span><strong>{formatDate(current.created_at)}</strong><small>{value(current.confirmed_by)}</small></div></div>
      <div className="trade-plan-metrics"><div className="trade-plan-price"><span>Reference Price</span><strong>{value(current.currency, "")} {formatDecimal(current.reference_price, 4)}</strong><small>{formatDate(current.reference_price_at)}</small></div><div><span>Target Position</span><strong>{formatDecimal(current.target_position_percent, 2)}%</strong></div><div><span>Max Position</span><strong>{formatDecimal(current.max_position_percent, 2)}%</strong></div><div><span>Risk Budget</span><strong>{formatDecimal(current.risk_budget_percent, 2)}%</strong></div><div><span>Stop Price</span><strong>{current.stop_price == null ? "—" : formatDecimal(current.stop_price, 4)}</strong></div></div>
      <div className="trade-plan-conditions">{listOf<Dict>(current, "conditions").map((condition) => <article key={value(condition.condition_code)}><header><span className="mono">{value(condition.condition_code)}</span><span className="trade-plan-mode">{value(condition.phase)} · {value(condition.mode)}</span></header><strong>{value(condition.description)}</strong><small>{planCondition(condition)}</small></article>)}</div>
      {plans.length > 1 && <div className="trade-plan-diff"><label><span>Compare with History</span><select value={compareVersion} onChange={(event) => setCompareVersion(event.target.value)}><option value="">Select Version</option>{plans.filter((item) => item.version !== current.version).map((item) => <option value={String(item.version)} key={String(item.version)}>v{value(item.version)} · {formatDate(item.created_at)}</option>)}</select></label>{diffRows.length > 0 && <table><thead><tr><th>Field</th><th>Prior v{compareVersion}</th><th>Current v{value(current.version)}</th></tr></thead><tbody>{diffRows.map(([label, before, after]) => <tr key={String(label)}><th>{String(label)}</th><td>{value(before)}</td><td className={String(before) !== String(after) ? "changed" : ""}>{value(after)}</td></tr>)}</tbody></table>}</div>}
    </> : <div className="research-trade-plan-empty"><strong>No Confirmed Trade Plan Yet</strong><span>Select a live Thesis, then propose the first non-executing plan candidate.</span></div>}
    {editing && <div className="continuity-editor"><div className="research-form-grid"><label className="research-field"><span><b className="required-mark" aria-hidden="true">*</b>Bound Thesis</span><select required value={thesisId} onChange={(event) => setThesisId(event.target.value)}><option value="">Select One</option>{liveTheses.map((item) => <option value={value(item.thesis_id)} key={value(item.thesis_id)}>{value(item.title)} · {shortId(item.thesis_id)}</option>)}</select></label><label className="research-field"><span><b className="required-mark" aria-hidden="true">*</b>Plan Status</span><select required value={status} onChange={(event) => setStatus(event.target.value)}>{["DRAFT", "ACTIVE", "PAUSED", "ARCHIVED"].map((item) => <option key={item}>{item}</option>)}</select></label><label className="research-field"><span><b className="required-mark" aria-hidden="true">*</b>Execution / Position Instrument</span><input required value={instrument} onChange={(event) => setInstrument(event.target.value)} /></label><label className="research-field"><span><b className="required-mark" aria-hidden="true">*</b>Currency</span><input required value={currency} onChange={(event) => setCurrency(event.target.value)} /></label><label className="research-field"><span><b className="required-mark" aria-hidden="true">*</b>Reference Price</span><input required type="number" step="any" value={referencePrice} onChange={(event) => setReferencePrice(event.target.value)} /></label><label className="research-field"><span><b className="required-mark" aria-hidden="true">*</b>Reference Price Time</span><input required type="datetime-local" value={referenceAt} onChange={(event) => setReferenceAt(event.target.value)} /></label><label className="research-field"><span><b className="required-mark" aria-hidden="true">*</b>Valid From</span><input required type="datetime-local" value={validFrom} onChange={(event) => setValidFrom(event.target.value)} /></label><label className="research-field"><span>Valid Until</span><input type="datetime-local" value={validUntil} onChange={(event) => setValidUntil(event.target.value)} /></label><label className="research-field"><span><b className="required-mark" aria-hidden="true">*</b>Target Position %</span><input required type="number" min="0" max="100" step="any" value={target} onChange={(event) => setTarget(event.target.value)} /></label><label className="research-field"><span><b className="required-mark" aria-hidden="true">*</b>Max Position %</span><input required type="number" min="0" max="100" step="any" value={maximum} onChange={(event) => setMaximum(event.target.value)} /></label><label className="research-field"><span><b className="required-mark" aria-hidden="true">*</b>Risk Budget %</span><input required type="number" min="0" max="100" step="any" value={riskBudget} onChange={(event) => setRiskBudget(event.target.value)} /></label><label className="research-field"><span>Stop Price</span><input type="number" step="any" value={stop} onChange={(event) => setStop(event.target.value)} /></label><label className="research-field research-field-wide"><span><b className="required-mark" aria-hidden="true">*</b>Plan Notes</span><textarea required rows={4} value={notes} onChange={(event) => setNotes(event.target.value)} /></label></div>
      <div className="continuity-heading"><strong>{status === "ACTIVE" && <b className="required-mark" aria-hidden="true">*</b>}Plan Conditions</strong><button type="button" className="close-button" onClick={() => setConditions((items) => [...items, { ...EMPTY_CONDITION }])}>Add Condition</button></div>{conditions.map((item, index) => <div className="trade-plan-condition-editor" key={`${index}-${item.conditionCode}`}><input required aria-label="Condition Code" placeholder="CONDITION_CODE" value={item.conditionCode} onChange={(event) => updateCondition(index, "conditionCode", event.target.value)} /><select required aria-label="Phase" value={item.phase} onChange={(event) => updateCondition(index, "phase", event.target.value)}>{["ENTRY", "SCALE", "EXIT", "INVALIDATION", "REVIEW"].map((option) => <option key={option}>{option}</option>)}</select><select required aria-label="Mode" value={item.mode} onChange={(event) => updateCondition(index, "mode", event.target.value)}>{["MANUAL", "MONITORABLE"].map((option) => <option key={option}>{option}</option>)}</select><select required aria-label="Severity" value={item.severity} onChange={(event) => updateCondition(index, "severity", event.target.value)}>{["INFO", "MEDIUM", "HIGH"].map((option) => <option key={option}>{option}</option>)}</select><textarea required aria-label="Condition Meaning" placeholder="Specific, understandable condition meaning" value={item.description} onChange={(event) => updateCondition(index, "description", event.target.value)} />{item.mode === "MONITORABLE" && <><select required aria-label="Fact Type" value={item.factType} onChange={(event) => updateCondition(index, "factType", event.target.value)}>{["PRICE", "VOLUME", "TECHNICAL", "FUNDAMENTAL", "COMPANY_EVENT", "MACRO", "SENTIMENT", "THESIS_STATE", "PORTFOLIO_RISK"].map((option) => <option key={option}>{option}</option>)}</select><input required aria-label="Metric Key" value={item.metricKey} onChange={(event) => updateCondition(index, "metricKey", event.target.value)} /><select required aria-label="Comparator" value={item.comparator} onChange={(event) => updateCondition(index, "comparator", event.target.value)}>{["GT", "GTE", "LT", "LTE", "EQ", "OCCURRED"].map((option) => <option key={option}>{option}</option>)}</select><input required={item.comparator !== "OCCURRED"} aria-label="Threshold" type="number" step="any" disabled={item.comparator === "OCCURRED"} value={item.threshold} onChange={(event) => updateCondition(index, "threshold", event.target.value)} /><input aria-label="Unit" value={item.unit} onChange={(event) => updateCondition(index, "unit", event.target.value)} /></>}<button type="button" className="close-button warning-text" onClick={() => setConditions((items) => items.filter((_, itemIndex) => itemIndex !== index))}>Remove</button></div>)}
      <div className="research-form-actions"><ActionButton busy={busy} onClick={() => { void propose(); }}>Propose Trade Plan Candidate</ActionButton><button type="button" className="close-button" onClick={() => setEditing(false)}>Cancel</button></div><p className="trade-plan-disclaimer">This only creates a pending candidate; explicitly confirm it in Review Queue above to make it a new version.</p></div>}
  </Card>;
}

function ResearchMemoryWorkspace({ subject, onWrite, busy }: { subject: Dict; onWrite: Write; busy: boolean }) {
  const subjectId = value(subject.subject_id, "");
  const [timeline, setTimeline] = useState<Dict | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"journal" | "decision" | null>(null);
  const [entryType, setEntryType] = useState("observation");
  const [decisionType, setDecisionType] = useState("research_more");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [expanded, setExpanded] = useState(false);

  async function load() {
    setLoading(true); setError(null);
    try {
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: "research_memory_get", arguments: { request: { operation: "timeline", case_id: subjectId, limit: 100 } } });
      setTimeline(envelopeData<Dict>((response.result ?? response) as Dict));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Failed to load timeline"); }
    finally { setLoading(false); }
  }

  useEffect(() => {
    setExpanded(false);
    setTimeline(null);
    setMode(null);
  }, [subjectId]);

  useEffect(() => {
    if (expanded && timeline === null && !loading) void load();
  }, [expanded, timeline, loading]);

  async function submit() {
    if (!title.trim() || !body.trim()) { window.alert("Title and content are required."); return; }
    const request = mode === "journal" ? { operation: "journal", case_id: subjectId, entry_type: entryType, title: title.trim(), body_markdown: body.trim(), authored_by: "user", confirmed_by: "user", instrument_ids: subject.primary_instrument_id ? [subject.primary_instrument_id] : [], topic_tags: [], idempotency_key: key("journal") } : { operation: "decision", case_id: subjectId, decision_type: decisionType, title: title.trim(), rationale: body.trim(), decided_at: new Date().toISOString(), decided_by: "user", confirmation_mode: "strict_review", primary_instrument_id: subject.primary_instrument_id ?? null, thesis_revision_ids: [], evidence_ids: [], report_ids: [], idempotency_key: key("decision") };
    await onWrite("research_memory_append", request, "research_memory_append");
    setMode(null); setTitle(""); setBody(""); await load();
  }

  const items = listOf<Dict>(timeline, "items");
  return <Card className={`research-memory-card research-collapsible-card${expanded ? " expanded" : ""}`} kicker="DURABLE MEMORY" title="Research History" subtitle="Timeline, journal entries, and confirmed decisions" action={<div className="research-detail-actions">{expanded && <><button className="close-button" type="button" onClick={() => setMode("journal")}>Add Journal Entry</button><button className="close-button" type="button" onClick={() => setMode("decision")}>Record Decision</button></>}<button className="research-collapse-toggle" type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>{expanded ? "Collapse" : `Expand${timeline ? ` · ${items.length}` : ""}`}<ChevronDown aria-hidden="true" /></button></div>}>
    {!expanded ? <p className="research-collapsed-hint">Durable journal entries, decisions, and the full subject timeline are hidden by default.</p> : <>
    {mode && <div className="continuity-editor"><div className="research-form-grid"><label className="research-field"><span><b className="required-mark" aria-hidden="true">*</b>{mode === "journal" ? "Entry Type" : "Decision Type"}</span>{mode === "journal" ? <select required value={entryType} onChange={(event) => setEntryType(event.target.value)}>{["note", "observation", "reflection", "postmortem", "question"].map((item) => <option key={item}>{item}</option>)}</select> : <select required value={decisionType} onChange={(event) => setDecisionType(event.target.value)}>{["watch", "no_action", "initiate_intent", "add_intent", "hold", "reduce_intent", "exit_intent", "avoid", "research_more"].map((item) => <option key={item}>{item}</option>)}</select>}</label><label className="research-field"><span><b className="required-mark" aria-hidden="true">*</b>Title</span><input required value={title} onChange={(event) => setTitle(event.target.value)} /></label><label className="research-field research-field-wide"><span><b className="required-mark" aria-hidden="true">*</b>{mode === "journal" ? "Body (Markdown)" : "Decision Rationale"}</span><textarea required rows={5} value={body} onChange={(event) => setBody(event.target.value)} /></label></div><div className="research-form-actions"><ActionButton busy={busy} onClick={() => { void submit(); }}>Save</ActionButton><button className="close-button" type="button" onClick={() => setMode(null)}>Cancel</button></div></div>}
    {error && <div className="inline-error">{error}</div>}{loading ? <Empty>Loading timeline…</Empty> : items.length === 0 ? <Empty>No timeline records yet.</Empty> : <div className="research-timeline">{items.map((item) => <article key={value(item.entity_id)}><div><Badge value={value(item.entity_type).toUpperCase()} /><time>{formatDate(item.occurred_at)}</time></div><strong>{value(item.title)}</strong><p>{value(item.summary)}</p><small className="mono">{value(item.entity_id)}{item.source_name ? ` · ${value(item.source_name)}` : ""}</small></article>)}</div>}
    </>}
  </Card>;
}

function WorkflowWorkspace({ subject }: { subject: Dict }) {
  const [running, setRunning] = useState<string | null>(null);
  const [result, setResult] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [topic, setTopic] = useState("");
  const [peers, setPeers] = useState("");
  const subjectId = value(subject.subject_id, "");

  async function run(operation: string) {
    setRunning(operation); setError(null);
    const request: Dict = { operation, idempotency_key: key(operation) };
    if (["deep_dive", "catalyst_review"].includes(operation)) { request.case_id = subjectId; request.lookback_days = 365; }
    if (operation === "catalyst_review" && topic.trim()) request.topic = topic.trim();
    if (operation === "peer_comparison") { request.primary_instrument_id = subject.primary_instrument_id; request.peer_instrument_ids = split(peers); request.period_mode = "annual"; request.periods = 3; request.include_valuation = true; request.include_operating_metrics = false; }
    if (operation === "portfolio_review") { request.account_snapshot_ids = []; request.risk_lookback_sessions = 126; request.max_risk_instruments = 12; }
    try {
      if (operation === "peer_comparison" && split(peers).length === 0) { throw new Error("Peer Comparison requires at least one same-market instrument."); }
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: "research_workflow_run", arguments: { request }, confirmation: "research_workflow_run" });
      setResult(response);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Workflow failed"); }
    finally { setRunning(null); }
  }
  return <Card className="research-workflow-card" kicker="FACT RECIPES · NO AUTO-JUDGMENT" title="Research Workflows"><div className="workflow-launchers"><article><strong>Instrument Research</strong><span>Fact packages are persisted; Thesis is not changed automatically.</span><label><small>Catalyst Topic</small><input value={topic} onChange={(event) => setTopic(event.target.value)} /></label><div><ActionButton busy={running === "deep_dive"} onClick={() => { void run("deep_dive"); }}>Deep Dive</ActionButton><ActionButton busy={running === "catalyst_review"} onClick={() => { void run("catalyst_review"); }}>Catalyst Review</ActionButton></div></article><article><strong>Peer Comparison</strong><span>Enter 1–5 same-market peers; they are not discovered or ranked automatically.</span><label><small><b className="required-mark" aria-hidden="true">*</b>Peer Instrument IDs</small><textarea required rows={2} value={peers} onChange={(event) => setPeers(event.target.value)} placeholder="equity:US:AMD, equity:US:INTC" /></label><ActionButton busy={running === "peer_comparison"} onClick={() => { void run("peer_comparison"); }}>Run Comparison</ActionButton></article><article><strong>Market & Portfolio</strong><span>Uses current Provider or persisted account facts; historical backtests are not run here.</span><div><ActionButton busy={running === "a_share_market_review"} onClick={() => { void run("a_share_market_review"); }}>A-share Review</ActionButton><ActionButton busy={running === "us_market_review"} onClick={() => { void run("us_market_review"); }}>US Review</ActionButton><ActionButton busy={running === "portfolio_review"} onClick={() => { void run("portfolio_review"); }}>Portfolio Review</ActionButton></div></article></div>{error && <div className="inline-error">{error}</div>}{result !== null && <details className="research-raw" open><summary>Workflow Receipt & Fact Package</summary><pre>{displayJson(result)}</pre></details>}</Card>;
}

function ChallengeWorkspace({ subject }: { subject: Dict }) {
  const [review, setReview] = useState<Dict | null>(null);
  const [reviewId, setReviewId] = useState("");
  const [trigger, setTrigger] = useState("discussion");
  const [proposedAction, setProposedAction] = useState("");
  const [resolution, setResolution] = useState("defer");
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function invoke(toolName: string, request: Dict, confirmation?: string) {
    setBusy(true); setError(null);
    try {
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: toolName, arguments: { request }, confirmation });
      const envelope = (response.result ?? response) as Dict;
      const data = envelopeData<Dict>(envelope);
      const next = data?.review && typeof data.review === "object" ? data.review as Dict : data;
      setReview(next ?? null);
      if (next?.review_id) setReviewId(String(next.review_id));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Challenge Review failed"); }
    finally { setBusy(false); }
  }
  return <Card className="research-challenge-card" kicker="STRICT REVIEW · NON-EXECUTING" title="Challenge Review"><div className="challenge-controls"><label><span>Review ID</span><input value={reviewId} onChange={(event) => setReviewId(event.target.value)} placeholder="challenge_<uuid7>" /></label><ActionButton busy={busy} onClick={() => { if (reviewId.trim()) void invoke("research_judgment_get", { operation: "challenge_review", review_id: reviewId.trim() }); }}>Load</ActionButton><label><span><b className="required-mark" aria-hidden="true">*</b>Trigger</span><select required value={trigger} onChange={(event) => setTrigger(event.target.value)}>{["discussion", "thesis_activation", "confidence_increase", "invalidation_relaxation", "position_intent", "contrary_evidence", "position_thesis_conflict", "stale_review", "confidence_without_evidence"].map((item) => <option key={item}>{item}</option>)}</select></label><label className="challenge-action"><span><b className="required-mark" aria-hidden="true">*</b>Proposed Action</span><input required value={proposedAction} onChange={(event) => setProposedAction(event.target.value)} /></label><ActionButton busy={busy} onClick={() => { if (!proposedAction.trim()) { window.alert("Enter a proposed action."); return; } void invoke("research_judgment_propose", { operation: "challenge_review", case_id: subject.subject_id, trigger, proposed_action: proposedAction.trim(), related_evidence_ids: [], idempotency_key: trigger === "discussion" ? null : key("challenge") }, "research_judgment_propose"); }}>Start Review</ActionButton></div>{error && <div className="inline-error">{error}</div>}{review && <div className="challenge-review"><header><div><strong>{value(review.review_id)}</strong><small>{value(review.trigger)} · {formatDate(review.created_at)}</small></div><Badge value={value(review.status).toUpperCase()} /></header><p>{value(review.proposed_action)}</p><div className="challenge-questions">{listOf<Dict>(review, "questions").map((question) => <div key={value(question.question_id)}><span>{value(question.dimension)}</span><p>{value(question.prompt)}</p></div>)}</div>{value(review.status, "").toLowerCase() === "open" && <div className="challenge-resolution"><label><span><b className="required-mark" aria-hidden="true">*</b>Resolution</span><select required value={resolution} onChange={(event) => setResolution(event.target.value)}>{["accept", "revise", "reject", "defer"].map((item) => <option key={item}>{item}</option>)}</select></label><label><span><b className="required-mark" aria-hidden="true">*</b>Resolution Rationale</span><input required value={rationale} onChange={(event) => setRationale(event.target.value)} /></label><ActionButton busy={busy} onClick={() => { if (!rationale.trim()) { window.alert("Enter a resolution rationale."); return; } void invoke("research_judgment_confirm", { operation: "challenge_review", review_id: review.review_id, resolution, rationale: rationale.trim(), confirmed_by: "user", idempotency_key: key("challenge-resolve") }, "research_judgment_confirm"); }}>Record Outcome</ActionButton></div>}</div>}</Card>;
}

export function ResearchContinuity({ activeModule, subject, state, onWrite, onRefresh, busy }: { activeModule: string; subject: Dict; state: Dict; onWrite: Write; onRefresh: () => void; busy: boolean }) {
  return <>
    <section id="research-panel-trade-plan" className="research-module-panel" role="tabpanel" aria-labelledby="research-tab-trade-plan" hidden={activeModule !== "trade-plan"}>
    <TradePlanWorkspace subject={subject} state={state} onWrite={onWrite} onRefresh={onRefresh} busy={busy} />
    </section>
    <section id="research-panel-history" className="research-module-panel" role="tabpanel" aria-labelledby="research-tab-history" hidden={activeModule !== "history"}>
    <ResearchMemoryWorkspace subject={subject} onWrite={onWrite} busy={busy} />
    </section>
    <section className="research-module-panel" aria-label="Thesis Challenge Review" hidden={activeModule !== "thesis"}>
    <ChallengeWorkspace subject={subject} />
    </section>
    <section id="research-panel-evidence" className="research-module-panel" role="tabpanel" aria-labelledby="research-tab-evidence" hidden={activeModule !== "evidence"}>
    <WorkflowWorkspace subject={subject} />
    </section>
  </>;
}
