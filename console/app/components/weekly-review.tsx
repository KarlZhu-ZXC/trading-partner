"use client";

import { Badge, Button, Card, Disclosure, ErrorNote, LinkButton, formatDate } from "./ui";
import { useApi } from "../lib/api";

type View = { subject_id: string; subject_title: string; thesis_id: string; revision_id: string; revision_no: number; confirmed_at: string; kind: string; before_statement: string | null; after_statement: string; changed_fields: string[]; href: string };
type Decision = { subject_id: string; subject_title: string; decision_id: string; decision_type: string; title: string; rationale: string; recorded_at: string; review_due_at: string | null; href: string };
type Question = { subject_id: string; question_id: string; text: string; asked_at: string; status: string; href: string };
type Group = { group_id: string; title: string; priority: string; subjects: { subject_id: string; title: string; href: string }[]; reasons: { reason_id: string; title: string; source_id: string; subject_id: string | null; href: string }[] };
type Weekly = { as_of: string; week_start: string; week_end: string; timezone: string; confirmed_views: View[]; decisions: Decision[]; open_questions: Question[]; due_groups: Group[]; unresolved_groups: Group[]; coverage: Record<string, string>; warnings: string[] };

function ReviewGroups({ groups }: { groups: Group[] }) {
  return <>{groups.map((group) => <article key={group.group_id}>
    <h4>{group.title}</h4><Badge value={group.priority} />
    <div className="page-actions">{group.subjects.map((subject) => <LinkButton key={subject.subject_id} href={subject.href}>Review {subject.title}</LinkButton>)}</div>
    <Disclosure title="Source Reasons">{group.reasons.map((reason) => <p key={reason.reason_id}><LinkButton href={reason.href}>{reason.title}</LinkButton> · {reason.source_id}{reason.subject_id ? ` · ${reason.subject_id}` : ""}</p>)}</Disclosure>
  </article>)}</>;
}

export function WeeklyReview() {
  const request = useApi<{ data: Weekly }>("/api/weekly-review");
  const digest = request.data?.data;
  const partial = !digest || digest.warnings.length > 0 || Object.keys(digest.coverage).length === 0 || Object.values(digest.coverage).some((value) => !["COMPLETE", "NOT_APPLICABLE"].includes(value));
  return <Card id="weekly-review" className="span-12" kicker="JUDGMENT HISTORY" title="Weekly Review" action={<Button disabled={request.loading} onClick={request.refresh}>Refresh Weekly Review</Button>}>
    <ErrorNote>{request.error}</ErrorNote>{request.loading && <p role="status">Reading this week’s saved reviews…</p>}
    {digest && <>
      <p>Week {formatDate(digest.week_start)} to {formatDate(digest.week_end)} · {digest.timezone}. Records through {formatDate(digest.as_of)}.</p>
      <p>Counts describe review records, not investment returns or win rates. Questions and due reviews reflect current durable state.</p>
      {partial && <p role="status">Some sources are incomplete or unavailable. This review may omit records that need attention.</p>}
      <section aria-label="Confirmed Views"><h3>Confirmed Views · {digest.confirmed_views.length}</h3>
        {digest.confirmed_views.length === 0 && !partial && <p>No confirmed Thesis revisions in this week’s available records.</p>}
        {digest.confirmed_views.map((view) => <article key={view.revision_id}>
          <h4>{view.subject_title}</h4><Badge value={view.kind} /><p>{view.after_statement}</p>
          <p>{formatDate(view.confirmed_at)} · {view.thesis_id} · {view.revision_id} (v{view.revision_no})</p>
          <Disclosure title="Exact Revision Difference"><p>Before: {view.before_statement ?? "No predecessor available"}</p><p>After: {view.after_statement}</p><p>Changed fields: {view.changed_fields.join(", ") || "No field differences available"}</p></Disclosure>
          <LinkButton href={view.href}>Review This Subject</LinkButton>
        </article>)}
      </section>
      <section aria-label="Unresolved Questions"><h3>Unresolved Questions · {digest.open_questions.length}</h3>
        {digest.open_questions.map((question) => <article key={question.question_id}><Badge value={question.status} /><p>{question.text}</p><p>{formatDate(question.asked_at)} · {question.question_id}</p><LinkButton href={question.href}>Review Question Scope</LinkButton></article>)}
      </section>
      <section aria-label="Due Reviews"><h3>Due Reviews · {digest.due_groups.length}</h3><ReviewGroups groups={digest.due_groups} /></section>
      <Disclosure title={`Other Unresolved Reviews · ${digest.unresolved_groups.length}`}><ReviewGroups groups={digest.unresolved_groups} /></Disclosure>
      <Disclosure title={`Recorded Decisions · ${digest.decisions.length}`}>
        {digest.decisions.map((decision) => <article key={decision.decision_id}><h4>{decision.subject_title} · {decision.title}</h4><Badge value={decision.decision_type} /><p>{decision.rationale}</p><p>{formatDate(decision.recorded_at)} · {decision.decision_id}{decision.review_due_at ? ` · Review due ${formatDate(decision.review_due_at)}` : ""}</p><LinkButton href={decision.href}>Review Decision Scope</LinkButton></article>)}
      </Disclosure>
      <Disclosure title="Source Coverage"><p>{Object.entries(digest.coverage).map(([key, value]) => `${key}: ${value}`).join(" · ")}</p><p>{digest.warnings.join(" · ")}</p></Disclosure>
    </>}
  </Card>;
}
