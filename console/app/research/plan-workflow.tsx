"use client";

import { useState } from "react";
import { useApi, postApi, listOf } from "../lib/api";
import { asRecord, textStrict as text } from "../lib/coerce";
import { Badge, Button, Disclosure, ErrorNote, LinkButton, Table, displayJson, formatDate } from "../components/ui";

type Dict = Record<string, unknown>;

export function PlanWorkflow({ subjectId, plan, onEdit }: { subjectId: string; plan: Dict; onEdit: () => void }) {
  const planId = text(plan.plan_id);
  const version = Number(plan.version);
  const query = useApi<{ data: Dict }>(`/api/research/${encodeURIComponent(subjectId)}/plans/${encodeURIComponent(planId)}/workflow?version=${version}`);
  const [choice, setChoice] = useState("");
  const [checking, setChecking] = useState(false);
  const [risk, setRisk] = useState<Dict | null>(null);
  const [error, setError] = useState<string | null>(null);
  const workflow = query.data?.data;
  const coverage = asRecord(workflow?.coverage);
  const thesis = asRecord(workflow?.thesis);
  const journal = `/decision-workbench?subject_id=${encodeURIComponent(subjectId)}`;
  async function checkReadiness() {
    setChecking(true); setError(null); setRisk(null);
    try {
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: "portfolio_risk_get", arguments: { request: { operation: "check", trade_plan_id: planId, refresh_accounts: false } }, preserve_full_result: true });
      const envelope = asRecord(response.result ?? response);
      const data = asRecord(envelope.data);
      const sizing = asRecord(data.position_sizing);
      if (envelope.ok === false) throw new Error("Readiness unavailable. Review the Plan and durable account snapshots.");
      if (sizing.plan_id !== planId || Number(sizing.plan_version) !== version) throw new Error("Plan version changed. Reload Research before checking readiness again.");
      setRisk(data);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Readiness unavailable"); }
    finally { setChecking(false); }
  }
  return <section aria-label="Plan Dependency Review">
    <h3>Dependency Review · Plan v{version}</h3>
    <p>Confirmed Plan and Monitor changes are separate. Existing rules keep their exact binding until you explicitly edit or pause them.</p>
    {query.loading ? <p>Reading dependencies…</p> : query.error ? <><ErrorNote>Dependencies unavailable; the confirmed Plan remains saved.</ErrorNote><Button onClick={query.refresh}>Reload Dependencies</Button></> : workflow ? <>
      <p>Read at {formatDate(workflow.as_of)}. Previous Plan: {workflow.previous_version == null ? "Unavailable / first version" : `v${workflow.previous_version}`}.</p>
      {workflow.previous_version_available === false && <ErrorNote>Prior Plan version unavailable; differences are incomplete.</ErrorNote>}
      <p>Thesis revision at Plan creation: {text(thesis.revision_at_plan_creation) || "Unavailable"}; current: {text(thesis.current_revision_id) || "Unavailable"}. {thesis.review_needed === true ? "Review the Plan against the revised Thesis." : "No later Thesis revision found."} This comparison uses confirmation times; it does not prove a review.</p>
      <Button onClick={onEdit}>Review Plan in Editor</Button>{" "}<Button onClick={query.refresh}>Reload Dependencies</Button>
      <Disclosure title="Plan Field and Condition Differences"><pre>{displayJson({ fields: workflow.field_changes, conditions: workflow.condition_changes })}</pre></Disclosure>
      <h4>Exact Version Coverage</h4>
      <p>Configured means an active, unexpired rule matches this condition. Observed also requires a usable, sufficiently recent fact. It does not mean the condition passed.</p>
      {coverage.unavailable === true && <ErrorNote>Some Monitor reads are unavailable; coverage is incomplete.</ErrorNote>}
      <Table><thead><tr><th>Condition</th><th>State</th><th>Configured</th><th>Usable Observation</th></tr></thead><tbody>{listOf<Dict>(coverage, "conditions").map(c => <tr key={text(c.condition_code)}><td>{text(c.condition_code)}</td><td><Badge value={text(c.state)} /></td><td>{c.configured ? "Yes" : "No"}</td><td>{c.observed ? "Yes" : "No"}</td></tr>)}</tbody></Table>
      <Disclosure title="Coverage Source References"><pre>{displayJson(coverage.source_refs)}</pre></Disclosure>
      {workflow.monitors_available === false && <ErrorNote>Monitor bindings unavailable. No absence of dependencies can be inferred.</ErrorNote>}
      {listOf<Dict>(workflow, "monitors").map(m => <p key={text(m.monitor_id)}>{text(m.monitor_id)} v{String(m.version)} → Plan v{String(m.plan_version)} · {text(m.status)} · {text(m.binding)} <LinkButton href={`/monitors?monitor_id=${encodeURIComponent(text(m.monitor_id))}&version=${Number(m.version)}`}>Review Rules or Pause</LinkButton></p>)}
      <Button onClick={() => setChoice("Keeping existing bindings for now. This choice is local to this view; no Monitor was changed.")}>Keep Existing Rules</Button>{" "}
      <Button onClick={() => setChoice("Dependency review deferred in this view. No reminder or durable review record was created.")}>Defer Review</Button>
      {choice && <p role="status">{choice}</p>}
      {listOf<Dict>(coverage, "conditions").some(c => c.state === "MANUAL") && <p>MANUAL conditions need human review before acting on this Plan and when relevant evidence changes. <LinkButton href={`${journal}&capture=decision`}>Record Manual Review</LinkButton>{" "}<LinkButton href="/agenda">Choose a Review Reminder</LinkButton></p>}
    </> : null}
    <h3>Execution Readiness</h3>
    <p>Check sizing and risk from durable snapshots when you are considering execution. Each broker preview and submit still requires its own exact review.</p>
    <Button disabled={checking || plan.status !== "ACTIVE"} onClick={() => { void checkReadiness(); }}>{checking ? "Checking…" : "Check Sizing and Risk"}</Button>
    {error && <ErrorNote>{error}</ErrorNote>}
    {risk && <><p>Risk: <Badge value={text(risk.overall_status)} /> · checked {formatDate(risk.as_of)}. This is a calculation, not order authorization.</p><Table><thead><tr><th>Constraint</th><th>Status</th><th>Explanation</th></tr></thead><tbody>{listOf<Dict>(asRecord(risk.position_sizing), "constraints").map(c => <tr key={text(c.constraint_code)}><td>{text(c.constraint_code)}</td><td><Badge value={text(c.status)} /></td><td>{text(c.message)}</td></tr>)}</tbody></Table><p>Data gaps: {listOf<string>(asRecord(risk.position_sizing), "data_quality_codes").join(" · ") || "No sizing gap codes returned"}.</p><Disclosure title="Sizing, Blockers and Snapshot Basis"><pre>{displayJson(risk)}</pre></Disclosure></>}
    <p>Missing or stale NAV/cash: open Portfolio and explicitly choose Sync Accounts. Missing or stale Plan price/stop: review and confirm a revised Plan before checking again.</p>
    <LinkButton href="/portfolio">Review Accounts and Refresh Options</LinkButton>{" "}
    <LinkButton href={`/capabilities?workflow=order-preview&subject_id=${encodeURIComponent(subjectId)}&plan_id=${encodeURIComponent(planId)}&plan_version=${version}`}>Prepare Separate Order Preview</LinkButton>
    <h4>Execution and Reconciliation</h4>
    <p>UNKNOWN submit outcome: inspect the existing intent and broker status; never submit again. Partial fills: reconcile filled quantity and remaining order separately. External or unattributed activity stays execution-only until you explicitly link it to reviewed intent.</p>
    <LinkButton href={`${journal}#timeline`}>Review Orders and Activity Timeline</LinkButton>{" "}<LinkButton href={`${journal}#cycles`}>Review Fills and Unattributed Activity</LinkButton>
  </section>;
}
