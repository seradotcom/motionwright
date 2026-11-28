import SceneCompositionStudy from "./SceneCompositionStudy";
import { sampleProgramCaption } from "./programCaption";
import "./program-caption.css";
import { canvasBackground, inheritedTextInk } from "./canvasInheritedInk";
import { deliveryTextSourceConflict } from "./deliveryTextSources";
import { useSceneCaptureSourceImages } from "./useCaptureSourceImages";
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
  const surface = canvasBackground(project.visual_language.palette);
  const ink = inheritedTextInk(project.visual_language.palette);
  const cutScope = programSceneCutScope(scene, profile);
  const caption = sampleProgramCaption(project, scene, time, profile);
  const sourceConflict = profile && cutScope!=="excluded" ? deliveryTextSourceConflict(project,profile,scene) : null;
  const captureSources = useSceneCaptureSourceImages(project, sourceConflict ? null : scene.id);
  return <div className="program-design-study" aria-label="Saved Canvas editorial design monitor"
    data-source-revision={project.revision} data-selected-profile-id={profile?.id ?? ""}
    data-output-cut-scope={cutScope}>
    {sourceConflict ? <p className="production-error" role="alert">Delivery source layout unavailable: {sourceConflict}</p>
      : <SceneCompositionStudy scene={scene} frameRate={profile?.frame_rate} cameraReframes={project.production_design?.camera_reframes} time={time}
        background={surface} ink={ink} imageSources={captureSources.images}
        textOverrides={cutScope === "excluded" ? undefined : profile?.text_overrides}
        label="Program Canvas editorial study: not native output"/>}
    {caption && <div className="program-caption-study" aria-label="Program caption source study" data-source-revision={project.revision}
      data-caption-id={caption.cue?.id ?? ""} data-caption-voice-id={caption.cue?.voice_track_id ?? ""}>
      <small>Caption source · {profile?.burn_in_captions ? "burn-in" : "sidecar"} · shared playhead</small>
      {caption.issue ? <p role="status">{caption.issue}</p> : caption.cue ? <p className="program-caption-text">
        {caption.runs.map((run, index) => run.emphasis ? <strong key={index}>{run.text}</strong> : <span key={index}>{run.text}</span>)}
      </p> : <p className="program-caption-empty">No cue at this source time.</p>}
      {caption.cue && <small>Source style · final font layout and placement follow the native preview</small>}
    </div>}
    <p className="program-design-disclosure">
      DESIGN_PREVIEW · EDITORIAL SOURCE CANVAS · {profile ? profile.name : "No selected output profile"}
      {captureSources.status === "loading" && <strong role="status">{" "}Loading original capture images.</strong>}
      {captureSources.error && <>
        <strong role="status">{" "}Original capture preview unavailable. {captureSources.error}</strong>{" "}
        <button type="button" className="button compact" onClick={captureSources.retry}>Retry original images</button>
      </>}
      {cutScope === "excluded" &&
        <strong role="status"> · SCENE EXCLUDED FROM SELECTED OUTPUT CUT ·
          This editable source scene will not be exported in this profile.</strong>}
      {cutScope === "included" && " · Scene included in selected cut"}
      {cutScope === "full" && " · Complete saved sequence"}
      {" · "}Saved scene camera sampled at {time.toFixed(3)} s. Native frames, crop, fonts and final AV require separate verification.
    </p>
  </div>;
}
