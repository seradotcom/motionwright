import type { CanvasNode, RelationKind } from "./types";

export interface AlignmentScale { x: number; y: number; object: number; }
export interface AlignedPosition { x: number; y: number; }
export interface AlignmentResult {
  positions: Map<string, AlignedPosition>;
  participants: Set<string>;
  issue: string | null;
}

type Equation = { target: string; edge: number };
const EPSILON = 1e-6;
const MAX_NODES = 512;
const SOURCE_SCALE: AlignmentScale = { x: 1, y: 1, object: 1 };

export function alignmentScale(width: number, height: number, framing: "replan" | "crop"): AlignmentScale {
  let x = width / 1920, y = height / 1080;
  const object = framing === "crop" ? Math.max(x,y) : Math.min(x,y);
  if (framing === "crop") x = y = object;
  return {x,y,object};
}

function admitted(node: CanvasNode, children: Set<string>): boolean {
  return ["shape","rectangle","circle","text"].includes(node.kind) && node.parent_id === null &&
    !children.has(node.id) && node.coordinate_space === "project_pixels" &&
    node.keyframes.length === 0 && node.rotation_deg === 0 && node.opacity === 1 &&
    node.style.blend_mode === "normal" && node.width > 0 && node.height > 0 &&
    [node.x,node.y,node.width,node.height].every(Number.isFinite);
}

function resolveAxis(nodes: Map<string,CanvasNode>, equations: Map<string,Equation>,
  positionScale: number, objectScale: number, horizontal: boolean, positionLocked: boolean): Map<string,number> {
  const start = (node: CanvasNode) => horizontal ? node.x : node.y;
  const size = (node: CanvasNode) => horizontal ? node.width : node.height;
  const values = new Map([...nodes].filter(([id])=>!equations.has(id)).map(([id,node])=>[id,start(node)]));
  const remaining = new Map(equations);
  while (remaining.size) {
    const ready = [...remaining].filter(([,equation])=>values.has(equation.target));
    if (!ready.length) throw new Error("Cyclic dependencies on the same axis.");
    for (const [id,equation] of ready) {
      const node=nodes.get(id)!, target=nodes.get(equation.target)!;
      const ownSize=size(node), targetSize=size(target);
      const desired=values.get(target.id)!+targetSize/2-ownSize/2+
        equation.edge*(targetSize-ownSize)*objectScale/(2*positionScale);
      if (!Number.isFinite(desired)) throw new Error("Resolved coordinate is not finite.");
      const locked=positionLocked||node.property_locks.includes("position");
      if (locked && Math.abs((desired-start(node))*positionScale)>EPSILON)
        throw new Error("An alignment would move a locked object.");
      values.set(id,locked?start(node):desired);
      remaining.delete(id);
    }
  }
  return values;
}

const axes: Partial<Record<RelationKind,{axis:"x"|"y";edge:number}>>={
  align_left:{axis:"x",edge:-1},align_center_x:{axis:"x",edge:0},align_right:{axis:"x",edge:1},
  align_top:{axis:"y",edge:-1},align_center_y:{axis:"y",edge:0},align_bottom:{axis:"y",edge:1},
};

/** Source-space editorial preview or equivalent coordinates for the selected
 * output projection. The saved object pose and relation IDs are unchanged. */
export function resolveCanvasAlignments(source: readonly CanvasNode[], scale=SOURCE_SCALE,
  positionLocked=false): AlignmentResult {
  const participants=new Set<string>(), positions=new Map<string,AlignedPosition>();
  if (!source.some(node=>node.relations.length)) return {positions,participants,issue:null};
  for (const node of source) for (const relation of node.relations) {
    participants.add(node.id);participants.add(relation.target_id);
  }
  try {
    if (source.length>MAX_NODES || ![scale.x,scale.y,scale.object].every(v=>Number.isFinite(v)&&v>0) ||
        scale.object>Math.max(scale.x,scale.y)) throw new Error("Projection exceeds the bounded static layout contract.");
    const all=new Map(source.map(node=>[node.id,node]));
    if (all.size!==source.length) throw new Error("Duplicate object identity.");
    const children=new Set(source.flatMap(node=>node.parent_id?[node.parent_id]:[]));
    const nodes=new Map<string,CanvasNode>(), x=new Map<string,Equation>(), y=new Map<string,Equation>();
    for (const node of source) {
      if (!node.relations.length) continue;
      if (!admitted(node,children)) throw new Error("Only independent static drawable boxes may be aligned.");
      nodes.set(node.id,node);
      for (const relation of node.relations) {
        const target=all.get(relation.target_id);
        if (!target || target.id===node.id || !admitted(target,children))
          throw new Error("Alignment target is not an independent static drawable box.");
        nodes.set(target.id,target);
        const rule=axes[relation.kind];
        if (!rule) throw new Error("Follow and Attach require a dedicated dynamic relation projection.");
        const equations=rule.axis==="x"?x:y;
        if (equations.has(node.id)) throw new Error("More than one alignment is authored on the same axis.");
        equations.set(node.id,{target:target.id,edge:rule.edge});
      }
    }
    const xs=resolveAxis(nodes,x,scale.x,scale.object,true,positionLocked);
    const ys=resolveAxis(nodes,y,scale.y,scale.object,false,positionLocked);
    for (const node of source) if (node.relations.length) positions.set(node.id,{x:xs.get(node.id)!,y:ys.get(node.id)!});
    return {positions,participants,issue:null};
  } catch(reason) {
    return {positions:new Map(),participants,issue:reason instanceof Error?reason.message:String(reason)};
  }
}
