export type ObservationResearchSource = {
  note_revision_id: string;
  note_id: string;
  note_version: number;
  title: string;
  instrument_id: string | null;
  observed_at: string;
  analysis_kind: "ESCALATED_REVIEW" | "INTERPRETATION" | null;
  analysis_id: string | null;
  provider: string | null;
  model: string | null;
  created_at: string | null;
};

type Viewpoint = {
  speaker_kind: string;
  speaker_label: string;
  summary: string;
  direction: string;
  holding_horizon: string;
  structure: string;
  source_block_ordinals: number[];
};

type Scenario = {
  scenario: string;
  action: string;
  condition: string;
  confirmation: string;
  loss_boundary: string;
};

export type ObservationResearchDraft = {
  source: ObservationResearchSource;
  payload: {
    change_relation?: string;
    suggested_next_step?: string;
    material_change_summary: string;
    viewpoints: Viewpoint[];
    user_scenarios: Scenario[];
    catalysts: string[];
    key_levels: string[];
    missing_evidence: string[];
    contradictions: string[];
  } | null;
  warnings: string[];
};

export type ObservationResearchSeed = {
  source: ObservationResearchSource;
  warnings: string[];
  ready: boolean;
  subject: {
    subjectType: string; title: string; summary: string; instrument: string;
    tags: string; linkedSubjectIds: string;
  };
  thesis: { title: string; statement: string; rationale: string; invalidationCheckNote: string };
  plan: {
    instrument: string;
    notes: string;
    conditions: {
      conditionCode: string; phase: string; mode: string; description: string;
      severity: string; factType: string; metricKey: string;
      comparator: string; threshold: string; unit: string;
    }[];
  };
  keyLevels: string[];
  fullReviewText: string;
  otherViewpoints: { speaker: string; summary: string }[];
};

/** Project an already-parsed, exact Observation into editable form text only. */
export function observationResearchSeed(draft: ObservationResearchDraft): ObservationResearchSeed {
  const { source, payload } = draft;
  const warnings = [...draft.warnings];
  const bound = (value: string, maximum: number, field: string): string => {
    if (value.length <= maximum) return value;
    warnings.push(`${field} exceeds the editor limit; the full parsed review remains available.`);
    return value.slice(0, maximum);
  };
  const symbol = source.instrument_id?.split(":").at(-1) ?? "Observation";
  const userViews = payload?.viewpoints.filter((item) => item.speaker_kind === "USER") ?? [];
  const otherViews = payload?.viewpoints.filter((item) => item.speaker_kind !== "USER") ?? [];
  const scenarios = payload?.user_scenarios ?? [];
  const origin = `Observation revision: ${source.note_revision_id} (v${source.note_version})\nAnalysis: ${source.analysis_kind ?? "unavailable"} · ${source.model ?? "unknown"} · ${source.analysis_id ?? "none"}`;
  const userText = userViews.map((item) => `${item.summary}\nDirection: ${item.direction}; horizon: ${item.holding_horizon}.\nStructure: ${item.structure}\nSource blocks: ${item.source_block_ordinals.join(", ")}`).join("\n\n");
  const scenarioText = scenarios.map((item) => `${item.scenario} · ${item.action}\nCondition: ${item.condition}\nConfirmation: ${item.confirmation}\nLoss boundary: ${item.loss_boundary}`).join("\n\n");
  const reference = (label: string, values: string[] | undefined): string => values?.length
    ? `${label}\n${values.map((item) => `- ${item}`).join("\n")}` : "";
  const referenceText = [
    payload?.change_relation ? `Change relation: ${payload.change_relation}` : "",
    payload?.material_change_summary ? `Parsed change summary\n${payload.material_change_summary}` : "",
    reference("Parsed level references (verify speaker, date, and role before use)", payload?.key_levels),
    reference("Catalyst references", payload?.catalysts),
    reference("Missing evidence", payload?.missing_evidence),
    reference("Contradictions to review", payload?.contradictions),
    payload?.suggested_next_step ? `Model next step: ${payload.suggested_next_step}` : "",
  ].filter(Boolean).join("\n\n");
  const rationale = [origin, userText, scenarioText, referenceText].filter(Boolean).join("\n\n");
  const invalidation = scenarios.find((item) => item.scenario === "INVALIDATION");
  if (payload && !userViews.length) warnings.push("No USER viewpoint was parsed; write your own Thesis statement before proposing it.");
  const token = source.note_revision_id.replace(/[^a-zA-Z0-9]/g, "").slice(-12);
  return {
    source,
    warnings,
    ready: Boolean(payload && source.instrument_id),
    subject: {
      subjectType: source.instrument_id?.startsWith("equity:") ? "company" : "theme",
      title: `${symbol} Research`,
      summary: `Research ${symbol} fundamentals, catalysts, valuation, market structure, and evolving external observations.`,
      instrument: source.instrument_id ?? "",
      tags: `${symbol.toLowerCase()}, observation_source`,
      linkedSubjectIds: "",
    },
    thesis: {
      title: `${symbol} Observation Thesis`,
      statement: bound(userViews.map((item) => item.summary).join("\n\n"), 8000, "Thesis statement"),
      rationale: bound(rationale, 16000, "Thesis rationale"),
      invalidationCheckNote: invalidation
        ? bound(`${invalidation.condition}\n${invalidation.confirmation}\n${invalidation.loss_boundary}`, 4000, "Invalidation note") : "",
    },
    plan: {
      instrument: source.instrument_id ?? "",
      notes: bound(rationale, 8000, "Trade Plan notes"),
      conditions: userViews.length ? scenarios.map((item) => ({
        conditionCode: `obs_${item.scenario.toLowerCase()}_${token}`,
        phase: "REVIEW", mode: "MANUAL",
        description: bound(`${item.scenario} · ${item.action}\nCondition: ${item.condition}\nConfirmation: ${item.confirmation}\nLoss boundary: ${item.loss_boundary}`, 2000, "Scenario condition"),
        severity: "MEDIUM", factType: "PRICE", metricKey: "last_price",
        comparator: "GTE", threshold: "", unit: "",
      })) : [],
    },
    keyLevels: payload?.key_levels ?? [],
    fullReviewText: [rationale, ...otherViews.map((item) => `Other speaker: ${item.speaker_label}\n${item.summary}\nDirection: ${item.direction}; horizon: ${item.holding_horizon}.\nStructure: ${item.structure}\nSource blocks: ${item.source_block_ordinals.join(", ")}`)].join("\n\n"),
    otherViewpoints: otherViews.map((item) => ({ speaker: item.speaker_label, summary: item.summary })),
  };
}
