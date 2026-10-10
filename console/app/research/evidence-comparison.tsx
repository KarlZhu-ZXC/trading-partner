"use client";

import { useMemo, useState } from "react";
import {
  Badge,
  DescriptionList,
  Disclosure,
  Empty,
  ErrorNote,
  FormField,
  Select,
  TextLink,
  formatDate,
} from "../components/ui";
import type {
  EvidenceImpact,
  EvidenceLinks,
  EvidenceSource,
  EvidenceSourceRef,
  EvidenceTarget,
  SharedEvidenceLinksRead,
} from "./evidence-impact-panel";
import styles from "../styles/evidence-comparison.module.css";

type Lane = "supports" | "contradicts" | "uncertain";

const LANE_LABELS: Record<Lane, string> = {
  supports: "Supports",
  contradicts: "Contradicts",
  uncertain: "Uncertain",
};

type Props = {
  subjectId: string;
  snapshot: EvidenceLinks | null;
  linksRead?: SharedEvidenceLinksRead;
};

function exactTargetKey(target: Pick<EvidenceTarget, "thesis_revision_id" | "assumption_id">): string {
  return `${target.thesis_revision_id}:${target.assumption_id}`;
}

function sourceKey(ref: EvidenceSourceRef): string {
  return [ref.kind, ref.source_id, ref.monitor_id ?? "", ref.rule_code ?? ""].join(":");
}

function sourceReference(ref: EvidenceSourceRef): string {
  const parts = [`${ref.kind} · ${ref.source_id}`];
  if (ref.kind === "MONITOR" || ref.monitor_id || ref.rule_code) {
    parts.push(`monitor_id=${ref.monitor_id ?? "unavailable"}`);
    parts.push(`rule_code=${ref.rule_code ?? "unavailable"}`);
  }
  return parts.join(" · ");
}

function safeHttpUrl(value: string | null | undefined): string | null {
  if (!value?.trim()) return null;
  try {
    const parsed = new URL(value.trim());
    if ((parsed.protocol !== "http:" && parsed.protocol !== "https:") || parsed.username || parsed.password || !parsed.hostname) return null;
    return parsed.toString();
  } catch {
    return null;
  }
}

function sourceForImpact(snapshot: EvidenceLinks, impact: EvidenceImpact): EvidenceSource {
  const source = snapshot.sources.find((item) => sourceKey(item.source) === sourceKey(impact.source));
  if (source) return source;
  return {
    source: impact.source,
    title: impact.source_available === false ? "Source unavailable" : "Source metadata unavailable",
    source_name: null,
    source_url: null,
    source_url_invalid: false,
    fact_at: null,
    recorded_at: null,
    origin: "Historical immutable reference",
    warning_codes: ["SOURCE_METADATA_UNAVAILABLE"],
  };
}

function sourceIsUnavailable(snapshot: EvidenceLinks, impact: EvidenceImpact): boolean {
  if (impact.source_available === false) return true;
  if (impact.source_available === true) return false;
  return !snapshot.sources.some((item) => sourceKey(item.source) === sourceKey(impact.source));
}

function sourceHref(subjectId: string, source: EvidenceSource): { href: string; external: boolean } | null {
  if (source.source.kind === "OBSERVATION") {
    return {
      href: `/decision-workbench?subject_id=${encodeURIComponent(subjectId)}&note_revision_id=${encodeURIComponent(source.source.source_id)}#notes`,
      external: false,
    };
  }
  const url = safeHttpUrl(source.source_url);
  return url ? { href: url, external: true } : null;
}

function targetForImpact(snapshot: EvidenceLinks, impact: EvidenceImpact): EvidenceTarget | null {
  return snapshot.targets.find((target) => exactTargetKey(target) === exactTargetKey(impact)) ?? null;
}

function targetLabel(target: EvidenceTarget | null, impact?: Pick<EvidenceImpact, "thesis_revision_id" | "assumption_id">): string {
  if (!target) return `Historical target · revision ${impact?.thesis_revision_id ?? "unavailable"} · assumption ${impact?.assumption_id ?? "unavailable"}`;
  return `${target.thesis_title} · v${target.revision_no} · ${target.is_current ? "current" : "historical"}`;
}

function laneFor(impact: EvidenceImpact): Lane | null {
  if (impact.stance === "supports" || impact.stance === "contradicts" || impact.stance === "uncertain") return impact.stance;
  return null;
}

