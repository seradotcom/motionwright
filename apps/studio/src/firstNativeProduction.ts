/** v0.1 first-owner-render gate. Pure advisory/UI dispatch guard only.
 * It never grants Semwright Broker authority or asserts native provider health.
 */
import type {
  MotionCanvasProjectionPreflight,
  ProductionRuntimeCompatibility,
  Project,
} from "./types";

export type FirstRenderGateCode =
  | "BROWSER_ONLY"
  | "PROFILE_NOT_SAVED"
  | "SCENE_INTENT_INCOMPLETE"
  | "VISUAL_TOKENS_MISSING"
  | "FILM_PREFLIGHT_REQUIRED"
  | "FILM_PREFLIGHT_REJECTED"
  | "FILM_PREFLIGHT_STALE"
  | "RUNTIME_NOT_CHECKED"
  | "RUNTIME_OBSERVATION_STALE"
  | "RUNTIME_NOT_COMPATIBLE"
  | "ELIGIBLE_FOR_AUTHORIZED_DISPATCH";

export interface FirstRenderGate {
  code: FirstRenderGateCode;
  may_request_native_render: boolean;
  explanation: string;
  native_render_verified: false;
  broker_health_attested: false;
}

export interface ScopedRuntimeObservation {
  scope: string;
  value: ProductionRuntimeCompatibility;
}

export function firstRenderRuntimeScope(project: Project): string {
  return JSON.stringify([project.id, project.generation, project.revision]);
}

function blocked(code: Exclude<FirstRenderGateCode, "ELIGIBLE_FOR_AUTHORIZED_DISPATCH">,
  explanation: string): FirstRenderGate {
  return { code, may_request_native_render: false, explanation,
    native_render_verified: false, broker_health_attested: false };
}

const DIGEST = /^[0-9a-f]{64}$/;
const PIN = /^[0-9a-f]{40}$/;

/** Untrusted Studio data cannot elevate this to actual execution authority.
 * Tauri and pinned Semwright policy still enforce scope, effect grants and
 * provider provenance on every execution.
 */
export function evaluateFirstNativeRenderGate(input: {
  project: Project;
  profileId: string | null;
  desktopMode: boolean;
  profileDirty: boolean;
  sceneIntentComplete: boolean;
  nativePaletteReady: boolean;
  preflight: MotionCanvasProjectionPreflight | null;
  runtime: ScopedRuntimeObservation | null;
}): FirstRenderGate {
  const { project, profileId, desktopMode, profileDirty,
    sceneIntentComplete, nativePaletteReady, preflight, runtime } = input;
  if (!desktopMode) return blocked("BROWSER_ONLY",
    "Install Motionwright to use the trusted Semwright native runtime. Browser mode cannot render.");
  if (!profileId || profileDirty || !project.deliverables.some(p => p.id === profileId)) {
    return blocked("PROFILE_NOT_SAVED", "Save a valid delivery profile at the current project revision.");
  }
  if (!sceneIntentComplete) return blocked("SCENE_INTENT_INCOMPLETE",
    "Give every included Motion Canvas scene an explicit narrative role and archetype.");
  if (!nativePaletteReady) return blocked("VISUAL_TOKENS_MISSING",
    "Specify ink/text and background/surface palette tokens in Visual language.");
  if (!preflight) return blocked("FILM_PREFLIGHT_REQUIRED",
    "Run the read-only Film projection check for these exact authoring inputs.");
  if (preflight.project_resource !== "project:" + project.id
      || preflight.generation !== project.generation
      || preflight.revision !== project.revision
      || preflight.deliverable_id !== profileId) {
    return blocked("FILM_PREFLIGHT_STALE",
      "Film evidence belongs to another project, generation, revision or output profile.");
  }
  if (preflight.verdict !== "projection_ready"
      || !Number.isSafeInteger(preflight.segment_count) || preflight.segment_count < 1
      || preflight.segments.length !== preflight.segment_count
      || !Number.isSafeInteger(preflight.total_frames) || preflight.total_frames < 1
      || preflight.segments.some(segment => !Number.isSafeInteger(segment.frame_count)
          || segment.frame_count < 1)
      || preflight.segments.reduce((sum, segment) => sum + segment.frame_count, 0) !== preflight.total_frames) {
    return blocked("FILM_PREFLIGHT_REJECTED",
      "The Film projection has not verified a nonempty, internally consistent source-frame plan.");
  }
  if (!runtime) return blocked("RUNTIME_NOT_CHECKED",
    "Check the owner-selected Semwright CLI for this source revision before rendering.");
  if (runtime.scope !== firstRenderRuntimeScope(project)) {
    return blocked("RUNTIME_OBSERVATION_STALE",
      "The Semwright compatibility observation belongs to a different project revision.");
  }
  if (runtime.value.status !== "ready"
      || runtime.value.version_compatible !== true
      || runtime.value.observed_version !== runtime.value.expected_version
      || !DIGEST.test(runtime.value.executable_sha256 ?? "")
      || !DIGEST.test(runtime.value.connection_identity ?? "")
      || !PIN.test(runtime.value.pinned_revision)) {
    return blocked("RUNTIME_NOT_COMPATIBLE",
      "The selected runtime is missing, stale, invalid or not locally version-compatible.");
  }
  return {
    code: "ELIGIBLE_FOR_AUTHORIZED_DISPATCH",
    may_request_native_render: true,
    explanation:
      "Film inputs and digest-pinned CLI compatibility are locally checked. Broker health, native render success and effects are NOT yet verified; execution still requires an explicit one-time grant and canonical Semwright policy.",
    native_render_verified: false,
    broker_health_attested: false,
  };
}
