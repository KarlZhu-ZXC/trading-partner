"use client";

import { Badge, Button, Card, Disclosure, ErrorNote, LinkButton, Tag, formatDate } from "./ui";
import { useApi } from "../lib/api";

type Reason = { reason_id: string; source_type: string; source_id: string; subject_id: string | null; title: string; detail: string; occurred_at: string; due_at: string | null; href: string; severity: string };
type ReviewGroup = { group_id: string; instrument_id: string | null; title: string; status: "ACTIVE" | "DEFERRED" | "REVIEWED"; priority: "ERROR" | "DUE" | "ATTENTION"; subjects: Array<{ subject_id: string; title: string; href: string }>; reasons: Reason[]; review_due_at: string | null };
type Digest = { as_of: string; timezone: string; groups: ReviewGroup[]; unscoped: Reason[]; coverage: Record<string, string>; warnings: string[] };
function sourceLabel(source: string): string {
  if (/OBSERVATION|NOTE/.test(source)) return "Notes";
  if (/MONITOR/.test(source)) return "Monitoring";
  if (/AGENDA|CATALYST/.test(source)) return "Catalysts";
  if (/THESIS|RESEARCH|DECISION|REVIEW/.test(source)) return "Research";
  if (/ORDER|BROKER/.test(source)) return "Broker";
  return "Operations";
}
function Group({ group }: { group: ReviewGroup }) {
  return <article className="research-selection-item">
    <div><strong>{group.title}</strong><p><Badge value={group.priority} /> {group.reasons.length} {group.reasons.length === 1 ? "item" : "items"} · {[...new Set(group.reasons.map((reason) => sourceLabel(reason.source_type)))].join(" · ")}</p>{group.review_due_at && <small>Review {formatDate(group.review_due_at)}</small>}
      {group.subjects.length === 1 ? <LinkButton href={group.subjects[0].href}>Quick Review</LinkButton> : group.subjects.length > 1 ? <div><p>Choose the research scope to review:</p><div className="page-actions">{group.subjects.map((subject) => <LinkButton key={subject.subject_id} href={subject.href}>{subject.title}</LinkButton>)}</div></div> : <p>No Research Subject is linked. Open an item below.</p>}
      <Disclosure title="Review Items">{group.reasons.map((reason) => <div key={reason.reason_id}><p><Tag>{sourceLabel(reason.source_type)}</Tag> {reason.title}</p><p>{reason.detail}</p><small>{formatDate(reason.occurred_at)}{reason.due_at ? ` · Due ${formatDate(reason.due_at)}` : ""}</small><p><LinkButton href={reason.href}>Open Item</LinkButton></p></div>)}</Disclosure>
    </div>
  </article>;
}
export function TodayReview() {
  const request = useApi<{ data: Digest }>("/api/review-digest");
  const digest = request.data?.data;
  const active = digest?.groups.filter((group) => group.status === "ACTIVE") ?? [];
  const deferred = digest?.groups.filter((group) => group.status === "DEFERRED") ?? [];
  const reviewed = digest?.groups.filter((group) => group.status === "REVIEWED") ?? [];
  const partial = !digest || Object.keys(digest.coverage).length === 0 || digest.warnings.length > 0 || Object.values(digest.coverage).some((status) => !["COMPLETE", "NOT_APPLICABLE"].includes(status));
  return <Card id="today-review" className="span-12" kicker="DECISION WORKFLOW" title="Today Review" action={<Button disabled={request.loading} onClick={request.refresh}>Refresh Today Review</Button>}>
    <ErrorNote>{request.error}</ErrorNote>
    {request.loading && <p>Loading today’s review items…</p>}
    {!request.loading && !digest && !request.error && <p>Review sources are unavailable. Refresh to try again.</p>}
    {digest && <>
      {partial && <p role="status">Some review sources are incomplete or unavailable. The items below may not cover everything that needs attention.</p>}
      {!request.loading && active.length === 0 && digest.unscoped.length === 0 && !partial && !request.error && <p>No active review items in the available sources.</p>}
      <div className="research-selection-list">{active.map((group) => <Group key={group.group_id} group={group} />)}</div>
      {digest.unscoped.length > 0 && <section aria-label="Other Actions"><h3>Other Actions</h3><p>These actions need separate attention.</p>{digest.unscoped.map((reason) => <div key={reason.reason_id}><p><Badge value={reason.severity} /> {reason.title}</p><p>{reason.detail}</p><LinkButton href={reason.href}>Open Action</LinkButton></div>)}</section>}
      {deferred.length > 0 && <Disclosure title={`Deferred · ${deferred.length}`}><div className="research-selection-list">{deferred.map((group) => <Group key={group.group_id} group={group} />)}</div></Disclosure>}
      {reviewed.length > 0 && <Disclosure title={`Reviewed · ${reviewed.length}`}><div className="research-selection-list">{reviewed.map((group) => <Group key={group.group_id} group={group} />)}</div></Disclosure>}
      <Disclosure title="Source Details"><p>As of {formatDate(digest.as_of)} · {digest.timezone}</p><p>{Object.entries(digest.coverage).map(([source, status]) => `${source}: ${status}`).join(" · ")}</p>{digest.warnings.length > 0 && <p>{digest.warnings.join(" · ")}</p>}</Disclosure>
    </>}
    <LinkButton href="/research?section=quick-review">Choose Research to Review</LinkButton>
  </Card>;
}
