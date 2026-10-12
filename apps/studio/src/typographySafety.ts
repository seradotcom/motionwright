/** Source-bound, non-mutating editorial typography geometry review.
 * This is NOT font shaping, caption acceptance or a native render verdict.
 * All coordinates are evaluated in the existing semantic camera frame.
 */
import { previewTransform } from "./canvasMotion";
import { PROJECT_CANVAS_HEIGHT, PROJECT_CANVAS_WIDTH, projectWorldPoint } from "./canvasCamera";
import { seconds, type CanvasNode, type DeliverableProfile, type Project, type Scene } from "./types";

export type TypeSafetyStatus =
  | "frame_edge" | "safe_area" | "declared_lines" | "sampled_inside" | "unresolved";
export interface TypeSafetyFinding {
  nodeId: string;
  nodeName: string;
  status: TypeSafetyStatus;
  reason: string;
  sampledTimes: number;
  firstRiskTime: number | null;
}
export interface TypeSafetyReport {
  schema: "motionwright.typography-safearea-preflight/1";
  projectId: string;
  generation: string;
  revision: number;
  sceneId: string;
  profileId: string | null;
  sceneTime: number;
  sourceFrame: { width: number; height: number };
  formatCompatible: boolean;
  findings: TypeSafetyFinding[];
  warningCount: number;
  unresolvedCount: number;
}

const W = PROJECT_CANVAS_WIDTH;
const H = PROJECT_CANVAS_HEIGHT;
const MAX_KEYS = 48;
const EPS = 0.00001;

function unresolved(node: CanvasNode, reason: string): TypeSafetyFinding {
  return {
    nodeId: node.id, nodeName: node.name, status: "unresolved",
    reason, sampledTimes: 0, firstRiskTime: null,
  };
}

/** Include boundaries, the actual editor playhead, all keyframes and temporal
 * midpoints. Finite sampling is not a continuous-motion or raster proof.
 */
function sampleTimes(node: CanvasNode, scene: Scene, editorTime: number): number[] | null {
  const duration = seconds(scene.duration);
  if (!Number.isFinite(duration) || duration <= 0 ||
      !Number.isFinite(editorTime) || node.keyframes.length > MAX_KEYS) return null;
  const points = [0, duration, Math.max(0, Math.min(editorTime, duration))];
  for (const key of node.keyframes) {
    const at = seconds(key.at);
    if (!Number.isFinite(at) || at < 0 || at > duration) return null;
    points.push(at);
  }
  const sorted = [...new Set(points)].sort((a,b)=>a-b);
  const samples = [...sorted];
  for (let i=1; i<sorted.length; i++) samples.push((sorted[i-1]+sorted[i])/2);
  return samples.sort((a,b)=>a-b);
}

function projectedBounds(node: CanvasNode, scene: Scene, at: number):
  { left: number; right: number; top: number; bottom: number } | null {
  const pose = previewTransform(node, at);
  const { x,y,width,height,rotation_deg } = pose;
  if (![x,y,width,height,rotation_deg,pose.opacity].every(Number.isFinite) ||
      width <= 0 || height <= 0 || pose.opacity < 0 || pose.opacity > 1) return null;
  const centerX=x+width/2, centerY=y+height/2;
  const radians=rotation_deg*Math.PI/180;
  const cosine=Math.cos(radians), sine=Math.sin(radians);
  const vertices: Array<[number,number]> = [
    [-width/2,-height/2], [width/2,-height/2],
    [width/2,height/2], [-width/2,height/2],
  ];
  const points=vertices.map(([dx,dy])=>
    projectWorldPoint(scene.camera,
      centerX+dx*cosine-dy*sine,
      centerY+dx*sine+dy*cosine,
    ));
  if (points.some(point=>point===null)) return null;
  const xcoords=points.map(point=>point!.x), ycoords=points.map(point=>point!.y);
  return {left:Math.min(...xcoords),right:Math.max(...xcoords),
    top:Math.min(...ycoords),bottom:Math.max(...ycoords)};
}

