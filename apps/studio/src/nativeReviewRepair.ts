/** Source-anchored, view-only bridge from native frame review to an
 * explicitly authored scoped canvas proposal. NOT an effect grant or verdict.
 */
import { nativeFindingBody } from "./nativeInspection";
import type { ManualCreativeFinding, NativeInspectionPlan, NativeSampleEvidence } from "./nativeInspection";
import type { Project, Scene } from "./types";

export interface NativeReviewRepairDraft {
  schema: "motionwright.native-review-repair-draft/1";
  projectId: string;
  generation: string;
  revision: number;
  sceneId: string;
  profileId: string;
  frameIndex: number;
  frameSha256: string;
  targetNodeId: string;
  editKind: "text" | "transform";
  rationale: string;
}

const MAX_RATIONALE_BYTES = 4_000;

export function createNativeReviewRepairDraft(
  project: Project,
  scene: Scene,
  plan: NativeInspectionPlan,
  sample: NativeSampleEvidence,
  finding: ManualCreativeFinding,
): NativeReviewRepairDraft {
  if (!finding.nodeId) {
    throw new Error("Select an affected object before preparing a scoped repair.");
  }
  if (project.id !== plan.projectId || project.generation !== plan.generation ||
      project.revision !== plan.revision || scene.id !== plan.sceneId) {
    throw new Error("Native review source is stale or belongs to another project/scene.");
  }
  // Validates frame selection and PNG digest, not quality or source authenticity.
  nativeFindingBody(plan, sample, finding);
  const node = project.scenes.find((current) => current.id === scene.id)
    ?.nodes.find((candidate) => candidate.id === finding.nodeId);
  if (!node || !scene.nodes.some((candidate) => candidate.id === node.id)) {
    throw new Error("The selected repair object is outside the current scene.");
  }
  const rationale = [
    "Manual native-frame observation; repair NOT verified",
    "Project " + plan.projectId + " generation " + plan.generation + " revision " + plan.revision,
    "Scene " + plan.sceneId + " profile " + plan.profileId,
    "Target object " + node.id + "; severity " + finding.severity + "; confidence " + finding.confidence,
    "Frame " + sample.frameIndex + " time " + sample.timelineTime.num + "/" + sample.timelineTime.den + " s; PNG SHA-256 " + sample.pngSha256,
    "Constraint: " + finding.violatedConstraint,
    "Observed: " + finding.observation,
    "Suggested manual repair: " + finding.proposedRepair,
  ].join("\n");
  if (new TextEncoder().encode(rationale).length > MAX_RATIONALE_BYTES) {
    throw new Error("Native repair note exceeds the bounded creative rationale budget.");
  }
  return {
    schema: "motionwright.native-review-repair-draft/1",
    projectId: plan.projectId,
    generation: plan.generation,
    revision: plan.revision,
    sceneId: plan.sceneId,
    profileId: plan.profileId,
    frameIndex: sample.frameIndex,
    frameSha256: sample.pngSha256,
    targetNodeId: node.id,
    editKind: node.kind === "text" ? "text" : "transform",
    rationale,
  };
}

/** UI guard only; native Studio service separately enforces CAS and locks. */
export function nativeRepairDraftIsCurrent(
  project: Project,
  scene: Scene | null,
  draft: NativeReviewRepairDraft,
): boolean {
  return draft.schema === "motionwright.native-review-repair-draft/1" &&
    project.id === draft.projectId &&
    project.generation === draft.generation &&
    project.revision === draft.revision &&
    scene !== null && scene.id === draft.sceneId &&
    scene.nodes.some((node) => node.id === draft.targetNodeId) &&
    project.scenes.some((current) => current.id === scene.id &&
      current.nodes.some((node) => node.id === draft.targetNodeId)) &&
    /^[a-f0-9]{64}$/.test(draft.frameSha256);
}
