import {
  decodeNarrativeCopy, narrativeCopyKey, narrativePointerKey, narrativeScope,
  reconcileNarrativeDraft,
} from "./narrativeDraft";
import type {
  NarrativeCustody, NarrativeDraft, NarrativeRecoveryEntry, NarrativeStorage,
} from "./narrativeDraft";
import type { Project } from "./types";

/** The first successful observations stay independent of a new editor writer.
 * A failed read is never evidence that this tab has no earlier operation. */
export interface NarrativeRecoveryObservation {
  pointerObserved: boolean;
  pointer: string | null;
  rawObserved: boolean;
  raw: string | null;
}
export const emptyNarrativeRecoveryObservation = (): NarrativeRecoveryObservation => ({
  pointerObserved: false, pointer: null, rawObserved: false, raw: null,
});
type PointerStorage = Pick<Storage, "getItem">;

export function rememberNarrativeRecoveryEntry(observation: NarrativeRecoveryObservation,
  entry: NarrativeRecoveryEntry): void {
  if (observation.pointerObserved && observation.pointer === entry.writerId && !observation.rawObserved) {
    observation.raw = entry.raw;
    observation.rawObserved = true;
  }
}

/** Keep the exact first listed bytes downloadable even after a later read
 * fails or another runtime replaces this writer's entry. */
export function originalNarrativeRecoveryEntry(scope: string,
  observation: NarrativeRecoveryObservation): NarrativeRecoveryEntry | null {
  if (!observation.pointerObserved || observation.pointer === null
    || !observation.rawObserved || observation.raw === null) return null;
  let value: NarrativeRecoveryEntry["value"] = null;
  try { value = decodeNarrativeCopy(observation.raw, scope, observation.pointer); }
  catch { /* Unreadable author bytes still belong in the recovery download. */ }
  return { key: narrativeCopyKey(scope, observation.pointer), writerId: observation.pointer,
    raw: observation.raw, value };
}

/** Check the origin before and after the first new copy is published. Only
 * this runtime's verified copy may account for its own interrupted pointer write. */
export function assertNarrativeRecoverySource(storage: Pick<NarrativeStorage, "getItem">,
  session: PointerStorage, scope: string, observation: NarrativeRecoveryObservation,
  own?: NarrativeCustody): void {
  if (!observation.pointerObserved) throw new Error("Read this tab's earlier Narrative recovery pointer before making a new copy.");
  let originalPending: NarrativeDraft["pending"] = null;
  if (observation.pointer !== null) {
    if (!observation.rawObserved || observation.raw === null)
      throw new Error("The earlier Narrative recovery copy is unavailable. Its outcome remains unresolved.");
    if (storage.getItem(narrativeCopyKey(scope, observation.pointer)) !== observation.raw)
      throw new Error("The earlier Narrative recovery bytes changed. The original observation was kept.");
    originalPending = decodeNarrativeCopy(observation.raw, scope, observation.pointer).draft.pending;
  }
  const pointer = session.getItem(narrativePointerKey(scope));
  if (pointer === observation.pointer) return;
  if (own && pointer === own.writerId && own.expectedRaw !== null
    && storage.getItem(narrativeCopyKey(scope, own.writerId)) === own.expectedRaw) {
    const owned = decodeNarrativeCopy(own.expectedRaw, scope, own.writerId);
    if (originalPending && JSON.stringify(owned.draft.pending) !== JSON.stringify(originalPending))
      throw new Error("The new Narrative copy did not retain the earlier unconfirmed operation exactly.");
    return;
  }
  throw new Error("Another Narrative writer changed this tab's recovery pointer. Both sources were kept.");
}

export function readNarrativeRecoverySource(storage: Pick<NarrativeStorage, "getItem">,
  session: PointerStorage, scope: string, observation: NarrativeRecoveryObservation): NarrativeDraft | null {
  const pointer = session.getItem(narrativePointerKey(scope));
  if (observation.pointerObserved && pointer !== observation.pointer)
    throw new Error("Another Narrative writer changed the original recovery pointer. Nothing was adopted.");
  if (!observation.pointerObserved) {
    observation.pointer = pointer;
    observation.pointerObserved = true;
  }
  if (pointer === null) return null;
  const raw = storage.getItem(narrativeCopyKey(scope, pointer));
  if (observation.rawObserved && raw !== observation.raw)
    throw new Error("The earlier Narrative recovery bytes changed. The original observation was kept.");
  if (!observation.rawObserved) {
    observation.raw = raw;
    observation.rawObserved = true;
  }
  if (raw === null) throw new Error("This tab's Narrative pointer references a missing recovery copy.");
  const draft = decodeNarrativeCopy(raw, scope, pointer).draft;
  assertNarrativeRecoverySource(storage, session, scope, observation);
  return draft;
}

/** Recover the earlier pending comparison without consuming any text typed
 * while reading was unavailable, including an explicit blank or the old base. */
export function recoverNarrativeSource(recovered: NarrativeDraft | null, current: NarrativeDraft,
  project: Project): NarrativeDraft {
  if ((recovered && recovered.scope !== current.scope) || current.scope !== narrativeScope(project, current.target))
    throw new Error("The earlier Narrative source belongs to another project, generation, branch, or field.");
  if (current.pending !== null)
    throw new Error("An active Narrative comparison cannot be replaced by a recovery read.");
  const edited = current.sequence !== 0 || current.intent || current.raw !== current.base;
  // An observed empty pointer allows an untouched buffer to refresh. An
  // explicit return to the old base remains intent if Project changed meanwhile.
  const source = reconcileNarrativeDraft(recovered ?? (edited ? { ...current, intent: true } : current), project);
  if (!edited) return source;
  const sequence = Math.max(source.sequence, source.pending?.sequence ?? 0, current.sequence) + 1;
  if (!Number.isSafeInteger(sequence)) throw new Error("The Narrative input sequence cannot be represented safely. Both inputs were kept.");
  return { ...source, raw: current.raw, sequence, intent: true };
}
