import type { ProductionJobProjection } from "./types";

/** A receipt read never queries or advances the canonical driver state. */
export const RECEIPT_RECHECK_MS = 12_000;

export function shouldAutoRecheckReceipts(
  desktopMode: boolean,
  visible: boolean,
  jobs: readonly ProductionJobProjection[],
): boolean {
  return desktopMode && visible && jobs.some(({ state }) =>
    state === "queued" || state === "running" || state === "cancel_requested"
  );
}