function sourceDisplayName(source: EvidenceSource): string {
  return source.title || source.source_name?.trim() || "Untitled source";
}

function SourceDetails({ subjectId, source, impact }: { subjectId: string; source: EvidenceSource; impact: EvidenceImpact }) {
  const link = sourceHref(subjectId, source);
  return <Disclosure className={styles.detail} title="Source Details" variant="compact">
    <DescriptionList columns={2} items={[
      { label: "Original Source", value: link ? <TextLink href={link.href} target={link.external ? "_blank" : undefined} rel={link.external ? "noopener noreferrer" : undefined}>{sourceDisplayName(source)}</TextLink> : sourceDisplayName(source) },
      ...(source.source_name?.trim() ? [{ label: "Source Name", value: source.source_name.trim() }] : []),
      { label: "Provenance", value: source.origin || "Unavailable" },
      { label: "Fact Time", value: formatDate(source.fact_at) },
      { label: "Recorded Time", value: formatDate(source.recorded_at) },
      { label: "Exact Source Ref", value: sourceReference(source.source) },
      { label: "Impact ID / Version", value: `${impact.impact_id} · v${impact.version}` },
      { label: "Thesis Revision", value: impact.thesis_revision_id },
      { label: "Assumption", value: impact.assumption_id },
    ]} />
    {source.source_url_invalid && <p className={styles.unavailable}>A source URL was returned but was not safe to open; the exact URL is not rendered.</p>}
    <p>Source IDs are shown exactly as stored. No external URL is inferred from source type or origin.</p>
    <p>Warning codes</p>
    {source.warning_codes.length > 0 ? <ul>{source.warning_codes.map((warning) => <li key={warning}>{warning}</li>)}</ul> : <p>None reported.</p>}
  </Disclosure>;
}

function ImpactCard({ subjectId, snapshot, impact }: { subjectId: string; snapshot: EvidenceLinks; impact: EvidenceImpact }) {
  const target = targetForImpact(snapshot, impact);
  const source = sourceForImpact(snapshot, impact);
  const sourceUnavailable = sourceIsUnavailable(snapshot, impact);
  const knownStance = impact.stance === "supports" || impact.stance === "contradicts" || impact.stance === "uncertain";
  return <article className={styles.item}>
    <div className={styles.itemHeader}>
      <strong>{targetLabel(target, impact)}</strong>
      {!knownStance && <Badge value="STANCE UNAVAILABLE" />}
    </div>
    {target ? <p className={styles.statement}>{target.statement}</p> : <p className={styles.statement}>Historical Thesis target label is unavailable; exact revision and assumption IDs are retained below.</p>}
    <div className={`${styles.source}${sourceUnavailable ? ` ${styles.unavailable}` : ""}`}>
      <strong>{sourceDisplayName(source)}</strong>
      <small>{source.source.kind}</small>
      {sourceUnavailable && <small>Source metadata is currently unavailable; this immutable association is retained.</small>}
    </div>
    <p className={styles.rationale}>{impact.rationale || "No rationale was recorded."}</p>
    <p className={styles.meta}>Confirmed {formatDate(impact.confirmed_at)} · reviewer {impact.confirmed_by ?? "unknown"}</p>
    <SourceDetails subjectId={subjectId} source={source} impact={impact} />
  </article>;
}

