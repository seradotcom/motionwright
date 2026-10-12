import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { nativeRecoveryWarning } from "./nativeRecovery";
import type { ProductionJobProjection } from "./types";

const project = structuredClone(fixtureProject);
const base: ProductionJobProjection = {
  job_ref: "owner-local-known-job",
  provider: "driver:motion-canvas",
  root_request_id: "original-explicit-request",
  generation: project.generation,
  revision: project.revision,
  state: "outcome_unknown",
  cancellation_requested: false,
  applicability: "current",
  last_command: "driver.motion-canvas.render.status",
  created_at: "2026-10-12T01:00:00Z",
  last_observed_at: "2026-10-12T01:00:05Z",
  local_observations: 3,
  provider_generation: null,
  progress: null,
  artifact_available: false,
  result_available: false,
  last_observation: "outcome_unknown",
};
describe("v0.1 owner-native uncertain job triage", () => {
  it("keeps an unresolved render unknown without automatically authorizing another", () => {
    const result = nativeRecoveryWarning(project, [base]);
    expect(result).toEqual({
      code: "ORIGINAL_JOB_OUTCOME_UNKNOWN", affectedJobs: 1,
      requiresOwnerReconciliation: true, mayAutomaticallyRetry: false,
      providerTerminalConfirmed: false,
    });
  });
  it("never combines unrelated historic generations or providers into current owner warning", () => {
    expect(nativeRecoveryWarning(project, [{ ...base, generation: crypto.randomUUID() }])).toBeNull();
    expect(nativeRecoveryWarning(project, [{ ...base, revision: project.revision + 1 }])).toBeNull();
    expect(nativeRecoveryWarning(project, [{ ...base, applicability: "stale" }])).toBeNull();
    expect(nativeRecoveryWarning(project, [{ ...base, provider: "driver:other" }])).toBeNull();
  });
  it("shows owner cancellation as unconfirmed until actual terminal event", () => {
    expect(nativeRecoveryWarning(project, [
      { ...base, state: "cancel_requested", last_observation: "observed" },
    ])?.code).toBe("CANCELLATION_NOT_CONFIRMED");
    expect(nativeRecoveryWarning(project, [
      { ...base, state: "running", cancellation_requested: true },
    ])?.code).toBe("CANCELLATION_NOT_CONFIRMED");
  });
  it("definitive source job outcomes do not inherit a pending-cancel warning", () => {
    for (const state of ["succeeded","failed","cancelled"] as const) {
      expect(nativeRecoveryWarning(project, [
        { ...base, state, cancellation_requested: true, last_observation: "observed" },
      ])).toBeNull();
    }
  });
  it("counts unknowns exactly and prioritizes them above in-flight cancellation", () => {
    const known = { ...base, job_ref: "second-original-job" };
    const cancelledPending = { ...base, job_ref: "cancel-pending", state: "cancel_requested" as const };
    const result = nativeRecoveryWarning(project, [base, known, cancelledPending]);
    expect(result?.code).toBe("ORIGINAL_JOB_OUTCOME_UNKNOWN");
    expect(result?.affectedJobs).toBe(2);
  });
  it("an empty or completed-only local snapshot does not prove upstream health", () => {
    expect(nativeRecoveryWarning(project, [])).toBeNull();
    expect(nativeRecoveryWarning(project, [{ ...base, state: "succeeded" }])).toBeNull();
  });
});
