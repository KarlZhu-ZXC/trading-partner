"use client";

import { useEffect, useState } from "react";
import { Button, Disclosure, ErrorNote, formatDate } from "../components/ui";
import { getJson } from "../lib/api";
import { validQuestionContext, type QuestionResearchContext } from "../lib/question-research";
import type { EvidenceSource } from "./evidence-impact-panel";

export type ResearchSourceHandoff = {
  context: QuestionResearchContext; context_status: string;
  sources: EvidenceSource[]; sources_complete: boolean;
};

export function researchSourceHandoffUrl(subjectId: string): string | null {
  const query = new URLSearchParams(window.location.search);
  const conversation = query.get("research_conversation_id");
  const message = query.get("research_message_id");
  if (!conversation || !message) return null;
  return `/api/agent/conversations/${encodeURIComponent(conversation)}/messages/${encodeURIComponent(message)}/research-sources?subject_id=${encodeURIComponent(subjectId)}`;
}

export async function readResearchSourceHandoff(url: string, subjectId: string, signal?: AbortSignal): Promise<ResearchSourceHandoff> {
  const response = await getJson(url, signal) as { data: ResearchSourceHandoff };
  if (!validQuestionContext(response.data?.context) || response.data.context.subject_id !== subjectId || !Array.isArray(response.data.sources)) throw new Error("Research result belongs to an unavailable question scope.");
  return response.data;
}

export function ResearchResultSources({ subjectId, disabled, onUse }: {
  subjectId: string; disabled: boolean;
  onUse: (source: EvidenceSource, handoff: ResearchSourceHandoff, url: string) => void;
}) {
  const [handoff, setHandoff] = useState<ResearchSourceHandoff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let controller: AbortController | null = null;
    const read = () => {
      controller?.abort();
      const target = researchSourceHandoffUrl(subjectId);
      setUrl(target); setHandoff(null); setError(null);
      if (!target) return;
      const active = new AbortController(); controller = active;
      void readResearchSourceHandoff(target, subjectId, active.signal).then((value) => { if (!active.signal.aborted) setHandoff(value); }).catch((cause) => { if (!active.signal.aborted) setError(cause instanceof Error ? cause.message : "Research sources unavailable"); });
    };
    read();
    window.addEventListener("popstate", read);
    return () => { controller?.abort(); window.removeEventListener("popstate", read); };
  }, [subjectId]);
  if (!url) return null;
  return <section aria-label="Prepared Research Sources">
    <h3>Prepared Research Sources</h3>
    <p>These durable records were available at the question cutoff. They are source choices, not model endorsements; Provider and Web results are not imported as Evidence.</p>
    {error && <ErrorNote>{error}</ErrorNote>}
    {!handoff && !error && <p role="status">Reading the original question context…</p>}
    {handoff && <>
      <p>Question v{handoff.context.question_version} · {formatDate(handoff.context.evidence_cutoff)}</p>
      {handoff.context_status !== "CURRENT" && <ErrorNote>The question or selected assumption changed. Prepare Research again before using this handoff.</ErrorNote>}
      {(!handoff.sources_complete || handoff.context.source_coverage !== "COMPLETE") && <p>Source coverage is partial or unavailable. Missing records remain gaps.</p>}
      {handoff.sources.length === 0 && <p>No eligible durable sources were retained for this run. Continue research or review existing sources manually.</p>}
      {handoff.sources.map((source) => <div key={`${source.source.kind}:${source.source.source_id}:${source.source.rule_code ?? ""}`}><strong>{source.title}</strong><p>{source.source.kind} · {formatDate(source.fact_at)}</p><Button disabled={disabled || handoff.context_status !== "CURRENT"} onClick={() => onUse(source, handoff, url)}>Use Source in Link Draft</Button><Disclosure title="Prepared Source Reference"><p>{source.source.source_id}</p></Disclosure></div>)}
    </>}
  </section>;
}
