import { describe, expect, it } from "vitest";
import { RECEIPT_RECHECK_MS, shouldAutoRecheckReceipts } from "./jobRefresh";
import type { ProductionJobProjection, ProductionJobState } from "./types";

const sample = (state: ProductionJobState): ProductionJobProjection => ({
  job_ref: "job-receipt-42",
  provider: "driver:motion-canvas",
  root_request_id: "request-42",
  generation: "generation-1",
  revision: 7,
  state,
  cancellation_requested: state === "cancel_requested",
  applicability: "current",
  last_command: "render.status",
  created_at: "2026-10-08T01:00:00Z",
  last_observed_at: "2026-10-08T01:00:00Z",
  local_observations: 1,
  provider_generation: 4,
  progress: null,
  artifact_available: false,
  result_available: false,
  last_observation: "observed",
});

describe("local production receipt reread policy", () => {
  it("allows bounded rereads for active jobs on a visible desktop surface", () => {
    for (const state of ["queued", "running", "cancel_requested"] as ProductionJobState[]) {
      expect(shouldAutoRecheckReceipts(true, true, [sample(state)])).toBe(true);
    }
    expect(RECEIPT_RECHECK_MS).toBeGreaterThanOrEqual(10_000);
  });

  it("does not reread terminal and unresolved outcomes indefinitely", () => {
    for (const state of ["succeeded", "failed", "cancelled", "outcome_unknown"] as ProductionJobState[]) {
      expect(shouldAutoRecheckReceipts(true, true, [sample(state)])).toBe(false);
    }
    expect(shouldAutoRecheckReceipts(true, true, [])).toBe(false);
  });

  it("never polls a hidden surface or the browser demo", () => {
    expect(shouldAutoRecheckReceipts(true, false, [sample("running")])).toBe(false);
    expect(shouldAutoRecheckReceipts(false, true, [sample("running")])).toBe(false);
  });

  it("rereads any active job even when a terminal result is also present", () => {
    expect(shouldAutoRecheckReceipts(true, true, [sample("succeeded"), sample("running")])).toBe(true);
  });
});
