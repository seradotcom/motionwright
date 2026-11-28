import { useEffect, useRef, useState, type ComponentProps } from "react";
import SceneCompositionStudy from "./SceneCompositionStudy";
import type { Scene } from "./types";

type StudyOptions = Pick<ComponentProps<typeof SceneCompositionStudy>, "time" | "ink" | "frameRate" | "cameraReframes">;
type Props = StudyOptions & {
  before: Scene;
  after: Scene;
  boundTextNodes: ReadonlySet<string>;
  openCanvas: () => void;
};
type WidthFinding = {id: string; glyphWidth: number; boxWidth: number; overflows: boolean};

export default function MeasuredSourcePreview({before, after, boundTextNodes, openCanvas, ...options}: Props) {
  const proposed = useRef<HTMLDivElement>(null);
  const [findings, setFindings] = useState<WidthFinding[] | null>(null);
  const {time, frameRate, cameraReframes} = options;
  useEffect(() => {
    let cancelled = false;
    const frame = requestAnimationFrame(() => {
      void document.fonts.ready.then(() => {
        if (cancelled) return;
        const measured: WidthFinding[] = [];
        for (const element of proposed.current?.querySelectorAll<SVGTextElement>("svg text[data-authored-width]") ?? []) {
          const id = element.closest("[data-canvas-node-id]")?.getAttribute("data-canvas-node-id");
          if (!id || !boundTextNodes.has(id)) continue;
          const boxWidth = Number(element.dataset.authoredWidth);
          const left = Number(element.getAttribute("x"));
          const glyphs = element.getBBox();
          if (!Number.isFinite(boxWidth) || boxWidth <= 0 ||
              ![left, glyphs.x, glyphs.width].every(Number.isFinite) || glyphs.width <= 0) continue;
          measured.push({id, glyphWidth: glyphs.width, boxWidth,
            overflows: glyphs.x < left - 0.5 || glyphs.x + glyphs.width > left + boxWidth + 0.5});
        }
        setFindings(measured);
      }).catch(() => {
        if (!cancelled) setFindings([]);
      });
    });
    return () => { cancelled = true; cancelAnimationFrame(frame); };
  }, [after, boundTextNodes, time, frameRate, cameraReframes]);

  const overflows = findings?.filter(finding => finding.overflows) ?? [];
  return <>
    <div className="production-plan-grid">
      <div><SceneCompositionStudy {...options} scene={before} background="#0C1723" label="Saved measured source Canvas"/></div>
      <div ref={proposed}><SceneCompositionStudy {...options} scene={after} background="#0C1723" label="Proposed measured source Canvas"/></div>
    </div>
    {overflows.length > 0 && <div role="status" className="production-help">
      <ul>{overflows.map(({id, boxWidth, glyphWidth}) =>
        <li key={id}>{after.nodes.find(node => node.id === id)?.name ?? "Measured text"} exceeds its Canvas text box
          in the proposed value. Glyphs occupy {Math.round(glyphWidth)} px in a {Math.round(boxWidth)} px box.</li>)}</ul>
      <p>Resize the affected text boxes in Canvas or adjust the output precision before reviewing the new layout.</p>
    </div>}
    {findings && findings.length > 0 && findings.length === boundTextNodes.size && overflows.length === 0 &&
      <p className="production-help" role="status">Measured text fits its Canvas boxes at this preview position.</p>}
    {findings && findings.length !== boundTextNodes.size && <p className="production-help" role="status">
      Some bound text could not be measured in this editorial preview.
    </p>}
    {boundTextNodes.size > 0 && <p className="production-help">Text widths are measured in the browser's editorial preview.</p>}
    <button type="button" className="secondary-button" onClick={openCanvas}>Edit affected Canvas objects</button>
  </>;
}
