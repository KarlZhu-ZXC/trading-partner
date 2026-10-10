// Public synthetic DTOs only; no provider, account, or runtime reads.
import type { CycleAdjustmentRequest } from "../app/decision-workbench/cycle-adjustment-response";

export function syntheticCycleOverrideReceipt(request: CycleAdjustmentRequest, overrides: Record<string, unknown> = {}) {
  return {
    ...request,
    override_id: "trade_cycle_override_synthetic",
    version: request.expected_version + 1,
    algorithm_version: "trade_cycle_v1",
    actor: "user",
    note: request.note,
    authorization_note: request.authorization_note,
    created_at: "2026-10-05T01:00:00Z",
    ...overrides,
  };
}

function cycle(cycleId: string, activities: string[], effective = false) {
  return {
    cycle_id: cycleId, account_ref: "synthetic_account", provider: "schwab",
    instrument_id: "equity:US:AAPL", currency: "USD", activity_ids: activities,
    opened_at: "2026-08-18T14:00:00Z", closed_at: "2026-08-19T14:00:00Z",
    status: effective ? "UNRESOLVED" : "CLOSED", classification: "ACTIVE_TRADE",
    opening_count: effective ? 0 : 1, add_count: 0, reduce_count: effective ? 0 : 1,
    ending_quantity: effective ? null : "0", gross_realized_pnl: effective ? null : "100",
    net_realized_pnl: effective ? null : "98", current_average_cost: null,
    maximum_deployed_capital: effective ? null : "1000", holding_duration_seconds: effective ? null : 86400,
    reentry_of_cycle_id: null, quality: effective ? "INCOMPLETE" : "COMPLETE",
    warning_codes: effective ? ["MANUAL_RECOMPUTE_REQUIRED"] : [], algorithm_version: "trade_cycle_v1",
  };
}

export function syntheticCycleOverridePreview(request: CycleAdjustmentRequest) {
  const originalGroups = request.operation === "SPLIT" ? [request.split_groups.flat()]
    : request.operation === "MERGE" ? request.cycle_ids.map((_, index) => [`synthetic-fill-${index}`])
      : [["synthetic-kept-fill", ...request.activity_ids], ["synthetic-target-fill"]];
  const resultGroups = request.operation === "SPLIT" ? request.split_groups
    : request.operation === "MERGE" ? [originalGroups.flat()]
      : [["synthetic-kept-fill"], ["synthetic-target-fill", ...request.activity_ids]];
  const resultIds = request.operation === "RELINK" ? [request.cycle_ids[0], request.target_cycle_id!]
    : resultGroups.map((_, index) => `${request.root_cycle_id}:${request.operation.toLowerCase()}:synthetic-${index}`);
  const projection = {
    status: "COMPLETE", coverage_status: "COMPLETE", warning_codes: [], algorithm_version: "trade_cycle_v1",
    algorithm_cycles: [], override_revisions: [], override_impacts: [],
  };
  return {
    algorithm_projection: { ...projection, cycles: originalGroups.map((group, index) => cycle(request.cycle_ids[index], group)) },
    effective_projection: { ...projection, status: "INCOMPLETE", cycles: resultGroups.map((group, index) => cycle(resultIds[index], group, true)) },
    applied_revisions: [syntheticCycleOverrideReceipt(request, { override_id: "trade_cycle_override_preview" })],
    impacts: [{
      operation: request.operation, source_cycle_ids: request.cycle_ids, result_cycle_ids: resultIds,
      moved_activity_ids: request.operation === "RELINK" ? request.activity_ids : originalGroups.flat(),
      before_activity_count: originalGroups.flat().length, after_activity_count: resultGroups.flat().length,
      warning_codes: [`MANUAL_OVERRIDE_${request.operation}`], recompute_required: true,
    }],
  };
}
