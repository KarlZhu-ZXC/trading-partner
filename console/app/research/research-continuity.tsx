"use client";

import { useEffect, useState } from "react";
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
  return value.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean);
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
  if (condition.mode === "MANUAL") return "人工复核";
  const comparator = ({ GT: ">", GTE: "≥", LT: "<", LTE: "≤", EQ: "=", OCCURRED: "已发生" } as Record<string, string>)[value(condition.comparator, "")];
  return comparator === "已发生"
    ? `${value(condition.fact_type)} · ${value(condition.metric_key)} · 已发生`
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
      window.alert("Thesis、标的、参考价格、仓位约束、风险预算和备注均为必填。");
      return;
    }
    if (status === "ACTIVE" && conditions.length === 0) {
      window.alert("ACTIVE Trade Plan 至少需要一个条件。");
      return;
    }
    const invalid = conditions.find((item) => !item.conditionCode.trim() || !item.description.trim() || (item.mode === "MONITORABLE" && (!item.metricKey.trim() || (item.comparator !== "OCCURRED" && !item.threshold))));
    if (invalid) { window.alert("每个条件都需要机器代码和具体释义；可监控条件还需完整事实比较。 "); return; }
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
    await onWrite("research_judgment_propose", { operation: "research_state", case_id: value(subject.subject_id), payload, proposed_by: "user", proposed_by_rationale: "由本地 Research 工作区提出 Trade Plan 版本", idempotency_key: key("trade-plan") }, "research_judgment_propose");
    setEditing(false);
    onRefresh();
  }

  const compared = plans.find((item) => String(item.version) === compareVersion) ?? null;
  const diffRows = current && compared ? [
    ["Status", compared.status, current.status], ["Reference", compared.reference_price, current.reference_price], ["Target %", compared.target_position_percent, current.target_position_percent], ["Max %", compared.max_position_percent, current.max_position_percent], ["Risk %", compared.risk_budget_percent, current.risk_budget_percent], ["Stop", compared.stop_price, current.stop_price], ["Conditions", listOf<Dict>(compared, "conditions").length, listOf<Dict>(current, "conditions").length],
  ] : [];

  return <Card className="research-trade-plan-card" kicker="POSITION INTENT · VERSIONED · NON-EXECUTING" title="Trade Plan" action={<div className="research-detail-actions">{current && <Badge value={`V${value(current.version)} · ${value(current.status)}`} />}<ActionButton onClick={openEditor}>{current ? "提出新版本" : "新建 Trade Plan"}</ActionButton></div>}>
    {current ? <>
      <div className="trade-plan-identity"><div><span>标的</span><strong>{shortId(current.instrument_id)}</strong><small>{value(current.instrument_id)}</small></div><div><span>Plan ID</span><strong>{value(current.plan_id)}</strong><small>{plans.length} 个持久化版本</small></div><div><span>Thesis</span><strong>{shortId(current.thesis_id)}</strong><small>{value(current.thesis_id)}</small></div><div><span>确认</span><strong>{formatDate(current.created_at)}</strong><small>{value(current.confirmed_by)}</small></div></div>
      <div className="trade-plan-metrics"><div className="trade-plan-price"><span>参考价格</span><strong>{value(current.currency, "")} {formatDecimal(current.reference_price, 4)}</strong><small>{formatDate(current.reference_price_at)}</small></div><div><span>目标仓位</span><strong>{formatDecimal(current.target_position_percent, 2)}%</strong></div><div><span>最大仓位</span><strong>{formatDecimal(current.max_position_percent, 2)}%</strong></div><div><span>风险预算</span><strong>{formatDecimal(current.risk_budget_percent, 2)}%</strong></div><div><span>止损价</span><strong>{current.stop_price == null ? "—" : formatDecimal(current.stop_price, 4)}</strong></div></div>
      <div className="trade-plan-conditions">{listOf<Dict>(current, "conditions").map((condition) => <article key={value(condition.condition_code)}><header><span className="mono">{value(condition.condition_code)}</span><span className="trade-plan-mode">{value(condition.phase)} · {value(condition.mode)}</span></header><strong>{value(condition.description)}</strong><small>{planCondition(condition)}</small></article>)}</div>
      {plans.length > 1 && <div className="trade-plan-diff"><label><span>与历史版本比较</span><select value={compareVersion} onChange={(event) => setCompareVersion(event.target.value)}><option value="">选择版本</option>{plans.filter((item) => item.version !== current.version).map((item) => <option value={String(item.version)} key={String(item.version)}>v{value(item.version)} · {formatDate(item.created_at)}</option>)}</select></label>{diffRows.length > 0 && <table><thead><tr><th>字段</th><th>历史 v{compareVersion}</th><th>当前 v{value(current.version)}</th></tr></thead><tbody>{diffRows.map(([label, before, after]) => <tr key={String(label)}><th>{String(label)}</th><td>{value(before)}</td><td className={String(before) !== String(after) ? "changed" : ""}>{value(after)}</td></tr>)}</tbody></table>}</div>}
    </> : <div className="research-trade-plan-empty"><strong>暂无已确认 Trade Plan</strong><span>先选择 live Thesis，再提出第一个非执行性计划候选。</span></div>}
    {editing && <div className="continuity-editor"><div className="research-form-grid"><label className="research-field"><span>绑定 Thesis</span><select value={thesisId} onChange={(event) => setThesisId(event.target.value)}><option value="">请选择</option>{liveTheses.map((item) => <option value={value(item.thesis_id)} key={value(item.thesis_id)}>{value(item.title)} · {shortId(item.thesis_id)}</option>)}</select></label><label className="research-field"><span>计划状态</span><select value={status} onChange={(event) => setStatus(event.target.value)}>{["DRAFT", "ACTIVE", "PAUSED", "ARCHIVED"].map((item) => <option key={item}>{item}</option>)}</select></label><label className="research-field"><span>执行/持仓标的</span><input value={instrument} onChange={(event) => setInstrument(event.target.value)} /></label><label className="research-field"><span>币种</span><input value={currency} onChange={(event) => setCurrency(event.target.value)} /></label><label className="research-field"><span>参考价格</span><input type="number" step="any" value={referencePrice} onChange={(event) => setReferencePrice(event.target.value)} /></label><label className="research-field"><span>参考价格时间</span><input type="datetime-local" value={referenceAt} onChange={(event) => setReferenceAt(event.target.value)} /></label><label className="research-field"><span>有效起点</span><input type="datetime-local" value={validFrom} onChange={(event) => setValidFrom(event.target.value)} /></label><label className="research-field"><span>有效截止（可空）</span><input type="datetime-local" value={validUntil} onChange={(event) => setValidUntil(event.target.value)} /></label><label className="research-field"><span>目标仓位 %</span><input type="number" min="0" max="100" step="any" value={target} onChange={(event) => setTarget(event.target.value)} /></label><label className="research-field"><span>最大仓位 %</span><input type="number" min="0" max="100" step="any" value={maximum} onChange={(event) => setMaximum(event.target.value)} /></label><label className="research-field"><span>风险预算 %</span><input type="number" min="0" max="100" step="any" value={riskBudget} onChange={(event) => setRiskBudget(event.target.value)} /></label><label className="research-field"><span>止损价（可空）</span><input type="number" step="any" value={stop} onChange={(event) => setStop(event.target.value)} /></label><label className="research-field research-field-wide"><span>计划备注</span><textarea rows={4} value={notes} onChange={(event) => setNotes(event.target.value)} /></label></div>
      <div className="continuity-heading"><strong>计划条件</strong><button type="button" className="close-button" onClick={() => setConditions((items) => [...items, { ...EMPTY_CONDITION }])}>添加条件</button></div>{conditions.map((item, index) => <div className="trade-plan-condition-editor" key={`${index}-${item.conditionCode}`}><input aria-label="条件代码" placeholder="CONDITION_CODE" value={item.conditionCode} onChange={(event) => updateCondition(index, "conditionCode", event.target.value)} /><select aria-label="阶段" value={item.phase} onChange={(event) => updateCondition(index, "phase", event.target.value)}>{["ENTRY", "SCALE", "EXIT", "INVALIDATION", "REVIEW"].map((option) => <option key={option}>{option}</option>)}</select><select aria-label="模式" value={item.mode} onChange={(event) => updateCondition(index, "mode", event.target.value)}>{["MANUAL", "MONITORABLE"].map((option) => <option key={option}>{option}</option>)}</select><select aria-label="严重度" value={item.severity} onChange={(event) => updateCondition(index, "severity", event.target.value)}>{["INFO", "MEDIUM", "HIGH"].map((option) => <option key={option}>{option}</option>)}</select><textarea aria-label="条件释义" placeholder="具体、可理解的条件含义" value={item.description} onChange={(event) => updateCondition(index, "description", event.target.value)} />{item.mode === "MONITORABLE" && <><select aria-label="事实类型" value={item.factType} onChange={(event) => updateCondition(index, "factType", event.target.value)}>{["PRICE", "VOLUME", "TECHNICAL", "FUNDAMENTAL", "COMPANY_EVENT", "MACRO", "SENTIMENT", "THESIS_STATE", "PORTFOLIO_RISK"].map((option) => <option key={option}>{option}</option>)}</select><input aria-label="指标键" value={item.metricKey} onChange={(event) => updateCondition(index, "metricKey", event.target.value)} /><select aria-label="比较符" value={item.comparator} onChange={(event) => updateCondition(index, "comparator", event.target.value)}>{["GT", "GTE", "LT", "LTE", "EQ", "OCCURRED"].map((option) => <option key={option}>{option}</option>)}</select><input aria-label="阈值" type="number" step="any" disabled={item.comparator === "OCCURRED"} value={item.threshold} onChange={(event) => updateCondition(index, "threshold", event.target.value)} /><input aria-label="单位" value={item.unit} onChange={(event) => updateCondition(index, "unit", event.target.value)} /></>}<button type="button" className="close-button warning-text" onClick={() => setConditions((items) => items.filter((_, itemIndex) => itemIndex !== index))}>移除</button></div>)}
      <div className="research-form-actions"><ActionButton busy={busy} onClick={() => { void propose(); }}>提出 Trade Plan 候选</ActionButton><button type="button" className="close-button" onClick={() => setEditing(false)}>取消</button></div><p className="trade-plan-disclaimer">这里只生成 pending candidate；必须在上方 Review Queue 显式确认后才成为新版本。</p></div>}
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

  async function load() {
    setLoading(true); setError(null);
    try {
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: "research_memory_get", arguments: { request: { operation: "timeline", case_id: subjectId, limit: 100 } } });
      setTimeline(envelopeData<Dict>((response.result ?? response) as Dict));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Timeline 读取失败"); }
    finally { setLoading(false); }
  }

  useEffect(() => { void load(); }, [subjectId]);

  async function submit() {
    if (!title.trim() || !body.trim()) { window.alert("标题与内容不能为空。"); return; }
    const request = mode === "journal" ? { operation: "journal", case_id: subjectId, entry_type: entryType, title: title.trim(), body_markdown: body.trim(), authored_by: "user", confirmed_by: "user", instrument_ids: subject.primary_instrument_id ? [subject.primary_instrument_id] : [], topic_tags: [], idempotency_key: key("journal") } : { operation: "decision", case_id: subjectId, decision_type: decisionType, title: title.trim(), rationale: body.trim(), decided_at: new Date().toISOString(), decided_by: "user", confirmation_mode: "strict_review", primary_instrument_id: subject.primary_instrument_id ?? null, thesis_revision_ids: [], evidence_ids: [], report_ids: [], idempotency_key: key("decision") };
    await onWrite("research_memory_append", request, "research_memory_append");
    setMode(null); setTitle(""); setBody(""); await load();
  }

  const items = listOf<Dict>(timeline, "items");
  return <Card className="research-memory-card" kicker="DURABLE MEMORY" title="Timeline、Journal 与 Decision" action={<div className="research-detail-actions"><button className="close-button" type="button" onClick={() => setMode("journal")}>记 Journal</button><button className="close-button" type="button" onClick={() => setMode("decision")}>记 Decision</button></div>}>
    {mode && <div className="continuity-editor"><div className="research-form-grid"><label className="research-field"><span>{mode === "journal" ? "Entry type" : "Decision type"}</span>{mode === "journal" ? <select value={entryType} onChange={(event) => setEntryType(event.target.value)}>{["note", "observation", "reflection", "postmortem", "question"].map((item) => <option key={item}>{item}</option>)}</select> : <select value={decisionType} onChange={(event) => setDecisionType(event.target.value)}>{["watch", "no_action", "initiate_intent", "add_intent", "hold", "reduce_intent", "exit_intent", "avoid", "research_more"].map((item) => <option key={item}>{item}</option>)}</select>}</label><label className="research-field"><span>标题</span><input value={title} onChange={(event) => setTitle(event.target.value)} /></label><label className="research-field research-field-wide"><span>{mode === "journal" ? "正文（Markdown）" : "判断依据"}</span><textarea rows={5} value={body} onChange={(event) => setBody(event.target.value)} /></label></div><div className="research-form-actions"><ActionButton busy={busy} onClick={() => { void submit(); }}>确认写入</ActionButton><button className="close-button" type="button" onClick={() => setMode(null)}>取消</button></div></div>}
    {error && <div className="inline-error">{error}</div>}{loading ? <Empty>正在读取统一 Timeline…</Empty> : items.length === 0 ? <Empty>暂无 Timeline 记录。</Empty> : <div className="research-timeline">{items.map((item) => <article key={value(item.entity_id)}><div><Badge value={value(item.entity_type).toUpperCase()} /><time>{formatDate(item.occurred_at)}</time></div><strong>{value(item.title)}</strong><p>{value(item.summary)}</p><small className="mono">{value(item.entity_id)}{item.source_name ? ` · ${value(item.source_name)}` : ""}</small></article>)}</div>}
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
      if (operation === "peer_comparison" && split(peers).length === 0) { throw new Error("Peer Comparison 至少填写一个同市场标的。"); }
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: "research_workflow_run", arguments: { request }, confirmation: "research_workflow_run" });
      setResult(response);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Workflow 运行失败"); }
    finally { setRunning(null); }
  }
  return <Card className="research-workflow-card" kicker="FACT RECIPES · NO AUTO-JUDGMENT" title="Research Workflows"><div className="workflow-launchers"><article><strong>标的研究</strong><span>事实包会持久化，但不会自动改 Thesis。</span><label><small>Catalyst topic（可空）</small><input value={topic} onChange={(event) => setTopic(event.target.value)} /></label><div><ActionButton busy={running === "deep_dive"} onClick={() => { void run("deep_dive"); }}>Deep Dive</ActionButton><ActionButton busy={running === "catalyst_review"} onClick={() => { void run("catalyst_review"); }}>Catalyst Review</ActionButton></div></article><article><strong>Peer Comparison</strong><span>显式填写 1–5 个同市场 peer，不自动发现或排序。</span><label><small>Peer Instrument IDs</small><textarea rows={2} value={peers} onChange={(event) => setPeers(event.target.value)} placeholder="equity:US:AMD, equity:US:INTC" /></label><ActionButton busy={running === "peer_comparison"} onClick={() => { void run("peer_comparison"); }}>运行比较</ActionButton></article><article><strong>市场与组合</strong><span>使用当前 Provider 或持久化账户事实；历史回测仍不在这里执行。</span><div><ActionButton busy={running === "a_share_market_review"} onClick={() => { void run("a_share_market_review"); }}>A-share Review</ActionButton><ActionButton busy={running === "us_market_review"} onClick={() => { void run("us_market_review"); }}>US Review</ActionButton><ActionButton busy={running === "portfolio_review"} onClick={() => { void run("portfolio_review"); }}>Portfolio Review</ActionButton></div></article></div>{error && <div className="inline-error">{error}</div>}{result !== null && <details className="research-raw" open><summary>Workflow 回执与事实包</summary><pre>{displayJson(result)}</pre></details>}</Card>;
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
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: toolName, arguments: toolName === "challenge_review_get" ? request : { request }, confirmation });
      const envelope = (response.result ?? response) as Dict;
      const data = envelopeData<Dict>(envelope);
      const next = data?.review && typeof data.review === "object" ? data.review as Dict : data;
      setReview(next ?? null);
      if (next?.review_id) setReviewId(String(next.review_id));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Challenge Review 操作失败"); }
    finally { setBusy(false); }
  }
  return <Card className="research-challenge-card" kicker="STRICT REVIEW · NON-EXECUTING" title="Challenge Review"><div className="challenge-controls"><label><span>Review ID</span><input value={reviewId} onChange={(event) => setReviewId(event.target.value)} placeholder="challenge_<uuid7>" /></label><ActionButton busy={busy} onClick={() => { if (reviewId.trim()) void invoke("challenge_review_get", { review_id: reviewId.trim() }); }}>读取</ActionButton><label><span>触发原因</span><select value={trigger} onChange={(event) => setTrigger(event.target.value)}>{["discussion", "thesis_activation", "confidence_increase", "invalidation_relaxation", "position_intent", "contrary_evidence", "position_thesis_conflict", "stale_review", "confidence_without_evidence"].map((item) => <option key={item}>{item}</option>)}</select></label><label className="challenge-action"><span>拟议动作</span><input value={proposedAction} onChange={(event) => setProposedAction(event.target.value)} /></label><ActionButton busy={busy} onClick={() => { if (!proposedAction.trim()) { window.alert("请填写拟议动作。"); return; } void invoke("challenge_review_manage", { operation: "start", case_id: subject.subject_id, trigger, proposed_action: proposedAction.trim(), related_evidence_ids: [], idempotency_key: trigger === "discussion" ? null : key("challenge") }, "challenge_review_manage"); }}>开始 Review</ActionButton></div>{error && <div className="inline-error">{error}</div>}{review && <div className="challenge-review"><header><div><strong>{value(review.review_id)}</strong><small>{value(review.trigger)} · {formatDate(review.created_at)}</small></div><Badge value={value(review.status).toUpperCase()} /></header><p>{value(review.proposed_action)}</p><div className="challenge-questions">{listOf<Dict>(review, "questions").map((question) => <div key={value(question.question_id)}><span>{value(question.dimension)}</span><p>{value(question.prompt)}</p></div>)}</div>{value(review.status, "").toLowerCase() === "open" && <div className="challenge-resolution"><select value={resolution} onChange={(event) => setResolution(event.target.value)}>{["accept", "revise", "reject", "defer"].map((item) => <option key={item}>{item}</option>)}</select><input value={rationale} onChange={(event) => setRationale(event.target.value)} placeholder="解决依据（必填）" /><ActionButton busy={busy} onClick={() => { if (!rationale.trim()) { window.alert("请填写解决依据。"); return; } if (window.confirm("确认记录这个非执行性 Review 结论？")) void invoke("challenge_review_manage", { operation: "resolve", review_id: review.review_id, resolution, rationale: rationale.trim(), confirmed_by: "user", idempotency_key: key("challenge-resolve") }, "challenge_review_manage"); }}>记录结论</ActionButton></div>}</div>}</Card>;
}

export function ResearchContinuity({ subject, state, onWrite, onRefresh, busy }: { subject: Dict; state: Dict; onWrite: Write; onRefresh: () => void; busy: boolean }) {
  return <>
    <TradePlanWorkspace subject={subject} state={state} onWrite={onWrite} onRefresh={onRefresh} busy={busy} />
    <ResearchMemoryWorkspace subject={subject} onWrite={onWrite} busy={busy} />
    <ChallengeWorkspace subject={subject} />
    <WorkflowWorkspace subject={subject} />
  </>;
}
