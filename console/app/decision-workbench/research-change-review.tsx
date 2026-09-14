"use client";

import { useEffect, useState } from "react";
import { Button, Card, DescriptionList, ErrorNote, QuickLink, formatDate } from "../components/ui";
import { useApi, listOf } from "../lib/api";
import { asRecord as asDict, textStrict as text } from "../lib/coerce";
type Dict = Record<string, unknown>;

export function useResearchChangeReview(subjectId: string) {
  const [pinnedRevisionId, setPinnedRevisionId] = useState<string | null>(null);
  const [pinnedSubjectId, setPinnedSubjectId] = useState<string | null>(null);
  const [researchReturnHref, setResearchReturnHref] = useState<string | null>(null);
  const [changeReviewContext, setChangeReviewContext] = useState<{ id: string; baseline: string | null; offset: string } | null>(null);
  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const requested = query.get("subject_id");
    const changeId = query.get("change_id");
    if (requested && changeId) {
      const baseline = query.get("changes_baseline");
      const returnParams = new URLSearchParams({ change_id: changeId });
      if (baseline) returnParams.set("changes_baseline", baseline);
      const offset = query.get("changes_offset");
      if (offset && /^\d+$/.test(offset)) returnParams.set("changes_offset", offset);
      setResearchReturnHref(`/research?${returnParams.toString()}#subject-${encodeURIComponent(requested)}`);
      setPinnedSubjectId(requested);
      setPinnedRevisionId(query.get("note_revision_id"));
      setChangeReviewContext({ id: changeId, baseline, offset: offset && /^\d+$/.test(offset) ? offset : "0" });
    }
  }, []);
  const pinnedObservationApi = useApi<Dict>(
    `/api/observations/${encodeURIComponent(pinnedRevisionId ?? "")}/revision?subject_id=${encodeURIComponent(pinnedSubjectId ?? "")}`,
    { enabled: Boolean(pinnedRevisionId && pinnedSubjectId && subjectId === pinnedSubjectId) },
  );
  const pinnedObservation = asDict(pinnedObservationApi.data?.data);
  const pinnedRevisionMatches = text(asDict(pinnedObservation.revision).note_revision_id, "") === pinnedRevisionId && subjectId === pinnedSubjectId;
  const changeReviewApi = useApi<Dict>(
    `/api/research/${encodeURIComponent(pinnedSubjectId ?? "")}/changes?offset=${changeReviewContext?.offset ?? "0"}&limit=25&change_id=${encodeURIComponent(changeReviewContext?.id ?? "")}${changeReviewContext?.baseline ? `&baseline_decision_id=${encodeURIComponent(changeReviewContext.baseline)}` : ""}`,
    { enabled: Boolean(changeReviewContext && pinnedSubjectId && subjectId === pinnedSubjectId) },
  );
  const changeReviewData = asDict(changeReviewApi.data?.data);
  const reviewedChange = text(changeReviewData.subject_id, "") === pinnedSubjectId
    ? listOf<Dict>(changeReviewData, "items").find((item) => item.change_id === changeReviewContext?.id) : undefined;
  const pinnedItems = pinnedRevisionMatches && !pinnedObservationApi.loading && !pinnedObservationApi.error ? [pinnedObservation] : [];
  return { pinnedRevisionId, pinnedSubjectId, researchReturnHref, changeReviewContext, pinnedObservationApi, changeReviewApi, reviewedChange, pinnedItems,
    latestItems: (items: Dict[]) => pinnedRevisionId ? [] : items,
    noteItems: (items: Dict[]) => pinnedRevisionId ? pinnedItems : items,
    reviewWorkflowEnabled: (normal: boolean) => pinnedRevisionId ? pinnedObservation.review_workflow_enabled !== false : normal,
  };
}

export function ResearchChangeReview({ context, subjectId, canRecord, onRecord }: {
  context: ReturnType<typeof useResearchChangeReview>; subjectId: string; canRecord: boolean;
  onRecord: (change: Dict, baselineId: string | null) => void;
}) {
  const { researchReturnHref, pinnedRevisionId, pinnedSubjectId, changeReviewApi, reviewedChange, changeReviewContext } = context;
  return researchReturnHref ? <Card kicker="RESEARCH REVIEW" title="Review Context" action={<QuickLink href={researchReturnHref}>Return to Research Changes</QuickLink>}>
          <p className="card-note">This review keeps the selected source revision and review baseline. Returning to Research preserves that comparison; use Latest Review there to move to a newly confirmed Decision.</p>
          {changeReviewApi.loading ? <p role="status">Loading the pinned change…</p> : null}
          {changeReviewApi.error ? <ErrorNote>{changeReviewApi.error}<Button onClick={changeReviewApi.refresh}>Retry Review Context</Button></ErrorNote> : null}
          {reviewedChange && !changeReviewApi.loading && !changeReviewApi.error ? <>
            <h3>{text(reviewedChange.title)}</h3>
            <p>{text(reviewedChange.relation_detail)}</p>
            <DescriptionList items={[
              { label: "Previous Value", value: text(reviewedChange.old_value, "Not available") },
              { label: "New Value", value: text(reviewedChange.new_value, "Not available") },
              { label: "Source", value: text(reviewedChange.source_id) },
              { label: "Recorded", value: formatDate(reviewedChange.recorded_at) },
            ]} />
            {!pinnedRevisionId ? <Button disabled={!canRecord || pinnedSubjectId !== subjectId} onClick={() => onRecord(reviewedChange, changeReviewContext?.baseline ?? null)}>Record Review Decision</Button> : null}
          </> : null}
          {pinnedRevisionId ? <p>Observation revision <code>{pinnedRevisionId}</code></p> : null}
          {pinnedSubjectId !== subjectId ? <ErrorNote>The selected Research Subject changed. Return to the original review context before reviewing this source.</ErrorNote> : null}
        </Card> : null;

}

export function PinnedObservationStatus({ context }: { context: ReturnType<typeof useResearchChangeReview> }) {
  if (!context.pinnedRevisionId) return null;
  const api = context.pinnedObservationApi;
  return <>{api.loading ? <p role="status">Loading exact Observation revision…</p> : null}{api.error ? <ErrorNote>{api.error}<Button onClick={api.refresh}>Retry Exact Revision</Button></ErrorNote> : null}</>;
}
