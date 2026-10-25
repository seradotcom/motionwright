/** Authored input only. FileImportRetention owns every grant, dispatch and
 * UNKNOWN outcome; no import is prepared, repeated or acknowledged here. */
import { useEffect, useSyncExternalStore } from "react";
import type { NativeCapsule } from "./creativeProduction";
import { sameValue } from "./creativeProduction";
import { parseNativeCapsuleSourceSpec, type NativeCapsuleSourceSpec7 } from "./fileImport";
import { isRecoveryOrigin, matchesRecoveryOrigin, recoveryOrigin, type RecoveryOrigin } from "./recoveryOrigin";
import type { Project } from "./types";

export interface NativeSourceFields {
  path: string; name: string; mediaType: string; label: string; parameters: string; editorHint: string;
}
export type NativeSourceField = keyof NativeSourceFields;
export type NativeSourceTarget = "new" | string;
export type NativeSourceStorage = Pick<Storage, "getItem" | "setItem">;
export interface NativeSourceDraft {
  schema: "motionwright.native-source-draft/1";
  scope: string; sceneId: string; target: NativeSourceTarget; capsuleId: string;
  baseRevision: number; baseCapsule: NativeCapsule | null;
  fields: NativeSourceFields; sequence: number; dirty: boolean;
  /** A bounded, linear list of immutable earlier copies, never nested raw
   * envelopes. Their exact original keys and bytes remain untouched. */
  previousCopies: RecoveryOrigin[];
}
export interface NativeSourceOriginal { key: string; raw: string | null; }
interface Observation { pointerObserved: boolean; pointer: string | null; rawObserved: boolean; raw: string | null; }
interface Checkpoint { raw: string; previousRaw: string | null; previousPointer: string | null; pointerAttempted: boolean; }
interface AuthorCheckpoint { raw: string; origin: RecoveryOrigin; }
export const NATIVE_SOURCE_COPY_PREFIX = "motionwright.native-source-draft.v1:";
export const NATIVE_SOURCE_POINTER_PREFIX = "motionwright.native-source-writer.v1:";
export const MAX_NATIVE_SOURCE_COPY_BYTES = 1024 * 1024;
const fields: NativeSourceField[] = ["path", "name", "mediaType", "label", "parameters", "editorHint"];
const uuid = (value: unknown): value is string => typeof value === "string"
  && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value) && value !== "00000000-0000-0000-0000-000000000000";
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value < Number.MAX_SAFE_INTEGER;
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const exact = (value: Record<string, unknown>, names: string[]) => Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name));
const clone = <T,>(value: T): T => structuredClone(value);
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const newId = () => crypto.randomUUID();
export const nativeSourceScope = (project: Project, sceneId: string, target: NativeSourceTarget) =>
  JSON.stringify([project.id, project.generation, project.active_branch, sceneId, target]);
export const nativeSourceCopyKey = (scope: string, writerId: string) => NATIVE_SOURCE_COPY_PREFIX + encodeURIComponent(scope) + ":" + writerId;
export const nativeSourcePointerKey = (scope: string) => NATIVE_SOURCE_POINTER_PREFIX + encodeURIComponent(scope);

