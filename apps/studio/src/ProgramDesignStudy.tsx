import CompositionStudy from "./CompositionStudy";
import { inheritedTextInk } from "./canvasInheritedInk";
import type { DeliverableProfile, Project, Scene } from "./types";

/** This is a bounded source-Canvas representation, never a substitute for
 * the separately verified native PNG or AV review stages. */
export function hasEditableProgramDesign(scene: Scene | null): boolean {
  return scene !== null && scene.renderer === "motion-canvas"
    && scene.nodes.some(node => ["text", "shape", "rectangle", "circle"].includes(node.kind));
}

/** The same cut inclusion rule as Film: an empty scene list means the
 * complete saved sequence; a nonempty list is an explicit output subset.
 * Source editorial inspection is still allowed for excluded scenes. */
export function programSceneCutScope(
  scene: Scene, profile: DeliverableProfile | null,
): "unselected" | "full" | "included" | "excluded" {
  if (!profile) return "unselected";
  if (profile.included_scene_ids.length === 0) return "full";
  return profile.included_scene_ids.includes(scene.id) ? "included" : "excluded";
}

export default function ProgramDesignStudy({
  project, scene, time, profile,
}: {
  project: Project; scene: Scene; time: number; profile: DeliverableProfile | null;
}) {
  const surface = project.visual_language.palette.find(token =>
    ["surface", "background"].includes(token.name.toLowerCase()))?.value ?? "#0F1216";
  const ink = inheritedTextInk(project.visual_language.palette);
  const cutScope = programSceneCutScope(scene, profile);
  return <div className="program-design-study" aria-label="Saved Canvas editorial design monitor"
    data-source-revision={project.revision} data-selected-profile-id={profile?.id ?? ""}
    data-output-cut-scope={cutScope}>
    <CompositionStudy nodes={scene.nodes} width={1920} height={1080} time={time}
      background={surface} ink={ink} textOverrides={profile?.text_overrides}
      label="Program Canvas editorial study: not native output"/>
    <p className="program-design-disclosure">
      DESIGN_PREVIEW · EDITORIAL SOURCE CANVAS · {profile ? profile.name : "No selected output profile"}
      {cutScope === "excluded" &&
        <strong role="status"> · SCENE EXCLUDED FROM SELECTED OUTPUT CUT ·
          This editable source scene will not be exported in this profile.</strong>}
      {cutScope === "included" && " · Scene included in selected cut"}
      {cutScope === "full" && " · Complete saved sequence"}
      {" · "}Native frames, crop, fonts and final AV require separate verification.
    </p>
  </div>;
}
