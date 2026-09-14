"use client";

import { Badge, DescriptionList, Disclosure } from "./ui";
import type { AgentReceipt, CopilotResearch, CopilotResearchProfile } from "../lib/agent-api";
import styles from "./copilot-research-progress.module.css";

const labels: Record<string, string> = { READ: "Gather Evidence", SYNTHESIZE: "Build Answer", CHALLENGE: "Counter-review", EVIDENCE_CHECK: "Check Evidence", COMPLETED: "Completed", TIME_BUDGET: "Time budget reached", MODEL_BUDGET: "Model call budget reached", TOOL_BUDGET: "Tool call budget reached", ROUND_LIMIT: "Round limit reached", EVIDENCE_GAP: "Evidence gap", CANCELLED: "Cancelled", FAILED: "Failed" };

export function CopilotResearchProgress({ research, recoveredStatus, receipts = [], usage }: { research: CopilotResearch; recoveredStatus?: string; receipts?: AgentReceipt[]; usage?: { input_tokens?: unknown; output_tokens?: unknown } }) {
  const recovered = Boolean(recoveredStatus);
  const modelUnknown = recovered || research.gaps.includes("MODEL_USAGE_UNAVAILABLE") || research.gaps.includes("MODEL_PROGRESS_UNAVAILABLE");
  const progressUnknown = recovered || research.gaps.includes("MODEL_PROGRESS_UNAVAILABLE");
  const tokenCount = (value: unknown) => !recovered && typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
  const inputTokens = tokenCount(usage?.input_tokens);
  const outputTokens = tokenCount(usage?.output_tokens);
  const usageCoverage = inputTokens === null && outputTokens === null ? "Unavailable" : research.usage_complete && inputTokens !== null && outputTokens !== null ? "Complete" : "Partial";
  return <section aria-label="Research Progress" className={styles.progress}>
    <header className={styles.heading}><strong>{research.mode === "challenge" ? "Counter-review" : "Research"}</strong><Badge value={recoveredStatus ?? research.phase} /></header>
    <ul aria-label="Research Focus"><li>Which sourced facts answer the question?</li><li>What evidence gaps or conflicts remain?</li><li>What evidence supports the conclusion?</li></ul>
    {recovered ? <p>Progress and model usage unavailable after recovery. Saved tool receipts below show completed steps; the original budget is retained.</p> : <ol className={styles.steps}>{research.steps.map((step) => <li key={step.code}><span>{labels[step.code]}</span><Badge value={step.status} /></li>)}</ol>}
    <DescriptionList columns={2} items={[
      { label: "Model Calls", value: `${modelUnknown ? "Unavailable" : research.model_calls_attempted} / ${research.max_model_calls}` },
      { label: "Tool Calls", value: `${progressUnknown ? "Unavailable" : research.tool_calls_attempted} / ${research.max_tool_calls}` },
      { label: "Elapsed", value: `${progressUnknown ? "Unavailable" : `${(research.elapsed_ms / 1000).toFixed(1)}s`} / ${research.max_seconds}s` },
      { label: "Cost", value: "Unavailable" },
      { label: "Reported Input Tokens", value: inputTokens ?? "Unavailable" },
      { label: "Reported Output Tokens", value: outputTokens ?? "Unavailable" },
      { label: "Usage Coverage", value: usageCoverage },
      { label: "Completed Reads", value: recovered ? "See saved receipts" : research.completed_reads },
      { label: "Evidence", value: recovered ? "Unavailable" : research.evidence_status.replaceAll("_", " ") },
    ]} />
    {!recovered && research.phase === "FINISHED" && research.evidence_status !== "NOT_CHECKED" && <DescriptionList columns={2} items={[{ label: "Verified Claims", value: research.verified_claims }, { label: "Blocked Claims", value: research.blocked_claims }]} />}
    <p>Token values include only reported usage. Claim counts describe the evidence check; they do not establish that every statement is verified. Provider internal attempts and cost are unavailable.</p>
    {research.stop_reason && !recovered && <p role="status"><strong>{labels[research.stop_reason]}</strong></p>}
    {research.gaps.length > 0 && !recovered && <p>Evidence gaps: {research.gaps.join(" · ")}</p>}
    {recovered && receipts.length > 0 && <Disclosure title={`Saved Tool Steps · ${receipts.length}`} variant="compact"><ol>{receipts.map((receipt) => <li key={receipt.receipt_id}>{receipt.capability} · {receipt.operation} · {receipt.error_codes.length ? receipt.error_codes.join(" · ") : "Recorded"}</li>)}</ol></Disclosure>}
  </section>;
}


export function CopilotResearchProfiles({ profiles }: { profiles: CopilotResearchProfile[] }) {
  if (profiles.length === 0) return null;
  return <Disclosure title="Research Usage Profiles" variant="compact"><p>Descriptive samples from different prompts and evidence needs, not model rankings. Token totals include only reported usage; cost remains unavailable.</p>{profiles.slice(0, 20).map((profile, index) => <section key={index} className={styles.progress}><strong>{profile.provider ?? "Unknown Provider"} · {profile.model ?? "Unknown Model"} · {profile.reasoning_effort ?? "Provider Default"} · {profile.mode}</strong><p>Budget: {profile.max_seconds}s · {profile.max_model_calls} model calls · {profile.max_tool_calls} tool calls</p><DescriptionList columns={2} items={[{ label: "Samples / Completed", value: `${profile.sample_count} / ${profile.completed_count}` }, { label: "Elapsed P50 / P95", value: `${(profile.p50_elapsed_ms / 1000).toFixed(1)}s / ${(profile.p95_elapsed_ms / 1000).toFixed(1)}s` }, { label: "Reported Input Tokens", value: profile.input_tokens ?? "Unavailable" }, { label: "Reported Output Tokens", value: profile.output_tokens ?? "Unavailable" }, { label: "Usage Coverage", value: profile.usage_complete ? "Complete" : "Partial" }, { label: "Cost", value: "Unavailable" }]} /></section>)}</Disclosure>;
}
