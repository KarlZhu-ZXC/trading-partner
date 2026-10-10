export type QuestionResearchContext = {
  subject_id: string; question_id: string; question_version: number;
  question_fingerprint: string; evidence_cutoff: string;
  thesis_revision_id: string | null; assumption_id: string | null;
  source_refs: Array<{ kind: "EVIDENCE" | "OBSERVATION" | "MONITOR"; source_id: string; monitor_id?: string | null; rule_code?: string | null }>;
  source_coverage: "COMPLETE" | "PARTIAL" | "UNAVAILABLE";
};
export type PreparedQuestionResearch = {
  question_context_token: string; prompt: string; expires_at: string;
  context: QuestionResearchContext;
};
export const QUESTION_RESEARCH_EVENT = "tp:prepared-question-research";

export function navigateQuestionResearch(event: { preventDefault(): void; button: number; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean }, href: string) {
  if (window.location.pathname !== "/research" || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  const target = new URL(href, window.location.origin);
  const subjectChanged = new URLSearchParams(window.location.search).get("subject_id") !== target.searchParams.get("subject_id");
  window.history.pushState(null, "", href);
  window.dispatchEvent(new PopStateEvent("popstate"));
  if (subjectChanged) window.dispatchEvent(new HashChangeEvent("hashchange"));
  window.requestAnimationFrame(() => {
    const id = target.hash ? decodeURIComponent(target.hash.slice(1)) : "research-section-evidence-impact";
    document.getElementById(id)?.scrollIntoView({ block: "start" });
  });
}

export function validQuestionContext(value: unknown): value is QuestionResearchContext {
  const v = value as QuestionResearchContext | null;
  const id = (value: unknown) => typeof value === "string" && value.length > 0 && value.length <= 128;
  return Boolean(v && id(v.subject_id) && id(v.question_id)
    && Number.isSafeInteger(v.question_version) && v.question_version > 0
    && typeof v.question_fingerprint === "string" && /^[a-f0-9]{64}$/.test(v.question_fingerprint)
    && typeof v.evidence_cutoff === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(v.evidence_cutoff) && Number.isFinite(Date.parse(v.evidence_cutoff))
    && (v.thesis_revision_id == null) === (v.assumption_id == null)
    && (v.thesis_revision_id == null || id(v.thesis_revision_id) && id(v.assumption_id))
    && ["COMPLETE", "PARTIAL", "UNAVAILABLE"].includes(v.source_coverage)
    && Array.isArray(v.source_refs) && v.source_refs.length <= 8
    && v.source_refs.every((ref) => ref && ["EVIDENCE", "OBSERVATION", "MONITOR"].includes(ref.kind) && id(ref.source_id)));
}

export function offerQuestionResearch(prepared: PreparedQuestionResearch): boolean {
  if (!validQuestionContext(prepared.context) || !prepared.prompt || !prepared.question_context_token) return false;
  // A listener accepts explicitly. No storage, navigation payload, or auto-send.
  return !window.dispatchEvent(new CustomEvent(QUESTION_RESEARCH_EVENT, { detail: prepared, cancelable: true }));
}

export function researchOutcome(stop: string | null, verified: number, gaps: readonly string[]) {
  if (stop === "FAILED") return { title: "Research Failed", next: "Inspect the saved steps and source availability before explicitly retrying." };
  if (stop === "CANCELLED") return { title: "Research Cancelled", next: "Saved steps remain available. Prepare another run when you want to continue." };
  if (stop && ["TIME_BUDGET", "MODEL_BUDGET", "TOOL_BUDGET", "ROUND_LIMIT"].includes(stop)) return { title: "Research Incomplete", next: "The budget stopped this run. Review saved evidence and narrow the question before another run." };
  if (gaps.includes("MODEL_PROGRESS_UNAVAILABLE")) return { title: "Research Outcome Unavailable", next: "Inspect the saved answer and receipts; missing progress does not establish an evidence gap." };
  if (stop === "EVIDENCE_GAP" || verified === 0 || gaps.length > 0) return { title: "More Evidence Needed", next: "Inspect missing or conflicting sources, then update the research plan or continue manually." };
  return { title: "Answer Ready for Review", next: "Check the interpretation and strongest counterevidence before proposing an answer or evidence link." };
}
