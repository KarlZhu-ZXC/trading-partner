"use client";

import { useState } from "react";
import { Badge, Disclosure, FormField, LinkButton, Select, Table, formatDate } from "../components/ui";
import { listOf } from "../lib/api";
import { asObject, textStringified as text } from "../lib/coerce";

import { orderedFollowups } from "../lib/retro-review-presentation";
import { HistoricalReview } from "./retro-comparison";

type Dict = Record<string, unknown>;
export type FollowupEdit = { status: string; note: string; followup?: string; question_id?: string | null; question_version?: number | null };
const LABELS: Record<string, string> = {
  recorded_trade_references: "Trade references in original run",
  resolved_trade_count: "Unambiguously resolved trades",
  unique_active_snapshot_match_count: "Trades with one active pre-period Plan match",
  compatible_preperiod_decision_count: "Trades with compatible pre-period Decision",
  unmatched_snapshot_trade_count: "Trades without a pre-period Plan match",
  ambiguous_snapshot_trade_count: "Trades with multiple pre-period Plan matches",
  currently_unlinked_trade_count: "Trades currently without a Decision/Plan link",
  new_plan_versions_during_period: "Plan versions created during period (separate cohort)",
};
export function RetroCoverage({ data }: { data: Dict }) {
  const metrics = asObject(data.metrics);
  return <section aria-label="Retro Coverage Basis"><FollowupProgress data={asObject(data.followup_progress)} /><Disclosure title="Coverage and Timing">
    <p>Original run: {text(data.original_run_status)}. This read-time projection uses the original trade references and pre-period snapshot. Instrument matches do not prove attribution or adherence. Plans created later never increase pre-period coverage.</p>
    <Table><thead><tr><th>Cohort / Measure</th><th>Count</th></tr></thead><tbody>{Object.entries(LABELS).map(([key, label]) => <tr key={key}><td>{label}</td><td>{metrics[key] == null ? "Unknown" : String(metrics[key])}</td></tr>)}</tbody></Table>
    <p>Trade counts share the original run’s reference cohort; Plan-version counts have a separate denominator. Current links may be retrospective. Counts are not quality scores or proof of complete broker history.</p>
    {listOf<string>(data, "warning_codes").map(code => <Badge key={code} value={code} />)}
  </Disclosure></section>;
}
export function FindingFollowup({ runId, findingKey, data, edit, onChange }: { runId: string; findingKey: string; data: Dict | undefined; edit: FollowupEdit; onChange: (patch: Partial<FollowupEdit>) => void }) {
  const [subjectId, setSubjectId] = useState("");
  const subjects = listOf<Dict>(data, "subjects");
  const selected = subjects.find(s => text(s.subject_id) === subjectId);
  const questions: Dict[] = subjects.flatMap(s => listOf<Dict>(s, "questions").map(q => ({ ...q, subject_title: text(s.title), subject_id: text(s.subject_id) })));
  return <section aria-label="Finding Follow-Up">
    <FormField label="Follow-Up Choice"><Select value={edit.followup ?? ""} onChange={e => onChange({ followup: e.target.value, question_id: null, question_version: null })}><option value="">No choice recorded</option><option value="QUESTION">Covered by a Confirmed Question</option><option value="NO_FOLLOWUP">No Follow-up Needed</option><option value="DEFER">Defer Follow-up</option></Select></FormField>
    {edit.followup === "QUESTION" && <FormField label="Confirmed Question" required><Select required aria-label="Confirmed Follow-Up Question" value={edit.question_id ? `${edit.question_id}|${edit.question_version}` : ""} onChange={e => { const [id, version] = e.target.value.split("|"); onChange({ question_id: id || null, question_version: version ? Number(version) : null }); }}><option value="">Select Exact Question Version</option>{questions.map(q => <option key={text(q.question_id)} value={`${text(q.question_id)}|${String(q.version)}`}>{text(q.subject_title)} · {text(q.text)} · v{String(q.version)} · {text(q.status)}</option>)}</Select></FormField>}
    <p>Saving a link records your choice; it does not prove the question is answered. No Follow-up Needed requires a finding note.</p>
    <FormField label="Subject for New Question"><Select value={subjectId} onChange={e => setSubjectId(e.target.value)}><option value="">Select Research Subject</option>{subjects.map(s => <option key={text(s.subject_id)} value={text(s.subject_id)}>{text(s.title)}</option>)}</Select></FormField>
    {selected && <LinkButton href={`/research?subject_id=${encodeURIComponent(subjectId)}&section=thesis&retro_run_id=${encodeURIComponent(runId)}&retro_finding_key=${encodeURIComponent(findingKey)}`}>Prepare Question from Finding</LinkButton>}
    {subjects.some(s => s.questions_complete === false) && <p>Question choices are bounded. Open Research to inspect the remaining questions.</p>}
    <p>New questions require proposal and confirmation in Research. Return here, reload choices and explicitly link the confirmed question. Choose its deadline there if a reminder is needed.</p>
  </section>;
}