function capsuleAt(project: Project, id: string): NativeCapsule | null {
  const matches = (project.production_design?.capsules ?? []).filter(capsule => capsule.id === id);
  if (matches.length > 1) throw new Error("The native capsule identity is ambiguous. Preserve this input before reviewing the project.");
  return clone(matches[0] ?? null);
}
function checkProject(draft: NativeSourceDraft, project: Project) {
  if (nativeSourceScope(project, draft.sceneId, draft.target) !== draft.scope)
    throw new Error("This native source input belongs to another project, generation, branch or scene.");
  if (!uuid(project.id) || !uuid(project.generation) || !uuid(project.active_branch) || !integer(project.revision)
      || !uuid(draft.sceneId) || project.scenes.filter(scene => scene.id === draft.sceneId).length !== 1)
    throw new Error("Choose an available scene and a current project identity before saving this native source.");
  const current = capsuleAt(project, draft.capsuleId);
  if ((current && current.scene_id !== draft.sceneId) || (!current && (draft.target !== "new" || draft.baseCapsule)))
    throw new Error("The original native capsule is missing or belongs to another scene. Keep its authored input.");
  return current;
}
export function createNativeSourceDraft(project: Project, sceneId: string, target: NativeSourceTarget, capsuleId: string = newId()): NativeSourceDraft {
  if (target !== "new" && !uuid(target)) throw new Error("Choose a canonical native capsule identity.");
  const id = target === "new" ? capsuleId : target;
  if (!uuid(id)) throw new Error("The native source draft needs a stable capsule identity.");
  const base = capsuleAt(project, id), asset = base ? project.assets.find(value => value.id === base.source_asset_id) : null;
  return { schema: "motionwright.native-source-draft/1", scope: nativeSourceScope(project, sceneId, target), sceneId, target, capsuleId: id,
    baseRevision: project.revision, baseCapsule: base, fields: { path: "", name: asset?.name ?? "", mediaType: asset?.media_type ?? "",
      label: base?.label ?? "", parameters: JSON.stringify(base?.editable_parameters ?? [], null, 2), editorHint: base?.native_editor_hint ?? "" },
    sequence: 0, dirty: false, previousCopies: [] };
}
/** JSON remains raw in storage and through every edit/recovery. The save path
 * alone calls this parser; it preserves array order and each authored string. */
export function prepareNativeSourceSpec(input: NativeSourceFields, capsuleId: string, sceneId: string): NativeCapsuleSourceSpec7 {
  let parameters: unknown;
  try { parameters = JSON.parse(input.parameters); }
  catch { throw new Error("Complete the editable parameters JSON array before saving. Your exact input remains retained."); }
  return parseNativeCapsuleSourceSpec({ name: input.name, media_type: input.mediaType, capsule_id: capsuleId, scene_id: sceneId,
    label: input.label, editable_parameters: parameters, native_editor_hint: input.editorHint });
}
export function encodeNativeSourceCopy(draft: NativeSourceDraft, writerId: string): string {
  if (!uuid(writerId)) throw new Error("Invalid native source writer identity.");
  const raw = JSON.stringify({ schema: "motionwright.native-source-copy/1", writerId, draft });
  if (new TextEncoder().encode(raw).length > MAX_NATIVE_SOURCE_COPY_BYTES)
    throw new Error("The native source input exceeds local backup capacity. Download the exact input before continuing.");
  return raw;
}
export function decodeNativeSourceCopy(raw: string, scope: string, writerId: string): NativeSourceDraft {
  if (new TextEncoder().encode(raw).length > MAX_NATIVE_SOURCE_COPY_BYTES) throw new Error("The original native source copy is too large to restore.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || !exact(value, ["schema", "writerId", "draft"]) || value.schema !== "motionwright.native-source-copy/1"
      || value.writerId !== writerId || !uuid(writerId) || !record(value.draft)) throw new Error("The original native source copy is unreadable or has another writer.");
  const draft = value.draft;
  if (!exact(draft, ["schema", "scope", "sceneId", "target", "capsuleId", "baseRevision", "baseCapsule", "fields", "sequence", "dirty", "previousCopies"])
      || draft.schema !== "motionwright.native-source-draft/1" || draft.scope !== scope || typeof draft.sceneId !== "string"
      || (draft.target !== "new" && !uuid(draft.target)) || !uuid(draft.capsuleId)
      || (draft.target !== "new" && draft.target !== draft.capsuleId) || !integer(draft.baseRevision) || !integer(draft.sequence)
      || typeof draft.dirty !== "boolean" || !record(draft.fields) || !exact(draft.fields, fields)
      || fields.some(field => typeof (draft.fields as Record<string, unknown>)[field] !== "string"))
    throw new Error("The original native source input has an unsupported shape. Its exact bytes remain available.");
  if (!Array.isArray(draft.previousCopies) || draft.previousCopies.length > 32
      || draft.previousCopies.some(value => !isRecoveryOrigin(value) || !uuid(value.writerId) || value.writerId === writerId)
      || new Set(draft.previousCopies.map(value => value.writerId)).size !== draft.previousCopies.length)
    throw new Error("The earlier native source checkpoints are unreadable. Preserve the original bytes.");
  const identity: unknown = JSON.parse(scope);
  if (!Array.isArray(identity) || identity.length !== 5 || identity[3] !== draft.sceneId || identity[4] !== draft.target)
    throw new Error("The native source copy does not match this scene and target.");
  if (draft.baseCapsule !== null && (!record(draft.baseCapsule) || draft.baseCapsule.id !== draft.capsuleId
      || draft.baseCapsule.scene_id !== draft.sceneId || !uuid(draft.baseCapsule.source_asset_id)
      || typeof draft.baseCapsule.source_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(draft.baseCapsule.source_sha256)
      || typeof draft.baseCapsule.label !== "string" || typeof draft.baseCapsule.native_editor_hint !== "string"
      || !Array.isArray(draft.baseCapsule.editable_parameters) || draft.baseCapsule.editable_parameters.some(value => typeof value !== "string")
      || !record(draft.baseCapsule.fidelity))) throw new Error("The original native capsule comparison snapshot is unreadable.");
  return clone(draft as unknown as NativeSourceDraft);
}

