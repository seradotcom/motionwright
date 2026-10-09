import type { CanvasNode } from "./types";
import { previewTransform } from "./canvasMotion";
function safePaint(value: string | null, fallback: string): string { return value && /^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(value) ? value : fallback; }

/** A DOM/SVG editorial study. Raster/native evidence is shown by the existing native frame stage. */
export default function CompositionStudy({ nodes, width=1920, height=1080, time, background, label }: {
  nodes: CanvasNode[]; width?:number; height?:number; time:number; background:string; label:string;
}) {
  return <svg className="production-study" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
    <rect width={width} height={height} fill={safePaint(background, "#0F1216")}/>
    {nodes.filter(n => !n.parent_id).sort((a,b) => a.z_index-b.z_index).map(node => {
      const t = previewTransform(node,time), size = node.style.font_size ?? 30;
      const rotation = `rotate(${t.rotation_deg} ${t.x+t.width/2} ${t.y+t.height/2})`;
      return <g key={node.id} opacity={t.opacity} transform={rotation}>
        {node.kind === "text" ? <text x={t.x} y={t.y+size*.88} fill={safePaint(node.style.fill, "#F2F4F3")}
          fontFamily={node.style.font_family ?? "Instrument Sans Variable"} fontSize={size} fontWeight={node.style.font_weight ?? 400}>
          {(node.text ?? "").split("\n").map((line,i) => <tspan key={i} x={t.x} dy={i===0?0:size*(node.style.line_height ?? 1.12)}>{line}</tspan>)}
        </text> : node.kind === "circle" ? <ellipse cx={t.x+t.width/2} cy={t.y+t.height/2} rx={t.width/2} ry={t.height/2} fill={safePaint(node.style.fill, "none")}/>
          : ["shape","rectangle"].includes(node.kind) ? <rect x={t.x} y={t.y} width={t.width} height={t.height} fill={safePaint(node.style.fill, "none")} stroke={safePaint(node.style.stroke, "none")} strokeWidth={node.style.stroke_width}/>
          : null}
      </g>;
    })}
  </svg>;
}
