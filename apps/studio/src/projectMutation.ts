/** Local presentation coordination only: no replacement for Native SDK CAS,
 * effect grants, persistence, job recovery or an execution retry policy.
 * Reconciled from PR196 (0d90c0eba9ae646118954f79a29f15b290c21c14).
 * The source stamp also tracks the current branch; an authorized checkout may
 * return a different branch, so that equality applies to the visible source. */
export interface ProjectRevision {
  id: string;
  generation: string;
  revision: number;
  active_branch: string;
}

export function sameProjectRevision(a: ProjectRevision | null, b: ProjectRevision): boolean {
  return a !== null && a.id === b.id && a.generation === b.generation
    && a.revision === b.revision && a.active_branch === b.active_branch;
}

/** Acquire synchronously, before React can batch another event. All ordinary
 * edits, asset/voice imports and portable project replacements share this latch.
 * A switched project or newer visible revision must never be overwritten by a
 * late response, even if the backend operation already committed. This protects
 * only the current view, not persistent backend rollback or Broker authority.
 */
export class ProjectMutationGate {
  private active = false;

  async run<P extends ProjectRevision>(
    source: P,
    current: () => P | null,
    operation: (source: P) => Promise<P>,
    publish: (next: P) => void,
    pending: (value: boolean) => void = () => {},
    mode: "update" | "replace" = "update",
  ): Promise<P> {
    if (this.active) {
      throw new Error("Finish the current project change or import before starting another.");
    }
    const base = { id: source.id, generation: source.generation, revision: source.revision,
      active_branch: source.active_branch };
    if (!Number.isSafeInteger(base.revision) || base.revision < 0
        || !sameProjectRevision(current(), base)) {
      throw new Error("The visible project changed before this request. Review its current state.");
    }
    this.active = true;
    try {
      pending(true);
      const next = await operation(source);
      const updateMatches = next.id === base.id && next.generation === base.generation
        && Number.isSafeInteger(next.revision) && next.revision >= base.revision;
      const replacementMatches = typeof next.id === "string" && next.id.length > 0
        && next.id !== base.id && typeof next.generation === "string"
        && next.generation.length > 0 && next.generation !== base.generation
        && Number.isSafeInteger(next.revision) && next.revision >= 0;
      if (mode === "update" ? !updateMatches : !replacementMatches) {
        throw new Error("The response does not match the expected project identity or revision. No automatic retry was made.");
      }
      if (!sameProjectRevision(current(), base)) {
        throw new Error("The active project changed while the request was pending. Its view was preserved; reopen the original project to reconcile the result. No automatic retry was made.");
      }
      publish(next);
      return next;
    } finally {
      this.active = false;
      pending(false);
    }
  }
}
