type Dict = Record<string, unknown>;

export type CycleAdjustmentRequest = {
  root_cycle_id: string;
  operation: "SPLIT" | "MERGE" | "RELINK";
  cycle_ids: string[];
  activity_ids: string[];
  split_groups: string[][];
  target_cycle_id: string | null;
  expected_version: number;
  idempotency_key: string;
  note: string | null;
  authorization_note: string;
};

function object(value: unknown): Dict {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Dict : {};
}

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(item => typeof item === "string" && item.trim().length > 0)
    && new Set(value).size === value.length;
}

function equal(value: unknown, expected: string[]): boolean {
  return strings(value) && value.length === expected.length && value.every((item, index) => item === expected[index]);
}

function matchingRevision(value: Dict, request: CycleAdjustmentRequest): boolean {
  return value.root_cycle_id === request.root_cycle_id && value.operation === request.operation
    && value.idempotency_key === request.idempotency_key
    && Number.isSafeInteger(value.version) && value.version === request.expected_version + 1;
}

function matchingIntent(value: Dict, request: CycleAdjustmentRequest): boolean {
  return equal(value.cycle_ids, request.cycle_ids) && equal(value.activity_ids, request.activity_ids)
    && value.target_cycle_id === request.target_cycle_id
    && value.note === request.note && value.authorization_note === request.authorization_note
    && JSON.stringify(value.split_groups) === JSON.stringify(request.split_groups);
}

export function requireCycleOverrideReceipt(value: unknown, request: CycleAdjustmentRequest): Dict {
  const receipt = object(value);
  if (!matchingRevision(receipt, request) || !matchingIntent(receipt, request) || typeof receipt.override_id !== "string"
    || !receipt.override_id.trim() || receipt.override_id === "trade_cycle_override_preview") {
    throw new Error("The saved revision could not be verified. It may have been recorded. Your reviewed draft and original request key are retained. Inspect the durable Cycle records or explicitly retry this same reviewed request.");
  }
  return receipt;
}

export function requireCycleOverridePreview(value: unknown, request: CycleAdjustmentRequest): Dict {
  const preview = object(value);
  const revisions = preview.applied_revisions;
  const impacts = preview.impacts;
  const cycles = object(preview.effective_projection).cycles;
  const originalCycles = object(preview.algorithm_projection).cycles;
  const invalid = () => new Error("The preview did not match the requested Cycle correction or its effective projection was incomplete. Reload durable Cycle context, review the draft, and request a new Preview; nothing was applied.");
  if (!Array.isArray(revisions) || !Array.isArray(impacts) || revisions.length !== impacts.length
    || !Array.isArray(cycles) || !Array.isArray(originalCycles) || originalCycles.length === 0) throw invalid();
  const proposed = revisions.map(object).flatMap((revision, index) => revision.override_id === "trade_cycle_override_preview" ? [index] : []);
  if (proposed.length !== 1) throw invalid();
  const revision = object(revisions[proposed[0]]);
  if (!matchingRevision(revision, request) || !matchingIntent(revision, request)) throw invalid();
  const impact = object(impacts[proposed[0]]);
  if (impact.operation !== request.operation || !equal(impact.source_cycle_ids, request.cycle_ids)
    || !strings(impact.result_cycle_ids) || impact.result_cycle_ids.length === 0
    || !strings(impact.moved_activity_ids) || impact.moved_activity_ids.length === 0
    || !Number.isSafeInteger(impact.before_activity_count) || Number(impact.before_activity_count) < 1
    || impact.after_activity_count !== impact.before_activity_count || impact.recompute_required !== true
    || !strings(impact.warning_codes)) throw invalid();
  const results = impact.result_cycle_ids.map(id => cycles.map(object).filter(cycle => cycle.cycle_id === id));
  if (results.some(matches => matches.length !== 1 || !strings(matches[0].activity_ids))) throw invalid();
  const resultActivities = results.flatMap(matches => matches[0].activity_ids as string[]);
  if (resultActivities.length !== impact.after_activity_count || new Set(resultActivities).size !== resultActivities.length) throw invalid();
  if (request.operation === "SPLIT") {
    if (!equal(impact.moved_activity_ids, request.split_groups.flat())
      || results.length !== request.split_groups.length
      || results.some((matches, index) => !equal(matches[0].activity_ids, request.split_groups[index]))) throw invalid();
  } else if (request.operation === "MERGE") {
    if (results.length !== 1 || !equal(resultActivities, impact.moved_activity_ids)) throw invalid();
  } else {
    const target = cycles.map(object).find(cycle => cycle.cycle_id === request.target_cycle_id);
    if (!equal(impact.moved_activity_ids, request.activity_ids) || !target || !strings(target.activity_ids)
      || !impact.result_cycle_ids.includes(request.target_cycle_id ?? "")
      || !request.activity_ids.every(id => (target.activity_ids as string[]).includes(id))) throw invalid();
  }
  return preview;
}
