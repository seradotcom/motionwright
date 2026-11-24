import type { CanvasNode, Scene } from "./types";

const isGroup = (node: CanvasNode) => ["group", "clip_group"].includes(node.kind);

/** A local eligibility check. The complete chain is validated separately. */
export function isStaticNativeGroup(node: CanvasNode): boolean {
  return isGroup(node) && node.coordinate_space === "project_pixels" &&
    !node.keyframes.length && !node.relations.length && node.rotation_deg === 0 &&
    node.opacity === 1 && node.style.blend_mode === "normal";
}

/** Immediate parent first. Undefined means a broken, cyclic or transformed
 * chain; an empty array is a valid scene root. Never accept only a prefix. */
export function staticNativeGroupAncestors(node: CanvasNode, scene: Pick<Scene, "nodes">): CanvasNode[] | undefined {
  if (node.coordinate_space !== "project_pixels") return undefined;
  const byId = new Map(scene.nodes.map(parent => [parent.id, parent]));
  const ancestors: CanvasNode[] = [], seen = new Set([node.id]);
  let next = node.parent_id;
  while (next !== null) {
    if (seen.has(next)) return undefined;
    seen.add(next);
    const parent = byId.get(next);
    if (!parent || !isStaticNativeGroup(parent)) return undefined;
    ancestors.push(parent);
    next = parent.parent_id;
  }
  return ancestors;
}

export function staticNativeGroupParent(node: CanvasNode, scene: Pick<Scene, "nodes">): CanvasNode | undefined {
  return staticNativeGroupAncestors(node, scene)?.[0];
}

/** Mirror Native's subtree painter-order guard at every parent scope. This
 * is an authoring hint, never renderer evidence or authority to rewrite z. */
export function nativeGroupOrderIssue(scene: Pick<Scene, "nodes">): string | null {
  if (!scene.nodes.some(node => node.parent_id !== null || isGroup(node))) return null;
  if (new Set(scene.nodes.map(node => node.id)).size !== scene.nodes.length) return "Duplicate object identity in the group hierarchy.";
  const chains = new Map<string, CanvasNode[]>();
  for (const node of scene.nodes) {
    if (isGroup(node) && !isStaticNativeGroup(node))
      return "Animated or transformed group containers need a dedicated native projection.";
    const ancestors = staticNativeGroupAncestors(node, scene);
    if (node.parent_id !== null && !ancestors)
      return "Every ancestor must be an unchanged static Group or Clip; missing parents and cycles are invalid.";
    chains.set(node.id, ancestors ?? []);
  }
  const ranges = new Map<string, { low: number; high: number }>();
  for (const node of scene.nodes.filter(node => !isGroup(node))) {
    ranges.set(node.id, { low: node.z_index, high: node.z_index });
    for (const parent of chains.get(node.id)!) {
      const range = ranges.get(parent.id);
      ranges.set(parent.id, { low: Math.min(range?.low ?? node.z_index, node.z_index),
        high: Math.max(range?.high ?? node.z_index, node.z_index) });
    }
  }
  for (const parentId of new Set(scene.nodes.map(node => node.parent_id))) {
    const siblings = scene.nodes.filter(node => node.parent_id === parentId && ranges.has(node.id));
    for (let i = 0; i < siblings.length; i++) for (let j = i + 1; j < siblings.length; j++) {
      const a = siblings[i], b = siblings[j];
      if (!isGroup(a) && !isGroup(b)) continue;
      const ar = ranges.get(a.id)!, br = ranges.get(b.id)!;
      if (!((a.z_index < b.z_index && ar.high < br.low) || (a.z_index > b.z_index && ar.low > br.high)))
        return "Sibling subtree layers interleave; assign nonoverlapping layer ranges.";
    }
  }
  return null;
}

/** Preserve the source array as well as stable sibling painter order. */
export function nativeGroupTreeRows(scene: Pick<Scene, "nodes">): Array<{ node: CanvasNode; depth: number }> {
  const rows: Array<{ node: CanvasNode; depth: number }> = [], visited = new Set<string>();
  const sorted = [...scene.nodes].sort((a, b) => b.z_index - a.z_index);
  const stack = sorted.filter(node => node.parent_id === null).reverse().map(node => ({ node, depth: 0 }));
  while (stack.length) {
    const row = stack.pop()!;
    if (visited.has(row.node.id)) continue;
    visited.add(row.node.id); rows.push(row);
    stack.push(...sorted.filter(node => node.parent_id === row.node.id).reverse()
      .map(node => ({ node, depth: row.depth + 1 })));
  }
  // Invalid historical trees remain selectable for repair.
  rows.push(...sorted.filter(node => !visited.has(node.id)).map(node => ({ node, depth: 0 })));
  return rows;
}