export function evaluateTypeSafety(
  project: Project, scene: Scene, profile: DeliverableProfile | null,
  editorTime: number,
): TypeSafetyReport {
  const positiveFormat = !!profile && Number.isInteger(profile.width) &&
    Number.isInteger(profile.height) && profile.width > 0 && profile.height > 0;
  const sameAspect=positiveFormat &&
    Math.abs(profile!.width*H-profile!.height*W) < EPS;
  const margin=scene.camera.safe_margin;
  const validMargin=Number.isFinite(margin) && margin>=0 && margin<0.5;
  const textNodes=scene.nodes.filter(n=>n.kind==="text");
  const findings=textNodes.map((node):TypeSafetyFinding=>{
    if (!profile || !positiveFormat) {
      return unresolved(node,"Select a valid delivery profile to inspect a source frame.");
    }
    if (!sameAspect) {
      return unresolved(node,"This output changes aspect ratio. Native replan/crop positions are not inferred from the 1920 × 1080 editor.");
    }
    if (profile.included_scene_ids.length > 0 &&
        !profile.included_scene_ids.includes(scene.id)) {
      return unresolved(node,"This scene is excluded from the selected deliverable profile.");
    }
    if (!validMargin) return unresolved(node,"Camera safe margin is outside the supported range.");
    if (node.coordinate_space!=="project_pixels" || node.parent_id!==null ||
        node.relations.length>0) {
      return unresolved(node,"Nested/related or non-pixel text requires a resolved native layout.");
    }
    if (!node.text?.trim()) return unresolved(node,"This object has no text to measure.");
    if (!node.style.font_family?.trim() ||
        node.style.font_size===null || node.style.line_height===null ||
        !Number.isFinite(node.style.font_size) || node.style.font_size<=0 ||
        !Number.isFinite(node.style.line_height) || node.style.line_height<=0) {
      return unresolved(node,"Font metrics are incomplete; raster text fit is not verified.");
    }
    const points=sampleTimes(node,scene,editorTime);
    if (!points) return unresolved(node,"This animation has invalid or too many keyframe times for bounded sampling.");
    let frameRisk: number|null=null,marginRisk: number|null=null;
    let minimumDeclaredBoxHeight=Number.POSITIVE_INFINITY;
    const inner={left:W*margin,right:W*(1-margin),
      top:H*margin,bottom:H*(1-margin)};
    for (const time of points) {
      minimumDeclaredBoxHeight=Math.min(minimumDeclaredBoxHeight,
        previewTransform(node,time).height);
      const box=projectedBounds(node,scene,time);
      if (!box) return unresolved(node,"Node or camera transform is not finite and cannot be reviewed.");
      if (box.left < -EPS || box.right > W+EPS ||
          box.top < -EPS || box.bottom > H+EPS) {
        if (frameRisk===null) frameRisk=time;
      } else if (box.left < inner.left-EPS || box.right>inner.right+EPS ||
                 box.top < inner.top-EPS || box.bottom>inner.bottom+EPS) {
        if (marginRisk===null) marginRisk=time;
      }
    }
    const base={nodeId:node.id,nodeName:node.name,sampledTimes:points.length};
    if (frameRisk!==null) return {...base,status:"frame_edge",
      reason:"Text box crosses the source frame boundary at a sampled time.",
      firstRiskTime:frameRisk};
    if (marginRisk!==null) return {...base,status:"safe_area",
      reason:"Text box crosses the camera safe margin at a sampled time.",
      firstRiskTime:marginRisk};
    const lines=node.text.split(/\r\n|\r|\n/).length;
    // Explicit line breaks expose a deterministic minimum line box; this
    // never estimates glyph width, kerning, fallback fonts or raster bounds.
    const lineHeight=node.style.font_size*node.style.line_height*lines;
    if (lineHeight > minimumDeclaredBoxHeight+EPS) return {...base,status:"declared_lines",
      reason:"Declared line box exceeds object height; native font shaping still needs review.",
      firstRiskTime:null};
    return {...base,status:"sampled_inside",
      reason:"Sampled geometric bounds fit. Font shaping, safe captions and native pixels remain unverified.",
      firstRiskTime:null};
  });
  return {
    schema:"motionwright.typography-safearea-preflight/1",
    projectId:project.id,generation:project.generation,revision:project.revision,
    sceneId:scene.id,profileId:profile?.id??null,sceneTime:editorTime,
    sourceFrame:{width:W,height:H},formatCompatible:!!sameAspect,
    findings,
    warningCount:findings.filter(f=>f.status!=="unresolved" && f.status!=="sampled_inside").length,
    unresolvedCount:findings.filter(f=>f.status==="unresolved").length,
  };
}
