import type { NativeFrameSelection } from "./nativeFrameSelection";
import type {
  DeliverableProfile, MotionCanvasFilmOptions, NativeWindowRenderResponse,
  Project, RationalTime, Scene, SourceFrameWindow,
} from "./types";

export const NATIVE_WINDOW_MAX_SOURCE_FRAME = 1_080_000;
export const NATIVE_WINDOW_MAX_SELECTED_FRAMES = 4_096;
export const NATIVE_WINDOW_MAX_PREFIX_FRAMES = 72_000;
const MAX_SAFE = BigInt(Number.MAX_SAFE_INTEGER);
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const same = (left: unknown, right: unknown): boolean => {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) && Array.isArray(right)) return left.length === right.length && left.every((value, index) => same(value, right[index]));
  if (!record(left) || !record(right)) return false;
  const names = Object.keys(left);
  return names.length === Object.keys(right).length && names.every(name => Object.hasOwn(right, name) && same(left[name], right[name]));
};
const text = (value: unknown): value is string => typeof value === "string" && value.length > 0 && value.length <= 512 && !/[\x00-\x1f\x7f]/.test(value);
function require(condition: unknown, message: string): asserts condition { if (!condition) throw new Error(message); }
function keys(value: unknown, expected: string[]): asserts value is Record<string, unknown> {
  require(record(value) && Object.keys(value).length === expected.length && expected.every(key => Object.hasOwn(value, key)),
    "The native window response has an unsupported shape.");
}

type Fraction = { num: bigint; den: bigint };
function fraction(value: RationalTime): Fraction {
  require(typeof value?.num === "string" && typeof value?.den === "string"
    && value.num.length <= 20 && value.den.length <= 20
    && /^-?(0|[1-9][0-9]*)$/.test(value.num) && /^[1-9][0-9]*$/.test(value.den), "The saved source clock is invalid.");
  return { num: BigInt(value.num), den: BigInt(value.den) };
}
function add(left: Fraction, right: Fraction): Fraction {
  let num = left.num * right.den + right.num * left.den;
  let den = left.den * right.den;
  let a = num < 0n ? -num : num, b = den;
  while (b) { const next = a % b; a = b; b = next; }
  if (a) { num /= a; den /= a; }
  return { num, den };
}
function savedTime(value: Fraction): RationalTime { return { num: value.num.toString(), den: value.den.toString() }; }
function rate(value: MotionCanvasFilmOptions["frame_rate"]) {
  require(integer(value?.num) && integer(value?.den) && value.num > 0 && value.den > 0
    && value.num / value.den >= 1 && value.num / value.den <= 120, "The saved native frame rate is invalid.");
  return value;
}
function exactFrame(time: Fraction, fps: MotionCanvasFilmOptions["frame_rate"]): number {
  rate(fps);
  const num = time.num * BigInt(fps.num), den = time.den * BigInt(fps.den);
  require(num >= 0n && den > 0n && num % den === 0n && num / den <= MAX_SAFE,
    "Native window scene boundaries must lie on exact saved source frames.");
  return Number(num / den);
}
export function nativeWindowSceneBounds(scene: Scene, fps: MotionCanvasFilmOptions["frame_rate"]): SourceFrameWindow {
  require(scene?.renderer === "motion-canvas", "The selected scene needs a Motion Canvas source.");
  const start = fraction(scene.start), duration = fraction(scene.duration);
  require(duration.num > 0n, "The saved source scene has no positive duration.");
  const bounds = { start: exactFrame(start, fps), end: exactFrame(add(start, duration), fps) };
  require(bounds.start < bounds.end, "The selected source scene has no frames.");
  return bounds;
}
function checkRange(value: SourceFrameWindow): void {
  require(integer(value?.start) && integer(value?.end) && value.start < value.end
    && value.end <= NATIVE_WINDOW_MAX_SOURCE_FRAME
    && value.end - value.start <= NATIVE_WINDOW_MAX_SELECTED_FRAMES,
    "Choose a nonempty source window of at most 4096 frames, ending by source frame 1080000.");
}
export function parseNativeWindowRange(scene: Scene, fps: MotionCanvasFilmOptions["frame_rate"], startRaw: string, endRaw: string): SourceFrameWindow {
  require(typeof startRaw === "string" && typeof endRaw === "string" && startRaw.length <= 16 && endRaw.length <= 16
    && /^[0-9]+$/.test(startRaw) && /^[0-9]+$/.test(endRaw), "Enter complete nonnegative integer source-frame bounds.");
  const selected = { start: Number(startRaw), end: Number(endRaw) };
  checkRange(selected);
  const source = nativeWindowSceneBounds(scene, fps);
  require(selected.start >= source.start && selected.end <= source.end, "The window must stay inside this original source scene.");
  return selected;
}
function currentScene(project: Project, scene: Scene, profile: DeliverableProfile): void {
  require(project.scenes.filter(value => value.id === scene.id).length === 1
    && project.deliverables.filter(value => value.id === profile.id).length === 1
    && same(project.scenes.find(value => value.id === scene.id), scene)
    && same(project.deliverables.find(value => value.id === profile.id), profile), "The window selection is not the saved project selection.");
  require(profile.included_scene_ids.length === 0 || profile.included_scene_ids.includes(scene.id), "The selected source scene is excluded from this delivery cut.");
}
/** Selected-cut time includes every preceding cut scene, even another renderer.
 * Segment-local time comes only from the backend's realized window response.
 */
