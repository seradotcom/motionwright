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
export class ProjectReadSupersededError extends Error {
  constructor() {
    super("A newer project opening replaced this request. Its view was preserved.");
    this.name = "ProjectReadSupersededError";
  }
}

export interface ProjectRead<P extends ProjectRevision, R> {
  source: P | null;
  current: () => P | null;
  requestedProjectId: string | null;
  load: () => Promise<R>;
  project: (result: R) => P | null;
  publish: (result: R) => void;
  pending?: (value: boolean) => void;
}

export class ProjectMutationGate {
  private active = false;
  private reading = false;
  private readEpoch = 0;

  /** A new document may begin from an empty library. The operation still uses
   * the same synchronous latch and source-view check as every existing edit.
   * Its durable request/consent lives in the native creation command. */
  async create<P extends ProjectRevision>(
    source: P | null,
    current: () => P | null,
    operation: () => Promise<P>,
    publish: (next: P) => void,
    pending: (value: boolean) => void = () => {},
  ): Promise<P> {
    if (this.active || this.reading) throw new Error("Finish the current project operation before creating another project.");
    const base = source === null ? null : { id: source.id, generation: source.generation,
      revision: source.revision, active_branch: source.active_branch };
    const matches = () => base === null ? current() === null : sameProjectRevision(current(), base);
    if (!matches()) throw new Error("The active project changed before creation. Review its current state.");
    this.active = true;
    try {
      pending(true);
      const next = await operation();
      if (!next.id || !next.generation || !next.active_branch || (base && next.id === base.id)
          || !Number.isSafeInteger(next.revision) || next.revision < 0) {
        throw new Error("Creation did not return the expected new document. Recover the original request; no retry was made.");
      }
      if (!matches()) throw new Error("The active project changed during creation. Its view was preserved; recover the original request to open its result.");
      publish(next);
      return next;
    } finally {
      this.active = false;
      pending(false);
    }
  }

  async run<P extends ProjectRevision>(
    source: P,
    current: () => P | null,
    operation: (source: P) => Promise<P>,
    publish: (next: P) => void,
    pending: (value: boolean) => void = () => {},
    mode: "update" | "replace" = "update",
  ): Promise<P> {
    if (this.active || this.reading) {
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

  /** Explicit read admission shares the mutation latch. Later reads supersede
   * older reads; this never cancels or retries a backend effect. A reopened
   * identity may carry a new generation after restore. No selection is made
   * for an unselected or unavailable result unless the caller explicitly does so. */
  async read<P extends ProjectRevision, R>(request: ProjectRead<P, R>): Promise<R> {
    if (this.active) throw new Error("Finish the current project change or import before opening another project.");
    const base = request.source === null ? null : {
      id: request.source.id, generation: request.source.generation,
      revision: request.source.revision, active_branch: request.source.active_branch,
    };
    const matchesSource = () => base === null ? request.current() === null : sameProjectRevision(request.current(), base);
    if ((base && (!Number.isSafeInteger(base.revision) || base.revision < 0)) || !matchesSource()) {
      throw new Error("The active project changed before opening. Review its current state.");
    }
    const epoch = ++this.readEpoch;
    this.reading = true;
    try {
      request.pending?.(true);
      const result = await request.load();
      if (epoch !== this.readEpoch) throw new ProjectReadSupersededError();
      if (!matchesSource()) throw new Error("The active project changed while opening. Its view was preserved.");
      const next = request.project(result);
      if (next !== null) {
        if (request.requestedProjectId === null || next.id !== request.requestedProjectId
            || typeof next.generation !== "string" || !next.generation
            || typeof next.active_branch !== "string" || !next.active_branch
            || !Number.isSafeInteger(next.revision) || next.revision < 0
            || (base && next.id === base.id && next.generation === base.generation && next.revision < base.revision)) {
          throw new Error("The opened project does not match the requested identity or revision. No fallback was accepted.");
        }
      }
      request.publish(result);
      return result;
    } catch (reason) {
      if (epoch !== this.readEpoch) throw new ProjectReadSupersededError();
      throw reason;
    } finally {
      if (epoch === this.readEpoch) {
        this.reading = false;
        request.pending?.(false);
      }
    }
  }
}
