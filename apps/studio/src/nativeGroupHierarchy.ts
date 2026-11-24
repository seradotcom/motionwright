import type { CanvasNode, Scene } from "./types";

export function isStaticNativeGroup(node: CanvasNode): boolean {
  return ["group","clip_group"].includes(node.kind) && node.parent_id === null &&
    node.coordinate_space === "project_pixels" && !node.keyframes.length && !node.relations.length &&
    node.rotation_deg === 0 && node.opacity === 1 && node.style.blend_mode === "normal";
}
export function staticNativeGroupParent(node: CanvasNode, scene: Pick<Scene,"nodes">): CanvasNode | undefined {
  if (node.coordinate_space !== "project_pixels" || node.parent_id === null) return undefined;
  return scene.nodes.find(parent => parent.id === node.parent_id && isStaticNativeGroup(parent));
}
/** Mirror the native subtree painter-order guard. This is a read-only
 * authoring hint, never renderer evidence or authority to change a layer. */
export function nativeGroupOrderIssue(scene: Pick<Scene,"nodes">): string | null {
  if (!scene.nodes.some(node=>node.parent_id!==null || ["group","clip_group"].includes(node.kind))) return null;
  for (const node of scene.nodes) {
    const group = ["group","clip_group"].includes(node.kind);
    if (node.parent_id !== null && (group || !staticNativeGroupParent(node,scene) ||
      scene.nodes.some(child => child.parent_id === node.id))) return "Only direct drawable children of a static root Group or Clip are supported.";
    if (group && !isStaticNativeGroup(node)) return "Animated, transformed or nested group containers need a dedicated native projection.";
  }
  const scopes = scene.nodes.filter(node=>node.parent_id===null).flatMap(node=>{
    const group=["group","clip_group"].includes(node.kind);
    const layers=group?scene.nodes.filter(child=>child.parent_id===node.id).map(child=>child.z_index):[node.z_index];
    return layers.length?[{layer:node.z_index,low:Math.min(...layers),high:Math.max(...layers),group}]:[];
  });
  for(let i=0;i<scopes.length;i++) for(let j=i+1;j<scopes.length;j++){
    const a=scopes[i],b=scopes[j];
    if((a.group||b.group)&&!((a.layer<b.layer&&a.high<b.low)||(a.layer>b.layer&&a.low>b.high)))
      return "Root layers and child layers interleave; assign nonoverlapping layer ranges.";
  }
  return null;
}