export function nativeWindowCutRange(project: Project, scene: Scene, profile: DeliverableProfile,
  selected: SourceFrameWindow, fps: MotionCanvasFilmOptions["frame_rate"]): { start: RationalTime; end: RationalTime } {
  currentScene(project, scene, profile); checkRange(selected);
  const source = nativeWindowSceneBounds(scene, fps);
  require(selected.start >= source.start && selected.end <= source.end, "Source window crossed the selected scene.");
  const ids = profile.included_scene_ids.length ? profile.included_scene_ids : project.scenes.map(value => value.id);
  require(new Set(ids).size === ids.length, "The saved delivery cut repeats a scene.");
  let preceding: Fraction = { num: 0n, den: 1n };
  let found = false;
  for (const id of ids) {
    const candidate = project.scenes.find(value => value.id === id);
    require(candidate, "The saved delivery cut references a missing scene.");
    if (id === scene.id) { found = true; break; }
    const duration = fraction(candidate.duration);
    require(duration.num > 0n, "The saved delivery cut has an invalid duration.");
    preceding = add(preceding, duration);
  }
  require(found, "The source scene is absent from the saved delivery cut.");
  const at = (frame: number) => savedTime(add(preceding, {
    num: BigInt(frame - source.start) * BigInt(fps.den), den: BigInt(fps.num),
  }));
  return { start: at(selected.start), end: at(selected.end) };
}

export interface NativeWindowIntent {
  project_id: string; generation: string; revision: number; branch_id: string;
  request_id: string; reuse_token: string | null;
  window: { deliverable_id: string; source_scene_id: string; source_window: SourceFrameWindow; options: MotionCanvasFilmOptions };
}
export interface NativeWindowReceipt { kind: "window"; intent: NativeWindowIntent; response: NativeWindowRenderResponse }
export interface NativeWindowFrameSelection extends NativeFrameSelection {
  sourceFrame: number; segmentFrame: number; cutTime: RationalTime;
}

export function createNativeWindowIntent(project: Project, scene: Scene, profile: DeliverableProfile,
  selected: SourceFrameWindow, options: MotionCanvasFilmOptions, requestId: string, reuseToken: string | null = null): NativeWindowIntent {
  currentScene(project, scene, profile); rate(options.frame_rate); checkRange(selected);
  require(options.frame_rate.num === Number(profile.frame_rate.num) && options.frame_rate.den === Number(profile.frame_rate.den),
    "Window options use a different saved delivery frame rate.");
  parseNativeWindowRange(scene, options.frame_rate, String(selected.start), String(selected.end));
  require(integer(project.revision) && text(project.id) && text(project.generation) && text(project.active_branch), "The saved project scope is invalid.");
  require(text(requestId) && requestId.trim() === requestId && requestId.length <= 96, "A new bounded original request identity is required.");
  require(reuseToken === null || text(reuseToken), "The original window token is invalid.");
  return structuredClone({ project_id: project.id, generation: project.generation, revision: project.revision,
    branch_id: project.active_branch, request_id: requestId, reuse_token: reuseToken,
    window: { deliverable_id: profile.id, source_scene_id: scene.id, source_window: selected, options } });
}
function intentIdentity(intent: NativeWindowIntent): unknown {
  return [intent.project_id, intent.generation, intent.revision, intent.branch_id, intent.window];
}
export function nativeWindowIntentMatches(intent: NativeWindowIntent, project: Project, scene: Scene,
  profile: DeliverableProfile, selected: SourceFrameWindow, options: MotionCanvasFilmOptions): boolean {
  try { return same(intentIdentity(intent), intentIdentity(createNativeWindowIntent(project, scene, profile, selected, options, intent.request_id, intent.reuse_token))); }
  catch { return false; }
}

