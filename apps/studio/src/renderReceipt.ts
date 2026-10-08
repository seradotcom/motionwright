import type {
  DeliverableProfile,
  MotionCanvasRenderEvidence,
  MotionCanvasSegmentEvidence,
  Project,
  Scene,
} from "./types";

/**
 * A session-scoped *metadata* association, not a way to read native frames.
 * Only the Tauri production completion path supplies native render evidence.
 */
export type SceneRenderReceipt =
  | { kind: "current"; segment: MotionCanvasSegmentEvidence; profileName: string; revision: number }
  | { kind: "stale"; segment: MotionCanvasSegmentEvidence; profileName: string; revision: number }
  | { kind: "other_profile"; segment: MotionCanvasSegmentEvidence; profileName: string; revision: number };

export function resolveSceneRenderReceipt(
  project: Project,
  scene: Scene | null,
  selectedDisplayProfile: DeliverableProfile | null,
  evidence: MotionCanvasRenderEvidence | null,
): SceneRenderReceipt | null {
  if (!scene || scene.renderer !== "motion-canvas" || !evidence) return null;
  if (
    evidence.project_resource !== "project:" + project.id
    || evidence.generation !== project.generation
    || !Number.isSafeInteger(evidence.revision)
    || evidence.revision < 0
  ) return null;

  const sourceProfile = project.deliverables.find((profile) => profile.id === evidence.deliverable_id);
  if (!sourceProfile) return null;
  const segment = evidence.segments.find((candidate) =>
    candidate.scene_ids.includes(scene.id)
    && candidate.segment_id.length > 0
    && candidate.job_ref.length > 0
    && Number.isSafeInteger(candidate.frame_count)
    && candidate.frame_count > 0
  );
  if (!segment) return null;
  const common = { segment, profileName: sourceProfile.name, revision: evidence.revision };
  if (evidence.revision !== project.revision) return { kind: "stale", ...common };
  if (selectedDisplayProfile?.id !== evidence.deliverable_id) {
    return { kind: "other_profile", ...common };
  }
  return { kind: "current", ...common };
}
