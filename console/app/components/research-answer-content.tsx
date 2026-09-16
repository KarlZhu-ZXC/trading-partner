"use client";

import { useMemo } from "react";
import { Disclosure } from "./ui";
import { AgentMessageContent } from "./agent-message-content";
import {
  humanizeResearchEvidenceField,
  segmentResearchEvidence,
  type ResearchEvidenceField,
} from "../lib/research-evidence-presentation";
import styles from "./research-answer-content.module.css";

type ResearchAnswerContentProps = {
  content: string;
};

function contextClassName(key: string): string | undefined {
  if (key === "warnings") return styles.warning;
  if (key === "degraded") return styles.degraded;
  return undefined;
}

const HIDDEN_CONTEXT_KEYS = new Set(["source", "sources", "url", "source_url"]);

function materialContext(field: ResearchEvidenceField): Array<[string, string]> {
  return Object.entries(field.metadata).filter(([key, value]) => {
    if (HIDDEN_CONTEXT_KEYS.has(key)) return false;
    if (key === "degraded") return value === "true";
    if (key === "warnings") return value.trim().length > 0;
    return true;
  });
}

function ResearchEvidenceFieldView({ field }: { field: ResearchEvidenceField }) {
  const context = materialContext(field);
  return (
    <section
      aria-label={`Research Evidence · ${humanizeResearchEvidenceField(field)}`}
      className={styles.field}
      data-evidence-path={field.path}
      data-evidence-null={field.isNull ? "true" : "false"}
    >
      <div className={styles.valueLine}>
        <span className={styles.label}>{humanizeResearchEvidenceField(field)}</span>
        <strong className={styles.value}>{field.isNull ? "Unavailable (null)" : field.scalar}</strong>
      </div>
      {context.length > 0 && (
        <p className={styles.context} aria-label="Evidence Context">
          {context.map(([key, value]) => (
            <span className={contextClassName(key)} key={key}>
              <span className={styles.contextLabel}>{humanizeContextKey(key)}: </span>{value}
            </span>
          ))}
        </p>
      )}
      <Disclosure
        className={styles.raw}
        description="Full field path and source context"
        title="View Raw Evidence Field"
        variant="code"
      >
        <div className={styles.rawBody}>
          <pre><code>{field.raw}</code></pre>
        </div>
      </Disclosure>
    </section>
  );
}

const CONTEXT_LABELS: Record<string, string> = {
  instrument_id: "Instrument ID",
  account_ref: "Account",
  metric_code: "Metric Code",
  unit: "Unit",
  units: "Units",
  scale: "Scale",
  period_end: "Period End",
  period_type: "Period Type",
  adjustment: "Adjustment",
  currency: "Currency",
  as_of: "As Of",
  observed_at: "Observed Time",
  timestamp: "Timestamp",
  published_at: "Published Time",
  quote_at: "Quote Time",
  snapshot_at: "Snapshot Time",
  source_as_of: "Source As Of",
  price_basis: "Price Basis",
  basis: "Basis",
  freshness: "Freshness",
  source: "Source",
  sources: "Sources",
  warnings: "Warnings",
  degraded: "Degraded",
  confirmed: "Confirmed",
  confirmed_at: "Confirmed Time",
  status: "Status",
  direction: "Direction",
  url: "URL",
  source_url: "Source URL",
};

function humanizeContextKey(key: string): string {
  return CONTEXT_LABELS[key]
    ?? key.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function ResearchAnswerContent({ content }: ResearchAnswerContentProps) {
  const segments = useMemo(() => segmentResearchEvidence(content), [content]);
  if (!segments) return <AgentMessageContent content={content} />;

  return (
    <div className={`${styles.answer} agent-message-content`}>
      {segments.map((segment, index) => segment.kind === "field" ? (
        <ResearchEvidenceFieldView
          field={segment.field}
          key={`evidence-${index}-${segment.field.path}`}
        />
      ) : segment.kind === "metadata" ? (
        <Disclosure title="View Answer References" variant="code" key={`metadata-${index}`}>
          <div className={styles.rawBody}><pre><code>{segment.metadata.raw}</code></pre></div>
        </Disclosure>
      ) : segment.text ? (
        <AgentMessageContent content={segment.text} key={`text-${index}`} />
      ) : null)}
    </div>
  );
}