/** Validate the closed wire projection against the original request, not a
 * manufactured Full receipt. A window can retain PASS checks and stays preview-only.
 */
export function acceptNativeWindowReceipt(intent: NativeWindowIntent, input: unknown): NativeWindowReceipt {
  checkRange(intent.window.source_window); rate(intent.window.options.frame_rate);
  keys(input, ["window", "preview"]);
  const summary = input.window, grant = input.preview;
  keys(summary, ["generation", "revision", "deliverable_id", "source_scene_id", "source_window", "segment_window", "original_prefix", "frame_rate", "preview_only", "master_eligible", "validation", "cost"]);
  keys(grant, ["token", "segment_id", "scene_ids", "frame_count"]);
  require(summary.generation === intent.generation && summary.revision === intent.revision
    && summary.deliverable_id === intent.window.deliverable_id && summary.source_scene_id === intent.window.source_scene_id
    && summary.preview_only === true && summary.master_eligible === false, "Native window response belongs to another source or claims master authority.");
  for (const value of [summary.source_window, summary.segment_window, summary.original_prefix]) {
    keys(value, ["start", "end"]);
    require(integer(value.start) && integer(value.end) && value.start < value.end, "Native window response has invalid half-open frame bounds.");
  }
  keys(summary.frame_rate, ["num", "den"]);
  require(same(summary.source_window, intent.window.source_window) && same(summary.frame_rate, intent.window.options.frame_rate), "Native window source range or rate differs from the original request.");
  const source = summary.source_window as unknown as SourceFrameWindow;
  const segment = summary.segment_window as unknown as SourceFrameWindow;
  const prefix = summary.original_prefix as unknown as SourceFrameWindow;
  const count = source.end - source.start;
  require(count > 0 && count <= NATIVE_WINDOW_MAX_SELECTED_FRAMES && segment.end - segment.start === count
    && prefix.start === 0 && prefix.end === segment.end && prefix.end <= NATIVE_WINDOW_MAX_PREFIX_FRAMES,
    "Native window coverage differs from its original prefix or selected frame count.");
  require(text(grant.token) && text(grant.segment_id) && Array.isArray(grant.scene_ids) && grant.scene_ids.length === 1
    && grant.scene_ids[0] === intent.window.source_scene_id && grant.frame_count === count,
    "The native window grant does not identify these selected frames.");
  const validation = summary.validation;
  keys(validation, ["verdict", "native_frame_coverage", "pass_checks", "unknown_checks", "unknown_findings"]);
  require((validation.verdict === "PASS" || validation.verdict === "UNKNOWN")
    && (validation.native_frame_coverage === "PASS" || validation.native_frame_coverage === "UNKNOWN")
    && integer(validation.pass_checks) && integer(validation.unknown_checks) && integer(validation.unknown_findings)
    && validation.pass_checks + validation.unknown_checks > 0, "Native window validation is absent or failed.");
  require(!(validation.verdict === "PASS" && (validation.unknown_checks > 0 || validation.unknown_findings > 0))
    && !(validation.native_frame_coverage === "UNKNOWN" && (validation.verdict !== "UNKNOWN" || validation.unknown_checks === 0))
    && !(validation.native_frame_coverage === "PASS" && validation.pass_checks === 0),
    "Native window UNKNOWN validation was promoted to PASS.");
  const cost = summary.cost;
  keys(cost, ["operation", "original_prefix_pngs", "selected_pngs", "reused_original_pngs", "start_calls_observed", "cached_png_bytes_returned", "cache_payload_bytes_admitted", "cache_micros", "internal_simulation_steps", "internal_simulation_observation"]);
  require(["original_prefix_pngs", "selected_pngs", "reused_original_pngs", "start_calls_observed", "cached_png_bytes_returned", "cache_payload_bytes_admitted", "cache_micros"].every(key => integer(cost[key]))
    && cost.original_prefix_pngs === prefix.end && cost.selected_pngs === count
    && (cost.cached_png_bytes_returned as number) <= 64 * 1024 * 1024 && (cost.cache_payload_bytes_admitted as number) <= 64 * 1024 * 1024
    && cost.internal_simulation_steps === null && cost.internal_simulation_observation === "not_observed",
    "Native window costs are not the bounded original observations.");
  if (intent.reuse_token !== null) {
    require(grant.token === intent.reuse_token && cost.operation === "explicit_original_reuse"
      && cost.start_calls_observed === 0 && cost.reused_original_pngs === count && cost.cache_payload_bytes_admitted === 0
      && (cost.cached_png_bytes_returned as number) > 0,
      "Warm reuse was replaced by another handle or a fresh native capture.");
  } else {
    require(cost.operation === "native_prefix" && (cost.start_calls_observed === 0 || cost.start_calls_observed === 1)
      && cost.reused_original_pngs === 0 && cost.cached_png_bytes_returned === 0
      && (cost.cache_payload_bytes_admitted as number) > 0,
      "The original prefix response has inconsistent capture observations.");
  }
  return { kind: "window", intent: structuredClone(intent), response: structuredClone(input) as unknown as NativeWindowRenderResponse };
}

