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

/** Observation of the existing local actor, never an execution grant. */
export interface ProjectActivity {
  serial: number;
  busy: boolean;
}

/** Exact native read identity. Decimal revisions are never rounded to Number. */
export interface ProjectionRevision {
  project_id: string;
  generation: string;
  revision: string;
}

export function projectionUuid(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
    && value.toLowerCase() !== "00000000-0000-0000-0000-000000000000";
}

export function canonicalRevision(value: unknown): value is string {
  return typeof value === "string" && /^(0|[1-9][0-9]{0,19})$/.test(value)
    && (value.length < 20 || value <= "18446744073709551615");
}

export function validProjectionRevision(value: unknown): value is ProjectionRevision {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  return Object.keys(item).length === 3 && projectionUuid(item.project_id)
    && projectionUuid(item.generation) && canonicalRevision(item.revision);
}

export function sameProjectionRevision(a: ProjectionRevision, b: ProjectionRevision): boolean {
  return a.project_id.toLowerCase() === b.project_id.toLowerCase()
    && a.generation.toLowerCase() === b.generation.toLowerCase() && a.revision === b.revision;
}

export function compareDecimalRevision(a: string, b: string): number {
  return a.length === b.length ? (a === b ? 0 : a < b ? -1 : 1) : a.length < b.length ? -1 : 1;
}

export type ProjectViewRevision =
  | { kind: "full"; project: ProjectRevision }
  | { kind: "projection"; identity: ProjectionRevision };

export interface ProjectViewObservation {
  source: ProjectViewRevision | null;
  current: () => ProjectViewRevision | null;
}

export interface ProjectViewRead<R> extends ProjectViewObservation {
  requestedProjectId: string | null;
  load: () => Promise<R>;
  view: (result: R) => ProjectViewRevision | null;
  publish: (result: R) => void;
  pending?: (value: boolean) => void;
}

function validView(view: ProjectViewRevision): boolean {
  if (view.kind === "projection") return validProjectionRevision(view.identity);
  if (view.kind !== "full" || !view.project) return false;
  const project = view.project;
  return typeof project.id === "string" && Boolean(project.id)
    && typeof project.generation === "string" && Boolean(project.generation)
    && typeof project.active_branch === "string" && Boolean(project.active_branch)
    && Number.isSafeInteger(project.revision) && project.revision >= 0;
}

function copyView(view: ProjectViewRevision | null): ProjectViewRevision | null {
  if (view === null) return null;
  if (view.kind === "projection") return { kind: "projection", identity: { ...view.identity } };
  const { id, generation, revision, active_branch } = view.project;
  return { kind: "full", project: { id, generation, revision, active_branch } };
}

function sameView(a: ProjectViewRevision | null, b: ProjectViewRevision | null): boolean {
  if (a === null || b === null) return a === b;
  if (a.kind === "full" && b.kind === "full") return sameProjectRevision(a.project, b.project);
  return a.kind === "projection" && b.kind === "projection" && validProjectionRevision(a.identity)
    && sameProjectionRevision(a.identity, b.identity);
}

function viewIdentity(view: ProjectViewRevision): { id: string; generation: string; revision: string } {
  return view.kind === "full" ? { id: view.project.id, generation: view.project.generation, revision: String(view.project.revision) }
    : { id: view.identity.project_id, generation: view.identity.generation, revision: view.identity.revision };
}


export class ProjectMutationGate {
  private active = false;
  private reading = false;
  private readEpoch = 0;
  private activitySerial = 0;

  get activity(): ProjectActivity {
    return { serial: this.activitySerial, busy: this.active || this.reading };
  }

  /** A new document may begin from an empty library. The operation still uses
   * the same synchronous latch and source-view check as every existing edit.
   * Its durable request/consent lives in the native creation command. */
  async create<P extends ProjectRevision>(
    source: P | null,
    current: () => P | null,
    operation: () => Promise<P>,
    publish: (next: P) => void,
    pending: (value: boolean) => void = () => {},
    observation?: ProjectViewObservation,
  ): Promise<P> {
    if (this.active || this.reading) throw new Error("Finish the current project operation before creating another project.");
    const base = source === null ? null : { id: source.id, generation: source.generation,
      revision: source.revision, active_branch: source.active_branch };
    if (observation?.source && !validView(observation.source)) {
      throw new Error("The active project changed before creation. Review its current state.");
    }
    const observed = observation ? copyView(observation.source) : null;
    const matches = () => (base === null ? current() === null : sameProjectRevision(current(), base))
      && (!observation || sameView(observation.current(), observed));
    const previousId = base?.id ?? (observed ? viewIdentity(observed).id : null);
    if (!matches()) throw new Error("The active project changed before creation. Review its current state.");
    this.active = true;
    this.activitySerial++;
    try {
      pending(true);
      const next = await operation();
      if (!next.id || !next.generation || !next.active_branch || (previousId !== null && next.id.toLowerCase() === previousId.toLowerCase())
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
    this.activitySerial++;
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
    const full = (project: P | null): ProjectViewRevision | null => project === null ? null : { kind: "full", project };
    return this.readView({
      ...request,
      source: full(request.source),
      current: () => full(request.current()),
      view: result => full(request.project(result)),
    });
  }

  /** Full opens and bounded saved pages share the same latch and read epoch.
   * A read publishes only a view; it never acknowledges or replays an effect. */
  async readView<R>(request: ProjectViewRead<R>): Promise<R> {
    if (this.active) throw new Error("Finish the current project change or import before opening another project.");
    if (request.source !== null && !validView(request.source)) {
      throw new Error("The active project changed before opening. Review its current state.");
    }
    const base = copyView(request.source);
    const matchesSource = () => sameView(request.current(), base);
    if (!matchesSource()) throw new Error("The active project changed before opening. Review its current state.");
    const epoch = ++this.readEpoch;
    this.reading = true;
    this.activitySerial++;
    try {
      request.pending?.(true);
      const result = await request.load();
      if (epoch !== this.readEpoch) throw new ProjectReadSupersededError();
      if (!matchesSource()) throw new Error("The active project changed while opening. Its view was preserved.");
      const next = request.view(result);
      if (next !== null) {
        if (!validView(next)) throw new Error("The opened project does not match the requested identity or revision. No fallback was accepted.");
        const identity = viewIdentity(next);
        const previous = base === null ? null : viewIdentity(base);
        const requested = request.requestedProjectId;
        const requestedMatches = requested !== null && (next.kind === "projection"
          ? identity.id.toLowerCase() === requested.toLowerCase() : identity.id === requested);
        if (!requestedMatches || (previous && identity.id.toLowerCase() === previous.id.toLowerCase()
            && identity.generation.toLowerCase() === previous.generation.toLowerCase()
            && compareDecimalRevision(identity.revision, previous.revision) < 0)) {
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