/** Testable custody used by the hook. It never removes a copy or adopts another
 * writer's copy for writing. Every fresh runtime forks to its own writer. */
export class NativeSourceDraftSession {
  draft: NativeSourceDraft;
  recoveryProblem: string | null = null;
  backupError: string | null = null;
  notice: string | null = null;
  version = 0;
  private project: Project;
  private readonly observation: Observation = { pointerObserved: false, pointer: null, rawObserved: false, raw: null };
  private readonly touched = new Set<NativeSourceField>();
  private readonly listeners = new Set<() => void>();
  private readonly submitted = new Map<number, NativeSourceDraft>();
  private readonly previousObservations = new Map<string, { raw: string | null; verifiedOrigin: RecoveryOrigin | null }>();
  private writerId: string;
  private expectedPointer: string | null = null;
  private expectedRaw: string | null = null;
  private checkpoint: Checkpoint | null = null;
  private authorCheckpoint: AuthorCheckpoint | null = null;
  constructor(project: Project, sceneId: string, target: NativeSourceTarget,
    private readonly storage: NativeSourceStorage, private readonly pointers: NativeSourceStorage, private readonly idFactory: () => string = newId) {
    this.project = project;
    this.draft = createNativeSourceDraft(project, sceneId, target, idFactory());
    this.writerId = idFactory();
    if (!uuid(this.writerId)) throw new Error("Invalid native source writer identity.");
    try { this.recover(); }
    catch (reason) { this.recoveryProblem = this.backupError = message(reason); }
    if (!this.recoveryProblem) this.backup();
  }
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private changed() { this.version++; for (const listener of this.listeners) listener(); }
  updateProject(project: Project) {
    if (nativeSourceScope(project, this.draft.sceneId, this.draft.target) !== this.draft.scope)
      throw new Error("A parked native source draft cannot change project, generation or branch.");
    this.project = project;
  }
  get conflict(): boolean {
    try { return this.project.revision !== this.draft.baseRevision || !sameValue(checkProject(this.draft, this.project), this.draft.baseCapsule); }
    catch { return true; }
  }
  get downloadableOriginals(): NativeSourceOriginal[] {
    const originals: NativeSourceOriginal[] = [];
    const add = (key: string, raw: string | null) => { if (!originals.some(value => value.key === key && value.raw === raw)) originals.push({ key, raw }); };
    const observation = this.observation;
    if (observation.rawObserved && observation.pointer !== null) add(nativeSourceCopyKey(this.draft.scope, observation.pointer), observation.raw);
    else if (this.recoveryProblem && observation.pointerObserved) add(nativeSourcePointerKey(this.draft.scope), observation.pointer);
    if (this.expectedRaw !== null) add(nativeSourceCopyKey(this.draft.scope, this.writerId), this.expectedRaw);
    // This is an exact locally attempted checkpoint, not a claim that storage
    // accepted it. It is retained even if later authored input differs.
    if (this.checkpoint) add(nativeSourceCopyKey(this.draft.scope, this.writerId), this.checkpoint.raw);
    if (this.authorCheckpoint) add(nativeSourceCopyKey(this.draft.scope, this.authorCheckpoint.origin.writerId), this.authorCheckpoint.raw);
    for (const [writerId, previous] of this.previousObservations) add(nativeSourceCopyKey(this.draft.scope, writerId), previous.raw);
    return originals;
  }
  get downloadableInput(): string {
    return JSON.stringify({ schema: "motionwright.native-source-input-download/1", draft: this.draft,
      recovery_problem: this.recoveryProblem, backup_error: this.backupError }, null, 2);
  }
  private recover() {
    const observation = this.observation, pointerKey = nativeSourcePointerKey(this.draft.scope), pointer = this.pointers.getItem(pointerKey);
    if (observation.pointerObserved && observation.pointer !== pointer) throw new Error("The first native source pointer changed. Keep the original copy and current input separate.");
    observation.pointerObserved = true; observation.pointer = pointer;
    if (pointer === null) {
      if (this.pointers.getItem(pointerKey) !== null) throw new Error("The first native source pointer changed while it was being read.");
      this.expectedPointer = null; return;
    }
    if (!uuid(pointer)) throw new Error("The original native source pointer is invalid. Download it before any recovery decision.");
    const key = nativeSourceCopyKey(this.draft.scope, pointer), raw = this.storage.getItem(key);
    if (observation.rawObserved && observation.raw !== raw) throw new Error("The first native source bytes changed. A replacement cannot be adopted by Retry.");
    observation.rawObserved = true; observation.raw = raw;
    if (raw === null) throw new Error("The original native source copy is missing. Its observed absence remains protected.");
    const restored = decodeNativeSourceCopy(raw, this.draft.scope, pointer);
    for (const origin of restored.previousCopies) this.observePrevious(origin);
    if (this.pointers.getItem(pointerKey) !== pointer || this.storage.getItem(key) !== raw)
      throw new Error("The first native source pointer or bytes changed during recovery.");
    const next = clone(restored);
    if (this.touched.size) {
      for (const field of this.touched) next.fields[field] = this.draft.fields[field];
      next.sequence = Math.max(next.sequence, this.draft.sequence) + 1; next.dirty = true;
      if (!integer(next.sequence)) throw new Error("The native source edit sequence is exhausted. Download both inputs.");
    }
    this.draft = next; this.expectedPointer = pointer;
    this.notice = "Recovered the native source input and its stable capsule identity. No import was repeated.";
  }
  private checkOrigin() {
    const observation = this.observation;
    if (observation.rawObserved && observation.pointer !== null
        && this.storage.getItem(nativeSourceCopyKey(this.draft.scope, observation.pointer)) !== observation.raw)
      throw new Error("The first native source bytes changed. Its exact original and current input remain downloadable.");
    for (const previous of this.draft.previousCopies) this.observePrevious(previous);
  }
  private observePrevious(origin: RecoveryOrigin) {
    const key = nativeSourceCopyKey(this.draft.scope, origin.writerId), raw = this.storage.getItem(key);
    let observed = this.previousObservations.get(origin.writerId);
    if (observed && observed.raw !== raw) throw new Error("An earlier native source checkpoint changed. Its first observed bytes remain downloadable.");
    // Pin even null/corrupt bytes before digest validation or another read can fail.
    if (!observed) { observed = { raw, verifiedOrigin: null }; this.previousObservations.set(origin.writerId, observed); }
    if (!sameValue(observed.verifiedOrigin, origin)) {
      if (!matchesRecoveryOrigin(origin, raw)) throw new Error("An earlier native source checkpoint does not match its retained byte anchor. No replacement will be adopted.");
      observed.verifiedOrigin = clone(origin);
    }
    if (this.storage.getItem(key) !== raw) throw new Error("An earlier native source checkpoint changed while being verified.");
  }
  private finishCheckpoint(checkpoint: Checkpoint) {
    const key = nativeSourceCopyKey(this.draft.scope, this.writerId), pointerKey = nativeSourcePointerKey(this.draft.scope);
    this.checkOrigin();
    const pointer = this.pointers.getItem(pointerKey);
    if (pointer !== checkpoint.previousPointer && !(checkpoint.pointerAttempted && pointer === this.writerId))
      throw new Error("A different native source pointer appeared. This editor will not overwrite it.");
    const current = this.storage.getItem(key);
    if (current !== checkpoint.previousRaw && current !== checkpoint.raw)
      throw new Error("Another window changed this native source copy. Its bytes will not be overwritten.");
    if (current !== checkpoint.raw) this.storage.setItem(key, checkpoint.raw);
    if (this.storage.getItem(key) !== checkpoint.raw) throw new Error("The native source input backup was not confirmed.");
    const afterCopyPointer = this.pointers.getItem(pointerKey);
    if (afterCopyPointer !== checkpoint.previousPointer && !(checkpoint.pointerAttempted && afterCopyPointer === this.writerId))
      throw new Error("The native source pointer changed before backup publication.");
    checkpoint.pointerAttempted = true;
    this.pointers.setItem(pointerKey, this.writerId);
    if (this.pointers.getItem(pointerKey) !== this.writerId || this.storage.getItem(key) !== checkpoint.raw)
      throw new Error("The native source backup and pointer were not confirmed together.");
    this.checkOrigin();
    this.expectedRaw = checkpoint.raw; this.expectedPointer = this.writerId; this.checkpoint = null;
  }
  private backup(): boolean {
    try {
      if (this.recoveryProblem) throw new Error(this.recoveryProblem);
      if (this.authorCheckpoint) throw new Error("The original authored checkpoint needs a verified local copy. Retry backup before saving.");
      if (this.checkpoint) this.finishCheckpoint(this.checkpoint);
      const raw = encodeNativeSourceCopy(this.draft, this.writerId);
      this.checkOrigin();
      if (this.pointers.getItem(nativeSourcePointerKey(this.draft.scope)) !== this.expectedPointer
          || this.storage.getItem(nativeSourceCopyKey(this.draft.scope, this.writerId)) !== this.expectedRaw)
        throw new Error("The verified native source copy or pointer changed. Keep the original and current input separate.");
      if (raw !== this.expectedRaw) {
        this.checkpoint = { raw, previousRaw: this.expectedRaw, previousPointer: this.expectedPointer, pointerAttempted: false };
        this.finishCheckpoint(this.checkpoint);
      }
      if (this.pointers.getItem(nativeSourcePointerKey(this.draft.scope)) !== this.writerId
          || this.storage.getItem(nativeSourceCopyKey(this.draft.scope, this.writerId)) !== raw)
        throw new Error("The complete native source input backup has not been verified.");
      this.backupError = null; return true;
    } catch (reason) { this.backupError = message(reason); return false; }
  }
  setField(field: NativeSourceField, value: string): void {
    if (!fields.includes(field) || typeof value !== "string") throw new Error("Unsupported native source input field.");
    if (!integer(this.draft.sequence + 1)) { this.backupError = "The native source edit sequence is exhausted. Download the current input."; this.changed(); return; }
    this.touched.add(field);
    this.draft = { ...this.draft, fields: { ...this.draft.fields, [field]: value }, sequence: this.draft.sequence + 1, dirty: true };
    this.notice = null; this.backup(); this.changed();
  }
  retryBackup(): boolean {
    try {
      if (this.recoveryProblem) { this.recover(); this.recoveryProblem = null; }
      if (this.authorCheckpoint) this.finishAuthorCheckpoint();
      const result = this.backup(); this.changed(); return result;
    } catch (reason) { this.backupError = message(reason); this.changed(); return false; }
  }
  inspectBackup(): void {
    try {
      if (this.recoveryProblem) return;
      this.checkOrigin();
      if (this.pointers.getItem(nativeSourcePointerKey(this.draft.scope)) !== this.expectedPointer
          || this.storage.getItem(nativeSourceCopyKey(this.draft.scope, this.writerId)) !== this.expectedRaw)
        throw new Error("Another window changed native source recovery. The current input remains in this editor.");
    } catch (reason) { this.backupError = message(reason); }
    finally { this.changed(); }
  }
  assertReady(): boolean {
    try {
      checkProject(this.draft, this.project);
      if (this.conflict) throw new Error("Review the current project revision and explicitly rebase this native source input before saving.");
      // This runs only on the explicit save path, never while typing/reloading.
      prepareNativeSourceSpec(this.draft.fields, this.draft.capsuleId, this.draft.sceneId);
      if (this.authorCheckpoint) this.finishAuthorCheckpoint();
      if (!this.backup()) { this.changed(); return false; }
      const submitted = this.submitted.get(this.draft.sequence);
      if (!submitted || submitted.baseRevision !== this.draft.baseRevision || !sameValue(submitted.fields, this.draft.fields)) {
        if (this.draft.previousCopies.length >= 32) throw new Error("The authored checkpoint history is full. Download the exact copies before continuing.");
        const writerId = this.idFactory(), key = nativeSourceCopyKey(this.draft.scope, writerId);
        if (writerId === this.writerId || !uuid(writerId) || this.storage.getItem(key) !== null)
          throw new Error("A separate authored checkpoint needs an unused writer identity. No existing copy was replaced.");
        const raw = encodeNativeSourceCopy(this.draft, writerId);
        this.authorCheckpoint = { raw, origin: recoveryOrigin(writerId, raw) };
        this.finishAuthorCheckpoint();
        // The current pointer must also retain the immutable original's anchor
        // before the caller can request a grant. Later edits use the live writer.
        if (!this.backup()) { this.changed(); return false; }
      }
      this.submitted.set(this.draft.sequence, clone(this.draft));
      this.changed(); return true;
    } catch (reason) {
      this.notice = message(reason);
      if (this.authorCheckpoint) this.backupError = this.notice;
      this.changed(); return false;
    }
  }
  private finishAuthorCheckpoint() {
    const checkpoint = this.authorCheckpoint;
    if (!checkpoint) return;
    this.checkOrigin();
    const pointerKey = nativeSourcePointerKey(this.draft.scope), ownKey = nativeSourceCopyKey(this.draft.scope, this.writerId);
    if (this.pointers.getItem(pointerKey) !== this.expectedPointer || this.storage.getItem(ownKey) !== this.expectedRaw)
      throw new Error("The current authored input custody changed before its original checkpoint could be retained.");
    const key = nativeSourceCopyKey(this.draft.scope, checkpoint.origin.writerId), existing = this.storage.getItem(key);
    if (existing !== null && existing !== checkpoint.raw) throw new Error("The original authored checkpoint was replaced. Its exact attempted bytes remain downloadable.");
    if (existing === null) this.storage.setItem(key, checkpoint.raw);
    if (this.storage.getItem(key) !== checkpoint.raw || this.pointers.getItem(pointerKey) !== this.expectedPointer
        || this.storage.getItem(ownKey) !== this.expectedRaw)
      throw new Error("The original authored input checkpoint was not verified. No import may start.");
    this.previousObservations.set(checkpoint.origin.writerId, { raw: checkpoint.raw, verifiedOrigin: clone(checkpoint.origin) });
    this.draft = { ...this.draft, previousCopies: [...this.draft.previousCopies, checkpoint.origin] };
    this.authorCheckpoint = null;
  }
  /** The App calls this only after FileImportRetention verified the original
   * receipt and published the current Project. This method grants no authority. */
  acknowledge(sequence: number, project: Project): void {
    try {
      const submitted = this.submitted.get(sequence);
      if (!submitted) throw new Error("This native source acknowledgement has no verified authored checkpoint.");
      const current = checkProject(submitted, project);
      if (!current || project.revision !== submitted.baseRevision + 1 || project.revision < this.project.revision
          || this.draft.baseRevision !== submitted.baseRevision)
        throw new Error("A newer project or authored base superseded this acknowledgement. Keep the current input for review.");
      const spec = prepareNativeSourceSpec(submitted.fields, submitted.capsuleId, submitted.sceneId);
      if (current.label !== spec.label || current.native_editor_hint !== spec.native_editor_hint
          || !sameValue(current.editable_parameters, spec.editable_parameters))
        throw new Error("The published native capsule has different authored metadata. Preserve this input for explicit review.");
      this.draft = { ...this.draft, baseRevision: project.revision, baseCapsule: clone(current),
        dirty: this.draft.sequence !== sequence };
      this.project = project; this.submitted.delete(sequence);
      this.notice = this.draft.dirty ? "The earlier import was acknowledged; later native source input remains editable." : "The imported native source is acknowledged. Its exact authored input remains available.";
      this.backup(); this.changed();
    } catch (reason) { this.notice = message(reason); this.changed(); }
  }
  /** Caller MUST first assertNoFileImport(project.id). An outstanding retained
   * import is owned by FileImportRetention, never interpreted by this module. */
  rebase(project: Project): void {
    try {
      const current = checkProject(this.draft, project);
      if (project.revision < this.draft.baseRevision) throw new Error("An older project cannot replace the native source base.");
      if (!this.backup()) { this.changed(); return; }
      if (!integer(this.draft.sequence + 1)) throw new Error("The native source edit sequence is exhausted. Download this input.");
      this.draft = { ...this.draft, baseRevision: project.revision, baseCapsule: current, sequence: this.draft.sequence + 1 };
      this.project = project; this.notice = "Rebased the native source comparison while preserving every authored field and capsule identity. Save this choice separately.";
      this.backup(); this.changed();
    } catch (reason) { this.notice = message(reason); this.changed(); }
  }
  /** Explicit UI action only, after its global assertNoFileImport guard. */
  startAnother(project: Project): void {
    try {
      this.updateProject(project);
      if (this.draft.target !== "new" || this.draft.dirty || this.conflict || !this.draft.baseCapsule)
        throw new Error("Finish and review the current native capsule before explicitly starting another one.");
      if (!this.backup() || this.expectedRaw === null) { this.changed(); return; }
      const next = createNativeSourceDraft(project, this.draft.sceneId, "new", this.idFactory()), writerId = this.idFactory();
      if (next.capsuleId === this.draft.capsuleId || capsuleAt(project, next.capsuleId) || writerId === this.writerId || !uuid(writerId))
        throw new Error("A new native capsule and writer need separate identities.");
      const previous = recoveryOrigin(this.writerId, this.expectedRaw);
      next.previousCopies = [...this.draft.previousCopies, previous];
      if (next.previousCopies.length > 32) throw new Error("Earlier native source copies reached this editor's capacity. Download them before continuing.");
      encodeNativeSourceCopy(next, writerId); // Capacity must be proved before any in-memory transition.
      if (this.storage.getItem(nativeSourceCopyKey(next.scope, writerId)) !== null)
        throw new Error("The next native source writer already has a copy. Nothing was replaced.");
      this.previousObservations.set(this.writerId, { raw: this.expectedRaw, verifiedOrigin: previous });
      this.writerId = writerId; this.expectedRaw = null; this.checkpoint = null; this.draft = next;
      this.touched.clear(); this.submitted.clear();
      if (this.backup()) this.notice = "Started another native capsule explicitly. The earlier capsule and exact input copies remain preserved.";
      else this.notice = "The new input buffer needs a verified recovery copy before saving. The earlier exact input remains downloadable.";
      this.changed();
    } catch (reason) { this.notice = message(reason); this.changed(); }
  }
}

