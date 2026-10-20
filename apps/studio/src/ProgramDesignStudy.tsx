import CompositionStudy from "./CompositionStudy";
import { inheritedTextInk } from "./canvasInheritedInk";
import type { DeliverableProfile, Project, Scene } from "./types";

/** This is a bounded source-Canvas representation, never a substitute for
 * the separately verified native PNG or AV review stages. */
export function hasEditableProgramDesign(scene: Scene | null): boolean {
  return scene !== null && scene.renderer === "motion-canvas"
    && scene.nodes.some(node => ["text", "shape", "rectangle", "circle"].includes(node.kind));
}

export default function ProgramDesignStudy({
  project, scene, time, profile,
}: {
  project: Project; scene: Scene; time: number; profile: DeliverableProfile | null;
}) {
  const surface = project.visual_language.palette.find(token =>
    ["surface", "background"].includes(token.name.toLowerCase()))?.value ?? "#0F1216";
  const ink = inheritedTextInk(project.visual_language.palette);
  return <div className="program-design-study" aria-label="Saved Canvas editorial design monitor"
    data-source-revision={project.revision} data-selected-profile-id={profile?.id ?? ""}>
    <CompositionStudy nodes={scene.nodes} width={1920} height={1080} time={time}
      background={surface} ink={ink} textOverrides={profile?.text_overrides}
      label="Program Canvas editorial study: not native output"/>
    <p className="program-design-disclosure">
      DESIGN_PREVIEW · EDITORIAL SOURCE CANVAS · {profile ? profile.name : "No selected output profile"}
      {" · "}Native frames, crop, fonts and final AV require separate verification.
    </p>
  </div>;
}