export function EvidenceComparison({ subjectId, snapshot, linksRead }: Props) {
  const [targetFilter, setTargetFilter] = useState("");
  const targetOptions = useMemo(() => {
    if (!snapshot) return [];
    const values = [...snapshot.targets];
    const seen = new Set(values.map(exactTargetKey));
    for (const impact of snapshot.impacts.filter((item) => item.state === "ACTIVE")) {
      const key = exactTargetKey(impact);
      if (seen.has(key)) continue;
      values.push({
        thesis_id: impact.thesis_id,
        thesis_revision_id: impact.thesis_revision_id,
        revision_no: 0,
        thesis_title: "Historical Thesis target",
        assumption_id: impact.assumption_id,
        statement: "Target label unavailable",
        is_current: false,
      });
      seen.add(key);
    }
    return values;
  }, [snapshot]);

  if (linksRead?.error && !snapshot) return <section className={styles.root} aria-label="Evidence Comparison"><ErrorNote role="alert">Evidence Comparison is unavailable until the shared source scope recovers. {linksRead.error}</ErrorNote></section>;
  if (linksRead?.loading && !snapshot) return <section className={styles.root} aria-label="Evidence Comparison"><p className={styles.scope} role="status">Loading evidence comparison…</p></section>;
  if (!snapshot) return null;

  const activeImpacts = snapshot.impacts.filter((impact) => impact.state === "ACTIVE").filter((impact) => !targetFilter || exactTargetKey(impact) === targetFilter);
  const lanes: Record<Lane, EvidenceImpact[]> = { supports: [], contradicts: [], uncertain: [] };
  const unavailableStance: EvidenceImpact[] = [];
  for (const impact of activeImpacts) {
    const lane = laneFor(impact);
    if (lane) lanes[lane].push(impact); else unavailableStance.push(impact);
  }
  const sourceCoverage = snapshot.coverage.sources;
  const impactCoverage = snapshot.coverage.impacts;

  return <section className={styles.root} aria-label="Evidence Comparison">
    <div className={styles.heading}><p className="card-kicker">COMPARISON WORKBENCH</p><h3>Evidence Comparison</h3></div>
    <p className={styles.scope}>Compare reviewed support, counterevidence and unresolved interpretations. Expand a source to inspect its provenance and exact references.</p>
    <div className={styles.controls}><FormField label="Exact Thesis Revision + Assumption Filter"><Select aria-label="Exact Thesis Revision + Assumption Filter" value={targetFilter} onChange={(event) => setTargetFilter(event.target.value)}><option value="">All exact targets</option>{targetOptions.map((target) => <option key={exactTargetKey(target)} value={exactTargetKey(target)}>{targetLabel(target)}</option>)}</Select></FormField></div>
    {linksRead?.error && <p className={styles.warning} role="status">The shared evidence read reported a refresh error; the last loaded comparison is retained. {linksRead.error}</p>}
    {sourceCoverage === "UNAVAILABLE" ? <ErrorNote role="alert">Source metadata is unavailable. Saved associations and exact source IDs remain visible; original source details may be incomplete.</ErrorNote> : sourceCoverage === "PARTIAL" ? <p className={styles.warning} role="status">Source coverage is partial. Associations whose source metadata is missing remain visible as unavailable historical references.</p> : null}
    {impactCoverage === "UNAVAILABLE" ? <ErrorNote role="alert">Evidence associations are unavailable for this read. No empty state is inferred.</ErrorNote> : impactCoverage === "PARTIAL" ? <p className={styles.warning} role="status">Evidence-association coverage is partial; this comparison may omit links beyond the bounded read.</p> : null}
    {impactCoverage === "UNAVAILABLE" ? null : activeImpacts.length === 0 ? <Empty>{targetFilter ? "No active evidence links match this exact target." : "No confirmed active evidence links are available for this Research Subject."}</Empty> : <><div className={styles.lanes}>{(Object.keys(LANE_LABELS) as Lane[]).map((lane) => <section className={styles.lane} key={lane} aria-label={`${LANE_LABELS[lane]} evidence`}><div className={styles.laneHeader}><h4>{LANE_LABELS[lane]}</h4><Badge value={`${lanes[lane].length} LINK${lanes[lane].length === 1 ? "" : "S"}`} /></div>{lanes[lane].length === 0 ? <p className={styles.empty}>No active links in this lane.</p> : <div className={styles.items}>{lanes[lane].map((impact) => <ImpactCard key={`${impact.impact_id}:${impact.version}`} subjectId={subjectId} snapshot={snapshot} impact={impact} />)}</div>}</section>)}</div>{unavailableStance.length > 0 && <section className={styles.lane} aria-label="Evidence with Unavailable Stance"><div className={styles.laneHeader}><h4>Stance unavailable</h4><Badge value={`${unavailableStance.length} NOT CLASSIFIED`} /></div><p className={styles.warning}>These active associations are retained with their exact stored value and are excluded from the three reviewed stance counts.</p><div className={styles.items}>{unavailableStance.map((impact) => <ImpactCard key={`${impact.impact_id}:${impact.version}`} subjectId={subjectId} snapshot={snapshot} impact={impact} />)}</div></section>}</>}
  </section>;
}
