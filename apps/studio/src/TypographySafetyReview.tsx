import { AlertTriangle, Check, CircleHelp, Type } from "lucide-react";
import { useMemo } from "react";
import { evaluateTypeSafety, type TypeSafetyStatus } from "./typographySafety";
import type { CanvasNode, DeliverableProfile, Project, Scene } from "./types";
import "./typographySafety.css";

const STATUS:Record<TypeSafetyStatus,string>={
  frame_edge:"Frame edge",
  safe_area:"Safe margin",
  declared_lines:"Text line height",
  sampled_inside:"Geometry sampled",
  unresolved:"Needs native review",
};

export default function TypographySafetyReview({
  project,scene,profile,sceneTime,onSelectNode,
}: {
  project:Project; scene:Scene; profile:DeliverableProfile|null;
  sceneTime:number; onSelectNode:(node:CanvasNode)=>void;
}) {
  const result=useMemo(
    ()=>evaluateTypeSafety(project,scene,profile,sceneTime),
    [project,scene,profile,sceneTime],
  );
  const affected=result.warningCount;
  const unresolved=result.unresolvedCount;
  const summary=affected>0 ? String(affected)+" flagged" :
    unresolved>0 ? String(unresolved)+" unresolved" :
    result.findings.length===0 ? "No text" : "Geometry sampled";
  return (
    <details className="canvas-type-preflight" aria-label="Typography safety review">
      <summary>
        <Type size={14} aria-hidden="true"/>
        <span>Typography safety</span>
        <span className="canvas-type-summary" aria-live="polite">{summary}</span>
      </summary>
      <div className="canvas-type-body">
        <p className="canvas-type-explainer">
          Source-frame bounds and declared text line height only. This is an
          editorial warning tool, <strong>not</strong> native text shaping,
          legibility approval, rights clearance or a delivery PASS.
        </p>
        <div className="canvas-type-source">
          <span>{profile?.name??"No delivery profile"}</span>
          <span className="mono">{"r"+result.revision+" · t="+sceneTime.toFixed(2)+"s"}</span>
        </div>
        {!result.formatCompatible && <p className="canvas-type-caution">
          Format-specific reframing cannot be inferred from the 1920 × 1080 semantic canvas.
          Review this target in its native preview.
        </p>}
        {result.findings.length===0 ? (
          <p className="canvas-type-none">No text objects in this scene.</p>
        ) : (
          <ul className="canvas-type-list" aria-label="Text geometry findings">
            {result.findings.map((finding)=>{
              const node=scene.nodes.find(n=>n.id===finding.nodeId);
              const warning=finding.status==="frame_edge" ||
                finding.status==="safe_area" || finding.status==="declared_lines";
              const Icon=warning?AlertTriangle:
                finding.status==="unresolved"?CircleHelp:Check;
              return (
                <li className={"canvas-type-result canvas-type-"+finding.status} key={finding.nodeId}>
                  <div className="canvas-type-result-head">
                    <Icon size={14} aria-hidden="true"/>
                    <strong>{finding.nodeName}</strong>
                    <span>{STATUS[finding.status]}</span>
                  </div>
                  <p>{finding.reason}</p>
                  <div className="canvas-type-result-footer">
                    <span className="mono">
                      {finding.sampledTimes===0 ? "Not sampled" : String(finding.sampledTimes)+" time samples"}
                      {finding.firstRiskTime!==null ? " · first "+finding.firstRiskTime.toFixed(2)+"s" : ""}
                    </span>
                    <button type="button" disabled={!node}
                      onClick={()=>{if(node) onSelectNode(node);}}
                      aria-label={"Inspect text "+finding.nodeName}>
                      Inspect object
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        <p className="canvas-type-disclaimer">
          Read-only and project-revision bound. Keyframes are sampled at endpoints
          and midpoints; glyph widths, font fallback, masks and unsampled motion
          require Semwright native frame evidence and human review.
        </p>
      </div>
    </details>
  );
}
