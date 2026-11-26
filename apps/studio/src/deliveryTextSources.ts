import type { DeliverableProfile, Project, Scene } from "./types";
import { sameValue } from "./creativeProduction";

export interface ProtectedDeliveryText { value: string; reason: string }

/** Read-only exact source rules. Mirror Rust, without certifying the content. */
export function protectedDeliveryText(project: Project, scene: Scene, nodeId: string): ProtectedDeliveryText | null {
  if(project.production_design?.capture_boards?.some(board=>board.scene_id===scene.id)){
    const node=scene.nodes.find(node=>node.id===nodeId&&node.kind==="text");
    if(typeof node?.text==="string")return {value:node.text,reason:"Capture board source labels and synthetic disclosure must retain exact wording"};
  }
  const hero=project.production_design?.heroes.find(item=>item.scene_id===scene.id);
  const family = ({metric_evidence:"MetricEvidence",step_sequence:"StepSequence",contrast_comparison:"ContrastComparison",
    editorial_quote:"EditorialQuote"} as Record<string,string>)[hero?.config.layout ?? ""];
  const disclosure = family && hero?.baseline.find(node=>node.name===family+" / disclosure");
  if (disclosure && disclosure.id===nodeId && disclosure.text!==null)
    return {value:disclosure.text,reason:"Attached component evidence disclosure must retain its original wording"};
  const binding = project.production_design?.measured_bindings?.find(item=>item.scene_id===scene.id && item.node_id===nodeId);
  if (binding?.baseline.kind === "text")
    return {value:binding.baseline.value,reason:"Measured text must retain its bound value and units"};
  if (hero?.config.layout==="step_sequence" && hero.baseline.some(node=>node.id===nodeId && node.name==="StepSequence / wordmark")) {
    const value=scene.nodes.find(node=>node.id===nodeId)?.text;
    if (typeof value==="string") return {value,reason:"Step index must retain the current authored sequence label"};
  }
  return null;
}

export function deliveryTextSourceConflict(project: Project, profile: DeliverableProfile, scene?: Scene): string | null {
  const scenes = scene ? [scene] : project.scenes.filter(item=>!profile.included_scene_ids.length || profile.included_scene_ids.includes(item.id));
  for (const source of scenes) {
    const hero=project.production_design?.heroes.find(item=>item.scene_id===source.id);
    const family=({metric_evidence:"MetricEvidence",step_sequence:"StepSequence",contrast_comparison:"ContrastComparison",
      editorial_quote:"EditorialQuote"} as Record<string,string>)[hero?.config.layout??""];
    if (family && hero) {
      const base=hero.baseline.find(node=>node.name===family+" / disclosure");
      const actual=source.nodes.find(node=>node.id===base?.id);
      if(!base || !actual || base.kind!=="text" || !sameValue({...actual,property_locks:base.property_locks},base))
        return "Attached component evidence disclosure differs from its original source; restore it or explicitly detach before output";
    }
    for (const node of source.nodes) {
      const retained=protectedDeliveryText(project,source,node.id);
      if (retained && Object.hasOwn(profile.text_overrides,node.id) && profile.text_overrides[node.id]!==retained.value)
        return retained.reason+": "+node.name+". Restore the exact source wording or detach the binding before authoring a replacement.";
    }
  }
  return null;
}