export function SavedFollowup({ row, data }: { row: Dict; data: Dict | undefined }) {
  const findings = listOf<Dict>(data, "findings");
  const source = findings.find(f => f.finding_key === row.finding_key);
  const subject = listOf<Dict>(source, "subjects").find(s => listOf<Dict>(s, "questions").some(q => q.question_id === row.question_id));
  const question = listOf<Dict>(subject, "questions").find(q => q.question_id === row.question_id);
  return <li>{text(row.finding_key)} · {text(row.followup, "No choice recorded")} {row.question_id ? `· ${text(row.question_id)} v${row.question_version}` : ""} · {text(row.note, "")}
    {question && subject ? <><p>Current question: v{String(question.version)} · {text(question.status)}. The saved review retains v{String(row.question_version)}; status alone is not proof that the process improved.</p><LinkButton href={`/research?subject_id=${encodeURIComponent(text(subject.subject_id))}&section=thesis&question_id=${encodeURIComponent(text(question.question_id))}`}>Inspect Follow-up Evidence</LinkButton></> : row.question_id ? <p>Current question status unavailable in this view. Reload follow-up choices; absence is not completion.</p> : null}
  </li>;
}


const NEXT_STEP: Record<string, string> = {
  DUE: "Review this due question",
  NOT_DUE: "Not due yet",
  NO_DEADLINE: "No deadline selected",
  QUESTION_CHANGED: "Question changed; review its new version",
  REVIEW_CHANGED: "Follow-up choice needs review",
  DEFERRED: "Follow-up deferred",
  NO_FOLLOWUP_RECORDED: "No further work requested; reason recorded",
  EVIDENCE_MISSING: "Answer needs traceable sources",
  READY_FOR_REVIEW: "Answer has sources; review the finding",
  REVIEWED_WITH_SOURCES: "Reviewed with traceable sources",
  CLOSED_QUESTION_NEEDS_REVIEW: "Question closed; review the finding separately",
  SOURCE_UNAVAILABLE: "Could not read the linked question or sources",
};
function FollowupProgress({ data }: { data: Dict }) {
  const rows = orderedFollowups(listOf<Dict>(data, "rows"));
  if (!data.coverage) return null;
  return <section aria-label="Follow-Up Next Steps"><h4>What Needs Attention</h4>
    {data.coverage === "UNAVAILABLE" ? <p>Follow-ups could not be read. Your saved choices remain unchanged.</p> : <>
      <p>{data.due_count == null ? "Due count is incomplete" : `${data.due_count} due follow-up(s)`} · {data.resolved_due_count == null ? "Reviewed count is incomplete" : `${data.resolved_due_count} reviewed with sources`}. This is follow-up evidence, not a score for investment results.</p>
      {rows.length === 0 ? <p>No question follow-ups found in the available review history.</p> : <ul>{rows.map(row => <li key={text(row.finding_key)}><strong>{text(row.title)}</strong><p>{NEXT_STEP[text(row.state)] ?? "Review status unavailable"}{row.due_at ? ` · Due ${formatDate(row.due_at)}` : ""}</p>
        {row.subject_id && row.question_id ? <LinkButton href={`/research?subject_id=${encodeURIComponent(text(row.subject_id))}&section=thesis&question_id=${encodeURIComponent(text(row.question_id))}`}>Open Question and Evidence</LinkButton> : null}
        {listOf<string>(row, "later_similar_runs").length > 0 && <div><p>Similar findings appear in later reviews. Compare the saved context before judging recurrence.</p>{listOf<string>(row, "later_similar_runs").map(id => <HistoricalReview key={id} runId={id} code={typeof row.finding_code === "string" ? row.finding_code : undefined} instrumentId={row.instrument_id as string | null} />)}</div>}
      </li>)}</ul>}
    </>}
    <Disclosure title="Follow-up Counting Basis"><p>Only question follow-ups explicitly selected in this Run's review history belong to this cohort. Due status uses the latest reviewed exact question version. Missing history, changed versions and unrecoverable deadlines keep totals unknown; undated and future tasks are outside the due denominator. Human RESOLVED review plus answer sources visible at answer time is counted as traceable review, not proof of semantic correctness or improved returns. NO_ACTION Decisions and trades are not this denominator.</p><p>Known selected: {String(data.known_selected_count ?? "Unknown")} · Known due: {String(data.known_due_count ?? "Unknown")} · Unknown deadline: {String(data.unknown_deadline_count ?? "Unknown")}.</p>{listOf<string>(data, "warning_codes").map(code => <Badge key={code} value={code} />)}</Disclosure>
  </section>;
}
