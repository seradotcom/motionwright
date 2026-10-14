import { useEffect, useMemo, useRef, useState } from "react";
import { analyzeNativeInspection, nativeAnalysisReviewChange, nativeInspectionRequestKey } from "./nativeInspectionAnalysis";
import type { InspectionRaster } from "./nativeInspectionAnalysis";
import type { NativeInspectionPlan } from "./nativeInspection";
import type { Change, Project } from "./types";
import "./native-analysis.css";

export default function NativeInspectionAnalysisPanel({ project, plan, samples, busy, commit, onInspect }: {
  project: Project; plan: NativeInspectionPlan; samples: InspectionRaster[]; busy: boolean;
  commit: (change: Change) => Promise<void>;
  onInspect: (frame: number, reference: number) => void;
}) {
  const key = nativeInspectionRequestKey(plan);
  const currentKey = useRef(key); currentKey.current = key;
  const alive = useRef(true), committing = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const [expectation, setExpectation] = useState({ key: "", motion: false });
  const expectedMotion = expectation.key === key && expectation.motion;
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const result = useMemo(() => {
    try { return { analysis: analyzeNativeInspection(plan, samples, expectedMotion), error: null }; }
    catch (reason) { return { analysis: null, error: reason instanceof Error ? reason.message : "Native thumbnail analysis unavailable." }; }
  }, [plan, samples, expectedMotion]);
  const record = async (candidate: string) => {
    if (busy || committing.current || !result.analysis) return;
    const requestedKey = key;
    committing.current = true; setRecording(true); setError(null);
    try {
      const change = nativeAnalysisReviewChange(project, plan, samples, candidate, expectedMotion);
      await commit(change);
    } catch (reason) {
      if (alive.current && currentKey.current === requestedKey) {
        setError(reason instanceof Error ? reason.message : "Could not record this review candidate.");
      }
    } finally {
      committing.current = false;
      if (alive.current) setRecording(false);
    }
  };
  const analysis = result.analysis;
  return <section className="review-analysis" aria-label="Native thumbnail analysis">
    <h3>Source-bound thumbnail analysis</h3>
    <p className="production-help">Read-only measurements of this native read batch. No aesthetic score, full-resolution equivalence, independent verification or automatic repair.</p>
    <label className="review-analysis__expectation"><input type="checkbox" checked={expectedMotion}
      disabled={busy || recording} onChange={event => setExpectation({ key, motion: event.target.checked })}/>
      I expect continuous motion in the inspected interval
    </label>
    {(error || result.error) && <p role="alert" className="production-error">{error || result.error}</p>}
    {analysis && <>
      <p className="production-help" aria-label="Native analysis coverage">
        {analysis.coverage.selectedFrames} source frames · {analysis.coverage.adjacentPairs} adjacent pairs · {analysis.coverage.adjacentTriples} adjacent triples · {analysis.coverage.temporalGaps} temporal gaps excluded · {analysis.coverage.width} × {analysis.coverage.height} thumbnails
      </p>
      {analysis.coverage.adjacentTriples === 0 && <p className="review-analysis__scope">No three consecutive frames are available. Use an in-scene consecutive window to inspect isolated frame changes.</p>}
      <div className="review-analysis__comparisons" aria-label="Measured thumbnail differences">
        {analysis.differences.map(item => <div key={`${item.fromFrame}:${item.toFrame}`}>
          <strong>f{item.fromFrame} → f{item.toFrame}</strong>
          <span>{item.changedPixels} / {item.comparedPixels} thumbnail pixels above delta 16</span>
          <span>{item.adjacent ? "Adjacent samples" : "Spatial comparison only; temporal gap"}</span>
          {item.changedBox && <small>Thumbnail box: {item.changedBox.x}, {item.changedBox.y}, {item.changedBox.width} × {item.changedBox.height}</small>}
        </div>)}
      </div>
      <div className="review-analysis__candidates" aria-label="Native analysis candidates">
        {!analysis.candidates.length && <p>No candidate from this bounded batch. This is not a creative or technical PASS.</p>}
        {analysis.candidates.map(candidate => <article key={candidate.key}>
          <h4>{candidate.code === "expected_motion_unchanged" ? "Expected motion: unchanged thumbnails" : "Isolated thumbnail change"}</h4>
          <p>{candidate.observation}</p><p className="production-help">{candidate.consequence}</p>
          <p className="production-help">Exact interval: [{candidate.start.num}/{candidate.start.den}, {candidate.end.num}/{candidate.end.den}) s · uncertain interpretation</p>
          <p>{candidate.proposedRepair}</p>
          <div className="production-button-row">
            <button type="button" className="secondary-button" onClick={() => onInspect(candidate.anchorFrame, candidate.referenceFrame)}>Inspect candidate frames</button>
            <button type="button" className="secondary-button" disabled={busy || recording} onClick={() => record(candidate.key)}>Record analytical review candidate</button>
          </div>
        </article>)}
      </div>
      <p className="production-help">Recording uses the existing revisioned review service and makes the current native read grant stale. It does not apply the proposed repair, resolve a review, or assert that an automated observation was a manual finding.</p>
    </>}
  </section>;
}
