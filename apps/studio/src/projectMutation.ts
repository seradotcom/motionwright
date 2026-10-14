/** Local presentation coordination only: no replacement for Native SDK CAS,
 * effect grants, persistence, job recovery or an execution retry policy. */
export interface ProjectRevision {
  id: string;
  generation: string;
  revision: number;
}

export function sameProjectRevision(a: ProjectRevision | null, b: ProjectRevision): boolean {
  return a !== null && a.id === b.id && a.generation === b.generation
    && a.revision === b.revision;
}

/** Acquire synchronously, before React can batch another event. All ordinary
 * edits, asset imports and measured voice imports share this one view latch.
 * A switched project or a newer visible revision must never be overwritten by
 * the response to a former request, even if that request committed server-side.
 */
export class ProjectMutationGate {
  private active = false;

  async run<P extends ProjectRevision>(
    source: P,
    current: () => P | null,
    operation: (source: P) => Promise<P>,
    publish: (next: P) => void,
    pending: (value: boolean) => void = () => {},
  ): Promise<P> {
    if (this.active) {
      throw new Error("Finish the current project change or import before starting another.");
    }
    const base = { id: source.id, generation: source.generation, revision: source.revision };
    if (!Number.isSafeInteger(base.revision) || base.revision < 0
        || !sameProjectRevision(current(), base)) {
      throw new Error("The visible project changed before this request. Review its current state.");
    }
    this.active = true;
    try {
      pending(true);
      const next = await operation(source);
      if (next.id !== base.id || next.generation !== base.generation
          || !Number.isSafeInteger(next.revision) || next.revision < base.revision) {
        throw new Error("The response does not match the original project revision. No automatic retry was made.");
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