export function nativeWindowReuseToken(intent: NativeWindowIntent, receipt: NativeWindowReceipt | null): string | null {
  try {
    if (!receipt || receipt.kind !== "window" || !same(intentIdentity(intent), intentIdentity(receipt.intent))) return null;
    acceptNativeWindowReceipt(receipt.intent, receipt.response);
    return receipt.response.preview.token;
  } catch { return null; }
}
export function nativeWindowFrameAtPlayhead(project: Project, scene: Scene | null, profile: DeliverableProfile | null,
  receipt: NativeWindowReceipt | null, playhead: number): NativeWindowFrameSelection | null {
  try {
    if (!scene || !profile || !receipt || receipt.kind !== "window" || !Number.isFinite(playhead) || playhead < 0) return null;
    const { intent } = receipt;
    if (!nativeWindowIntentMatches(intent, project, scene, profile, intent.window.source_window, intent.window.options)) return null;
    const accepted = acceptNativeWindowReceipt(intent, receipt.response);
    const { source_window: source, segment_window: segment, frame_rate: fps } = accepted.response.window;
    const sourceFrame = Math.floor(playhead * fps.num / fps.den + 1e-7);
    if (!integer(sourceFrame) || sourceFrame < source.start || sourceFrame >= source.end) return null;
    const frameIndex = sourceFrame - source.start;
    const cut = nativeWindowCutRange(project, scene, profile, { start: sourceFrame, end: sourceFrame + 1 }, fps);
    return { token: accepted.response.preview.token, segmentId: accepted.response.preview.segment_id,
      frameIndex, frameCount: source.end - source.start, sourceFrame, segmentFrame: segment.start + frameIndex, cutTime: cut.start };
  } catch { return null; }
}

/** An uncertainty marker only prevents dispatch. It never restores a token,
 * pixels or permission. Confirm it synchronously before the one native call.
 */
