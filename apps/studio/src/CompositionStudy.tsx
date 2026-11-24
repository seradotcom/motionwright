import { useId } from "react";
import type { CanvasNode } from "./types";
import { isStaticNativeGroup, nativeGroupOrderIssue, staticNativeGroupAncestors } from "./nativeGroupHierarchy";
import { intersectRectangularClips } from "./canvasClip";
import { previewTransform } from "./canvasMotion";
import { resolveCanvasRelations } from "./canvasFollow";
function safePaint(value: string | null, fallback: string): string { return value && /^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(value) ? value : fallback; }

/** A DOM/SVG editorial study. Raster/native evidence is shown by the existing native frame stage. */
export default function CompositionStudy({ nodes, width=1920, height=1080, time, background, label, ink=null, textOverrides }: {
  nodes: CanvasNode[]; width?:number; height?:number; time:number; background:string; label:string; ink?:string|null;
  textOverrides?: Readonly<Record<string,string>>;
}) {
  const prefix = "study-clip-" + useId().replace(/[^a-zA-Z0-9_-]/g, "_");
  const clipGroups = nodes.filter(node => node.kind === "clip_group" && isStaticNativeGroup(node));
  const clipIds = new Map(clipGroups.map((group, index) => [group.id, prefix + "-" + index]));
  const hierarchyIssue = nativeGroupOrderIssue({nodes});
  const alignment = resolveCanvasRelations(nodes);
  return <svg className="production-study" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
    {nodes.some(node => node.parent_id !== null) && <desc>Static Group descendants retain saved world coordinates and every ancestor's rectangular Clip plane. Native Film defines authoritative transforms and relations. {hierarchyIssue ?? ""}</desc>}
    {alignment.participants.size > 0 && <desc>{alignment.issue ? "Authored relation unresolved: " + alignment.issue : "Authored static alignment or Follow resolved for this editorial study, not native glyph or pixel evidence."}</desc>}
    <defs>{clipGroups.map(group => {
      const ancestors = staticNativeGroupAncestors(group, {nodes});
      const bounds = ancestors && intersectRectangularClips([group, ...ancestors.filter(parent => parent.kind === "clip_group")]);
      return bounds ? <clipPath key={group.id} id={clipIds.get(group.id)} clipPathUnits="userSpaceOnUse">
        <rect x={bounds.x} y={bounds.y} width={bounds.width} height={bounds.height}/>
      </clipPath> : null;
    })}</defs>
    <rect width={width} height={height} fill={safePaint(background, "#0F1216")}/>
    {nodes.filter(node => !["group","clip_group"].includes(node.kind)).sort((a,b) => a.z_index-b.z_index).map(node => {
      const followTrack = alignment.tracks.get(node.id);
      const t = {...previewTransform(followTrack ? {...node,keyframes:followTrack} : node,time),
        ...alignment.positions.get(node.id)}, size = node.style.font_size ?? 30;
      // A selected DeliverableProfile override has the same precedence as
      // native Film's text_overrides. Even an explicitly empty override must
      // never be interpreted as "no override" and reveal the source copy.
      const fromProfile = Object.prototype.hasOwnProperty.call(textOverrides ?? {}, node.id);
      const textValue = fromProfile ? textOverrides![node.id] : node.text ?? "";
      const rotation = `rotate(${t.rotation_deg} ${t.x+t.width/2} ${t.y+t.height/2})`;
      const clipParent = staticNativeGroupAncestors(node, {nodes})?.find(parent => parent.kind === "clip_group");
      const clipId = clipParent ? clipIds.get(clipParent.id) : undefined;
      // Apply the world-space rectangular clipping plane outside the local
      // rotation, so a rotated child is clipped by its actual parent plane.
      return <g key={node.id} clipPath={clipId ? "url(#" + clipId + ")" : undefined}><g data-canvas-node-id={node.id} data-parent-id={node.parent_id ?? undefined} opacity={t.opacity} transform={rotation}>
        {node.kind === "text" ? <text data-text-origin={fromProfile ? "profile" : "source"} x={t.x} y={t.y+size*.88} fill={node.style.fill == null ? safePaint(ink, "#F2F4F3") : safePaint(node.style.fill, "#F2F4F3")}
          fontFamily={node.style.font_family === "IBM Plex Mono" ? '"IBM Plex Mono", ui-monospace, monospace' : '"Instrument Sans Variable", ui-sans-serif, system-ui, sans-serif'} fontSize={size} fontWeight={node.style.font_weight ?? 400}>
          {textValue.split("\n").map((line,i) => <tspan key={i} x={t.x} dy={i===0?0:size*(node.style.line_height ?? 1.12)}>{line}</tspan>)}
        </text> : node.kind === "circle" ? <ellipse cx={t.x+t.width/2} cy={t.y+t.height/2} rx={t.width/2} ry={t.height/2} fill={safePaint(node.style.fill, "none")}/>
          : ["shape","rectangle"].includes(node.kind) ? <rect x={t.x} y={t.y} width={t.width} height={t.height} fill={safePaint(node.style.fill, "none")} stroke={safePaint(node.style.stroke, "none")} strokeWidth={node.style.stroke_width}/>
          : null}
      </g></g>;
    })}
  </svg>;
}
