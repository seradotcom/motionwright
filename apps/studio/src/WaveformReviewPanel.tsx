import { useEffect, useMemo, useRef, useState } from "react";
import { analyzeWaveformPage, waveformReviewChange } from "./waveformReview";
import type { Change, Project, VoiceTrack, WaveformPage } from "./types";
import "./native-analysis.css";

export default function WaveformReviewPanel({ project, track, page, pageIndex, pageSize, requestKey, commit, busy }: {
  project: Project; track: VoiceTrack; page: WaveformPage; pageIndex: number; pageSize: number;
  requestKey: string; commit: (change: Change) => Promise<void>; busy: boolean;
}) {
  const [expectation, setExpectation] = useState({ key: "", sound: false });
  const expectedSound = expectation.key === requestKey && expectation.sound;
  const [recording, setRecording] = useState(false), [error, setError] = useState<string | null>(null);
  const alive = useRef(true), committing = useRef(false), current = useRef(requestKey);
  current.current = requestKey;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const result = useMemo(() => {
    try { return { analysis: analyzeWaveformPage(project, track, page, pageIndex, pageSize, expectedSound), error: null }; }
    catch (reason) { return { analysis: null, error: reason instanceof Error ? reason.message : "Waveform review unavailable." }; }
  }, [project, track, page, pageIndex, pageSize, expectedSound]);
  const record = async (candidateKey: string) => {
    if (busy || committing.current || !result.analysis) return;
    const key = requestKey;
    committing.current = true; setRecording(true); setError(null);
    try { await commit(waveformReviewChange(project, track, page, pageIndex, pageSize, expectedSound, candidateKey, key)); }
    catch (reason) {
      if (alive.current && current.current === key) setError(reason instanceof Error ? reason.message : "Could not record waveform candidate.");
    } finally { committing.current = false; if (alive.current) setRecording(false); }
  };
  return <section className="review-analysis" aria-label="Measured waveform review candidates">
    <h3>Review this measured waveform page</h3>
    <p className="production-help">The existing source-bound, quantized sample-peak proxy is reused. No decoder, provider, mix or guessed waveform is started here.</p>
    <label className="review-analysis__expectation"><input type="checkbox" checked={expectedSound} disabled={busy || recording}
      onChange={event => setExpectation({ key: requestKey, sound: event.target.checked })}/>
      I expect audible content throughout this waveform page
    </label>
    {(error || result.error) && <p className="production-error" role="alert">{error || result.error}</p>}
    {result.analysis && <>
      <p className="production-help">{result.analysis.inspectedPeaks} / {result.analysis.totalPeaks} source peak bins inspected · one page only · no LUFS or true-peak measurement.</p>
      {!result.analysis.candidates.length && <p>No candidate from this page under the selected expectation. This is not a mix-quality or release PASS.</p>}
      <div className="review-analysis__candidates">
        {result.analysis.candidates.map(candidate => <article key={candidate.key}>
          <h4>{candidate.code === "near_full_scale_proxy" ? "Near-full-scale proxy bins" : "Expected sound: quiet proxy span"}</h4>
          <p>{candidate.observation}</p>
          <p className="production-help">Voice-take-relative interval: [{candidate.start.num}/{candidate.start.den}, {candidate.end.num}/{candidate.end.den}) s. Boundaries are peak-bin bounds, not exact clipping or silence sample positions.</p>
          {candidate.clippedToPage && <p className="review-analysis__scope">This span touches a page boundary and may continue outside the inspected page.</p>}
          <p className="production-help">{candidate.consequence}</p><p>{candidate.proposedRepair}</p>
          <button type="button" className="secondary-button" disabled={busy || recording} onClick={() => record(candidate.key)}>Record waveform review candidate</button>
        </article>)}
      </div>
      {result.analysis.omittedCandidates > 0 && <p className="review-analysis__scope">{result.analysis.omittedCandidates} additional candidate details omitted from this bounded view. No all-clear conclusion is issued.</p>}
      <p className="production-help">Recording creates a project review through the existing service. Its body retains exact take-relative evidence; no project-timeline anchor is fabricated and no gain, cue, clip or approval is changed.</p>
    </>}
  </section>;
}
