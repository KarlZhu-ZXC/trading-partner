"use client";

import { useState } from "react";
import { Badge, Button, Card, Disclosure, ErrorNote, FilterBar, FormField, Input, LinkButton, Select, Tag, formatDate } from "./ui";
import { useApi } from "../lib/api";
import { filterReviewGroups, normalizeReviewPriority, sourceLabel, type Digest, type ReviewGroup, type ReviewPriority } from "../lib/today-review-presentation";

function Group({ group }: { group: ReviewGroup }) {
  const firstReason = group.reasons[0];
  return <article className="research-selection-item">
    <div><strong>{group.title}</strong>{firstReason && <p className="card-note">Trigger · {firstReason.title}</p>}<p><Badge value={group.priority} /> {group.reasons.length} {group.reasons.length === 1 ? "item" : "items"} · {[...new Set(group.reasons.map((reason) => sourceLabel(reason.source_type)))].join(" · ")}</p>{group.review_due_at && <small>Review {formatDate(group.review_due_at)}</small>}
      {group.subjects.length === 1 ? <LinkButton href={group.subjects[0].href}>Quick Review</LinkButton> : group.subjects.length > 1 ? <div><p>Choose the research scope to review:</p><div className="page-actions">{group.subjects.map((subject) => <LinkButton key={subject.subject_id} href={subject.href}>{subject.title}</LinkButton>)}</div></div> : <p>No Research Subject is linked. Open an item below.</p>}
      <Disclosure title="Review Items">{group.reasons.map((reason) => <div key={reason.reason_id}><p><Tag>{sourceLabel(reason.source_type)}</Tag> {reason.title}</p><p>{reason.detail}</p><small>{formatDate(reason.occurred_at)}{reason.due_at ? ` · Due ${formatDate(reason.due_at)}` : ""}</small><p><LinkButton href={reason.href}>Open Item</LinkButton></p></div>)}</Disclosure>
    </div>
  </article>;
}
export function TodayReview() {
  const request = useApi<{ data: Digest }>("/api/review-digest");
  const [query, setQuery] = useState("");
  const [priority, setPriority] = useState<ReviewPriority>("ALL");
  const digest = request.data?.data;
  const active = digest?.groups.filter((group) => group.status === "ACTIVE") ?? [];
  const deferred = digest?.groups.filter((group) => group.status === "DEFERRED") ?? [];
  const reviewed = digest?.groups.filter((group) => group.status === "REVIEWED") ?? [];
  const filteredActive = filterReviewGroups(active, query, priority);
  const filtersActive = query.trim().length > 0 || priority !== "ALL";
  const dueGroups = active.filter((group) => group.priority === "DUE").length;
  const errorGroups = active.filter((group) => group.priority === "ERROR").length;
  const noMatches = filtersActive && filteredActive.length === 0;
  const partial = !digest || Object.keys(digest.coverage).length === 0 || digest.warnings.length > 0 || Object.values(digest.coverage).some((status) => !["COMPLETE", "NOT_APPLICABLE"].includes(status));
  const resetFilters = () => { setQuery(""); setPriority("ALL"); };
  return <Card id="today-review" className="span-12" kicker="DECISION WORKFLOW" title="Today Review" action={<Button disabled={request.loading} onClick={request.refresh}>Refresh Today Review</Button>}>
    <ErrorNote>{request.error}</ErrorNote>
    {request.loading && <p role="status">{digest ? "Refreshing today’s review items…" : "Loading today’s review items…"}</p>}
    {request.error && digest && <p role="status">Showing the last successful review snapshot. Refresh failed; this data may be stale.</p>}
    {!request.loading && !digest && !request.error && <p>Review sources are unavailable. Refresh to try again.</p>}
    {digest && <>
      <p>Snapshot as of {formatDate(digest.as_of)} · {digest.timezone}</p>
      {partial && <p role="status">Some review sources are incomplete or unavailable. The items below may not cover everything that needs attention.</p>}
      <p role="status">Active groups: {active.length} · Due groups: {dueGroups} · Error groups: {errorGroups}</p>
      <FilterBar aria-label="Today Review Filters">
        <FormField label="Search Reviews"><Input type="search" value={query} placeholder="Title, Instrument, source" onChange={(event) => setQuery(event.target.value)} /></FormField>
        <FormField label="Priority"><Select value={priority} onChange={(event) => setPriority(normalizeReviewPriority(event.target.value))}><option value="ALL">All priorities</option><option value="ERROR">Errors</option><option value="DUE">Due</option><option value="ATTENTION">Attention</option></Select></FormField>
        {filtersActive && <Button type="button" onClick={resetFilters}>Reset Filters</Button>}
      </FilterBar>
      <p role="status">Showing {filteredActive.length} of {active.length} active groups</p>
      {noMatches ? <div><p role="status">No active reviews match the current filters.</p><Button type="button" onClick={resetFilters}>Clear Filters</Button></div> : <div className="research-selection-list">{filteredActive.map((group) => <Group key={group.group_id} group={group} />)}</div>}
      {!request.loading && active.length === 0 && digest.unscoped.length === 0 && !partial && !request.error && !filtersActive && <p>No active review items in the available sources.</p>}
      {digest.unscoped.length > 0 && <section aria-label="Other Actions"><h3>Other Actions</h3><p>{filtersActive ? "Filters apply to active groups only; Other Actions are always shown." : "These actions need separate attention."}</p>{digest.unscoped.map((reason) => <div key={reason.reason_id}><p><Badge value={reason.severity} /> {reason.title}</p><p>{reason.detail}</p><LinkButton href={reason.href}>Open Action</LinkButton></div>)}</section>}
      {deferred.length > 0 && <Disclosure title={`Deferred · ${deferred.length}`}><div className="research-selection-list">{deferred.map((group) => <Group key={group.group_id} group={group} />)}</div></Disclosure>}
      {reviewed.length > 0 && <Disclosure title={`Reviewed · ${reviewed.length}`}><div className="research-selection-list">{reviewed.map((group) => <Group key={group.group_id} group={group} />)}</div></Disclosure>}
      <Disclosure title="Source Details"><p>As of {formatDate(digest.as_of)} · {digest.timezone}</p><p>{Object.entries(digest.coverage).map(([source, status]) => `${source}: ${status}`).join(" · ")}</p>{digest.warnings.length > 0 && <p>{digest.warnings.join(" · ")}</p>}</Disclosure>
    </>}
    <LinkButton href="/research?section=quick-review">Choose Research to Review</LinkButton>
  </Card>;
}