const sessions = new Map<string, NativeSourceDraftSession>();
// Storage getters are lazy: a denied window.localStorage/sessionStorage access
// becomes a recoverable problem inside the session, without losing the fields.
const local: NativeSourceStorage = { getItem: key => window.localStorage.getItem(key), setItem: (key, value) => window.localStorage.setItem(key, value) };
const pointers: NativeSourceStorage = { getItem: key => window.sessionStorage.getItem(key), setItem: (key, value) => window.sessionStorage.setItem(key, value) };
export function useNativeSourceDraft(project: Project, sceneId: string, target: NativeSourceTarget) {
  const scope = nativeSourceScope(project, sceneId, target);
  let session = sessions.get(scope);
  if (!session) { session = new NativeSourceDraftSession(project, sceneId, target, local, pointers); sessions.set(scope, session); }
  session.updateProject(project);
  const current = session;
  useSyncExternalStore(current.subscribe, () => current.version, () => current.version);
  useEffect(() => {
    const changed = (event: StorageEvent) => { if (event.key === null || event.key.startsWith(NATIVE_SOURCE_COPY_PREFIX)) current.inspectBackup(); };
    window.addEventListener("storage", changed);
    return () => window.removeEventListener("storage", changed);
  }, [current]);
  return { fields: { ...current.draft.fields }, capsuleId: current.draft.capsuleId, baseRevision: current.draft.baseRevision,
    dirty: current.draft.dirty, sequence: current.draft.sequence, conflict: current.conflict,
    recoveryProblem: current.recoveryProblem, backupError: current.backupError, notice: current.notice,
    downloadableOriginals: current.downloadableOriginals, downloadableInput: current.downloadableInput,
    setField: (field: NativeSourceField, value: string) => current.setField(field, value),
    assertReady: () => current.assertReady(), acknowledge: (sequence: number, value: Project) => current.acknowledge(sequence, value),
    rebase: (value: Project) => current.rebase(value), startAnother: (value: Project) => current.startAnother(value), retryBackup: () => current.retryBackup() };
}