export const NATIVE_WINDOW_UNCERTAINTY_PREFIX = "motionwright.native-window-uncertainty.v1:";
export interface NativeWindowUncertaintyClaim { key: string; raw: string; requestId: string }
type WindowStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem" | "removeItem">;
function uncertaintyScope(projectId: string, generation: string, branch: string, revision: number): string {
  return NATIVE_WINDOW_UNCERTAINTY_PREFIX + encodeURIComponent(JSON.stringify([projectId, generation, branch, revision]));
}
function uncertaintyKey(intent: NativeWindowIntent): string {
  return uncertaintyScope(intent.project_id, intent.generation, intent.branch_id, intent.revision) + ":" + encodeURIComponent(intent.request_id);
}
function uncertaintyInventory(storage: WindowStorage, scope: string): Array<{ key: string; raw: string }> {
  const scan = () => {
    const count = storage.length;
    require(integer(count) && count <= 8_192, "The local window inventory exceeds its inspection bound; no capture was dispatched.");
    const names: string[] = [];
    for (let index = 0; index < count; index++) {
      const name = storage.key(index);
      require(typeof name === "string", "The local window inventory changed while being inspected; all copies were preserved.");
      names.push(name);
    }
    require(storage.length === count && new Set(names).size === count,
      "The local window inventory was incomplete; all copies were preserved.");
    return names.sort();
  };
  const before = scan();
  const found = before.filter(name => name === scope || name.startsWith(scope + ":")).map(key => {
    const raw = storage.getItem(key);
    require(raw !== null, "An original window copy changed during inspection; no capture was dispatched.");
    return { key, raw };
  });
  require(same(before, scan()) && found.every(entry => storage.getItem(entry.key) === entry.raw),
    "The local window inventory changed during inspection; all copies were preserved.");
  return found;
}
function decodeWindowUncertainty(key: string, raw: string, project: Project): NativeWindowIntent {
  require(raw.length <= 32_768, "The original native window marker is unreadable; it was preserved.");
  let value: unknown;
  try { value = JSON.parse(raw); } catch { throw new Error("The original native window marker is unreadable; it was preserved."); }
  keys(value, ["schema", "intent"]);
  require(value.schema === "motionwright.native-window-uncertainty/1" && record(value.intent), "The native window uncertainty copy cannot be verified.");
  const intent = value.intent as unknown as NativeWindowIntent;
  require(intent.project_id === project.id && intent.generation === project.generation && intent.branch_id === project.active_branch
    && intent.revision === project.revision && intent.reuse_token === null && text(intent.request_id) && record(intent.window),
    "The native window uncertainty copy has a different original scope.");
  const scene = project.scenes.find(item => item.id === intent.window.source_scene_id);
  const profile = project.deliverables.find(item => item.id === intent.window.deliverable_id);
  require(scene && profile, "The original native window source is missing; its marker was preserved.");
  const verified = createNativeWindowIntent(project, scene, profile, intent.window.source_window, intent.window.options, intent.request_id);
  require(key === uncertaintyKey(verified) && same(verified, intent), "The native window uncertainty copy is incomplete or altered.");
  return verified;
}
export function readNativeWindowUncertainties(storage: WindowStorage, project: Project): NativeWindowIntent[] {
  const scope = uncertaintyScope(project.id, project.generation, project.active_branch, project.revision);
  return uncertaintyInventory(storage, scope).map(entry => decodeWindowUncertainty(entry.key, entry.raw, project));
}
export function markNativeWindowUncertain(storage: WindowStorage, intent: NativeWindowIntent): NativeWindowUncertaintyClaim {
  require(intent.reuse_token === null && text(intent.request_id), "A warm read must not be recorded as a fresh native capture.");
  const scope = uncertaintyScope(intent.project_id, intent.generation, intent.branch_id, intent.revision);
  const key = uncertaintyKey(intent);
  const raw = JSON.stringify({ schema: "motionwright.native-window-uncertainty/1", intent });
  require(raw.length <= 32_768, "The original window intent exceeds its local recovery bound.");
  require(storage.getItem(key) === null, "An original window outcome is unresolved at this saved revision; inspect it before another capture.");
  try {
    storage.setItem(key, raw);
    require(storage.getItem(key) === raw, "The original window uncertainty copy was not confirmed; no native capture was dispatched.");
    // Each original owns its bytes. Inventory is checked after publication so
    // an already-published peer blocks this dispatch; this is not a cross-tab CAS.
    const inventory = uncertaintyInventory(storage, scope);
    require(inventory.length === 1 && inventory[0].key === key && inventory[0].raw === raw,
      "Another original window attempt is retained at this revision; this capture was not dispatched.");
    return { key, raw, requestId: intent.request_id };
  } catch (reason) {
    // The live caller cannot invoke native until this function returns. A
    // failed publication/inventory is therefore known NOT_SENT, unlike a
    // recovered marker. Remove only our exact bytes; never repair a peer.
    try {
      const retained = storage.getItem(key);
      if (retained !== null) {
        require(retained === raw, "The unsubmitted local copy changed and was preserved.");
        storage.removeItem(key);
        require(storage.getItem(key) === null, "The unsubmitted local copy remains retained because its removal was not confirmed.");
      }
    } catch (cleanupReason) {
      const message = reason instanceof Error ? reason.message : "The window intent could not be confirmed before dispatch.";
      const cleanup = cleanupReason instanceof Error ? cleanupReason.message : "Its local copy could not be safely removed.";
      throw new Error(message + " " + cleanup);
    }
    throw reason;
  }
}
export function clearConfirmedNativeWindow(storage: WindowStorage, claim: NativeWindowUncertaintyClaim): void {
  require(storage.getItem(claim.key) === claim.raw, "The original window copy changed; it was preserved.");
  storage.removeItem(claim.key);
  require(storage.getItem(claim.key) === null, "The completed window marker could not be cleared; no capture will be retried.");
}
