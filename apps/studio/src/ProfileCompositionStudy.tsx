import { useEffect, useState } from "react";
import type { DeliverableProfile, Project, Scene } from "./types";
import CompositionStudy from "./CompositionStudy";
import { canvasBackground, inheritedTextInk } from "./canvasInheritedInk";
import { reflowSceneForProfile, resolvedProfileTextOverrides, sampleProfileLayout } from "./profileComposition";
import { proceduralGridDimensions, proceduralGridDimensionsForOutput } from "./proceduralField";

type Props = { project: Project; scene: Scene; profile: DeliverableProfile; time: number; label: string };
type Result = { project: Project; scene: Scene; profile: DeliverableProfile; output: Scene | null; issue: string | null };
export default function ProfileCompositionStudy({ project, scene, profile, time, label }: Props) {
  const [result, setResult] = useState<Result | null>(null);
  useEffect(() => {
    let active = true;
    reflowSceneForProfile(project, scene, profile).then(output => {
      if (active) setResult({ project, scene, profile, output, issue: null });
    }).catch(reason => {
      if (active) setResult({ project, scene, profile, output: null,
        issue: reason instanceof Error ? reason.message : String(reason) });
    });
    return () => { active = false; };
  }, [project, scene, profile]);
  if (!result || result.project !== project || result.scene !== scene || result.profile !== profile)
    return <p role="status">Evaluating output layout…</p>;
  if (!result.output) return <p className="production-error" role="alert" aria-label={label + " unavailable"}>{result.issue}</p>;
  try {
    const sampled = sampleProfileLayout(project, result.output, profile, time);
    const field = project.production_design?.procedural_fields?.find(value => value.scene_id === scene.id);
    const grid = field?.config.distribution === "adaptive_grid"
      ? profile.framing_strategy === "replan"
        ? proceduralGridDimensionsForOutput(field.config, profile.width, profile.height) : proceduralGridDimensions(field.config)
      : null;
    return <div aria-label={label + " source layout"} data-source-revision={project.revision} data-profile-id={profile.id}>
      <p className="production-help">{profile.name} · {profile.width} × {profile.height} · {profile.framing_strategy === "replan" ? "Replan" : "Crop"}{grid ? " · " + grid.columns + " columns × " + grid.rows + " rows" : ""}</p>
      <div className="production-preview-mat" style={{ height: 320, flex: "none", padding: 8 }}>
        <CompositionStudy nodes={sampled.nodes} width={profile.width} height={profile.height} time={0}
          background={canvasBackground(project.visual_language.palette)} ink={inheritedTextInk(project.visual_language.palette)} textOverrides={resolvedProfileTextOverrides(sampled.nodes,profile)} label={label}/>
      </div>
      {sampled.boundaryIssues.length > 0 && <div className="production-error" role="status" aria-label={label + " boundary warnings"}>
        <p>{sampled.boundaryIssues.length} sampled object bounds need review. Geometry has not been shrunk or clamped.</p>
        <ul>{sampled.boundaryIssues.map((issue, index) => <li key={index}>{issue}</li>)}</ul>
      </div>}
      <p className="production-help">Output layout at {time.toFixed(3)} s from the current source revision. Compatible object edits remain; conflicting edits stop this variant.
        Delivery-local Hero headlines and supporting copy use the selected aspect's line budget. Layout-only line breaks do not modify saved copy.
        Font shaping, media, full motion admission and rendered pixels require Native preview.</p>
    </div>;
  } catch (reason) {
    return <p className="production-error" role="alert" aria-label={label + " unavailable"}>{reason instanceof Error ? reason.message : String(reason)}</p>;
  }
}
