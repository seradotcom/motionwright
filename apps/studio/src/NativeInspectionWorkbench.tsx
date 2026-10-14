import { useEffect, useMemo, useRef, useState } from "react";
import { Eye, Flag, RefreshCw } from "lucide-react";
import { readNativePreviewFrame } from "./api";
import { addFrameOffset, nativeFindingBody, planNativeInspection } from "./nativeInspection";
import { createNativeReviewRepairDraft } from "./nativeReviewRepair";
import NativeInspectionAnalysisPanel from "./NativeInspectionAnalysisPanel";
import { nativeInspectionRequestKey, nativeInspectionWindow } from "./nativeInspectionAnalysis";
import type { InspectionRaster } from "./nativeInspectionAnalysis";
import type { NativeReviewRepairDraft } from "./nativeReviewRepair";
import type { ManualCreativeFinding } from "./nativeInspection";
import type { Change, DeliverableProfile, MotionCanvasRenderEvidence, Project, Scene } from "./types";
import { seconds } from "./types";

interface Sample extends InspectionRaster { url: string; }
const defaultFinding: ManualCreativeFinding = {
  severity: "important", confidence: "probable", violatedConstraint: "",
  observation: "", proposedRepair: "", nodeId: null,
};

async function thumbnail(bytes: Uint8Array, width: number, height: number): Promise<{
  url: string; thumbnail: InspectionRaster["thumbnail"];
}> {
  if (bytes.length < 33 || bytes.length > 16 * 1024 * 1024 ||
      !Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width < 1 || height < 1 ||
      [137, 80, 78, 71, 13, 10, 26, 10].some((value, index) => bytes[index] !== value) ||
      [73, 72, 68, 82].some((value, index) => bytes[index + 12] !== value)) {
    throw new Error("Native PNG header or inspection byte budget is invalid.");
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (view.getUint32(8) !== 13 || view.getUint32(16) !== width || view.getUint32(20) !== height ||
      width * height > 16_777_216) {
    throw new Error("Native PNG dimensions differ from the verified profile or exceed the decode budget.");
  }
  if (typeof createImageBitmap !== "function") {
    throw new Error("This WebView does not support bounded thumbnail decoding. Use the native frame stage instead.");
  }
  const copy = new Uint8Array(bytes.length); copy.set(bytes);
  const scale = Math.min(1, 384 / width, 384 / height);
  const targetWidth = Math.max(1, Math.round(width * scale));
  const targetHeight = Math.max(1, Math.round(height * scale));
  const bitmap = await createImageBitmap(new Blob([copy.buffer], { type: "image/png" }), {
    resizeWidth: targetWidth, resizeHeight: targetHeight, resizeQuality: "high",
  });
  try {
    const canvas = document.createElement("canvas"); canvas.width = targetWidth; canvas.height = targetHeight;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Bounded image preview is unavailable.");
    context.drawImage(bitmap, 0, 0, targetWidth, targetHeight);
    const pixels = context.getImageData(0, 0, targetWidth, targetHeight).data;
    const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob(
      value => value ? resolve(value) : reject(new Error("Could not encode the native thumbnail.")), "image/png",
    ));
    return { url: URL.createObjectURL(blob), thumbnail: { width: targetWidth, height: targetHeight, pixels } };
  } finally { bitmap.close(); }
}

function NativeComparison({ first, second, mode }: { first: Sample; second: Sample; mode: "onion" | "difference" }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true; setError(null);
    const load = async (url: string) => { const image = new Image(); image.src = url; await image.decode(); return image; };
    Promise.all([load(first.url), load(second.url)]).then(([a, b]) => {
      if (!active || !ref.current) return;
      if (a.naturalWidth !== b.naturalWidth || a.naturalHeight !== b.naturalHeight) {
        throw new Error("Native thumbnail dimensions differ; comparison is unavailable.");
      }
      const canvas = ref.current; canvas.width = a.naturalWidth; canvas.height = a.naturalHeight;
      const context = canvas.getContext("2d");
      if (!context) throw new Error("Comparison canvas unavailable.");
      context.clearRect(0, 0, canvas.width, canvas.height);
      context.globalAlpha = 1; context.globalCompositeOperation = "source-over";
      if (mode === "difference") {
        const left = first.thumbnail.pixels, right = second.thumbnail.pixels;
        if (left.length !== right.length || left.length !== canvas.width * canvas.height * 4) {
          throw new Error("Native thumbnail rasters are incompatible.");
        }
        const pixels = new Uint8ClampedArray(left.length);
        for (let i = 0; i < pixels.length; i += 4) {
          const delta = Math.max(Math.abs(left[i] - right[i]), Math.abs(left[i + 1] - right[i + 1]),
            Math.abs(left[i + 2] - right[i + 2]), Math.abs(left[i + 3] - right[i + 3]));
          pixels[i] = pixels[i + 1] = pixels[i + 2] = delta; pixels[i + 3] = 255;
        }
        context.putImageData(new ImageData(pixels, canvas.width, canvas.height), 0, 0);
      } else {
        context.drawImage(a, 0, 0, canvas.width, canvas.height);
        context.globalAlpha = .5;
        context.drawImage(b, 0, 0, canvas.width, canvas.height);
        context.globalAlpha = 1;
      }
    }).catch(reason => { if (active) setError(reason instanceof Error ? reason.message : "Native comparison unavailable."); });
    return () => { active = false; };
  }, [first.url, second.url, mode]);
  return <div className="native-inspection-comparison">{error ? <p role="alert">{error}</p> :
    <canvas ref={ref} aria-label={mode === "onion" ? "Onion overlay of two native-frame thumbnails" : "Absolute channel difference of two native-frame thumbnails"}/>}</div>;
}

export default function NativeInspectionWorkbench({ project, scene, displayProfile, evidence, commit, busy, onSeek, onOpenRender, onDraftRepair }: {
  project: Project; scene: Scene | null; displayProfile: DeliverableProfile | null; evidence: MotionCanvasRenderEvidence | null;
  commit: (change: Change) => Promise<void>; busy: boolean; onSeek: (time: number) => void; onOpenRender: () => void;
  onDraftRepair: (draft: NativeReviewRepairDraft) => void;
}) {
  const overview = useMemo(() => planNativeInspection(project, scene, displayProfile, evidence), [project, scene, displayProfile, evidence]);
  const overviewKey = nativeInspectionRequestKey(overview);
  const [samplingMode, setSamplingMode] = useState<"overview" | "window">("overview");
  const [windowSelection, setWindowSelection] = useState({ key: "", start: "" });
  const windowStart = windowSelection.key === overviewKey ? windowSelection.start : String(overview?.samples[0]?.frameIndex ?? 0);
  const planned = useMemo(() => {
    try {
      const requested = /^(0|[1-9][0-9]*)$/.test(windowStart) ? Number(windowStart) : NaN;
      return { plan: overview && samplingMode === "window" ? nativeInspectionWindow(overview, requested) : overview, error: null };
    } catch (reason) { return { plan: null, error: reason instanceof Error ? reason.message : "Native window unavailable." }; }
  }, [overview, samplingMode, windowStart]);
  const plan = planned.plan;
  const signature = nativeInspectionRequestKey(plan);
  const currentSignature = useRef(signature); currentSignature.current = signature;
  const [batch, setBatch] = useState<{ key: string; samples: Sample[] } | null>(null);
  // Hide stale evidence immediately, before passive-effect cleanup has a chance to run.
  const samples = batch?.key === signature ? batch.samples : [];
  const [loading, setLoading] = useState(false), [error, setError] = useState<string | null>(null);
  const [firstIndex, setFirstIndex] = useState(0), [secondIndex, setSecondIndex] = useState(1);
  const [mode, setMode] = useState<"onion" | "difference">("onion");
  const [finding, setFinding] = useState<ManualCreativeFinding>(defaultFinding);
  const urls = useRef<string[]>([]), epoch = useRef(0);
  const releaseUrls = () => { urls.current.forEach(URL.revokeObjectURL); urls.current = []; };
  useEffect(() => {
    epoch.current++; releaseUrls(); setBatch(null); setLoading(false); setError(null);
    return () => { epoch.current++; releaseUrls(); };
  }, [signature]);
  const inspect = async () => {
    if (!plan || busy || loading) return;
    const requestSignature = signature, active = ++epoch.current;
    const isCurrent = () => active === epoch.current && currentSignature.current === requestSignature;
    releaseUrls(); setBatch(null); setLoading(true); setError(null);
    const pending: Sample[] = [];
    try {
      // At most eight existing-authority reads; no render dispatch or parallel decoder burst.
      for (const selection of plan.samples) {
        if (!isCurrent()) return;
        const bytes = await readNativePreviewFrame(project, selection.token, selection.frameIndex);
        if (!isCurrent()) return;
        if (bytes.length < 33 || bytes.length > 16 * 1024 * 1024) throw new Error("Native PNG exceeds the bounded inspection size.");
        const copy = new Uint8Array(bytes.length); copy.set(bytes);
        const pngSha256 = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", copy.buffer)), b => b.toString(16).padStart(2, "0")).join("");
        if (!isCurrent()) return;
        const decoded = await thumbnail(bytes, plan.width, plan.height);
        if (!isCurrent()) { URL.revokeObjectURL(decoded.url); return; }
        urls.current.push(decoded.url);
        pending.push({ ...decoded, frameIndex: selection.frameIndex, timelineTime: selection.timelineTime,
          pngSha256, width: plan.width, height: plan.height });
      }
      if (isCurrent()) { setBatch({ key: requestSignature, samples: pending }); setFirstIndex(0); setSecondIndex(Math.min(1, pending.length - 1)); }
    } catch (reason) {
      if (isCurrent()) { releaseUrls(); setBatch(null); setError(reason instanceof Error ? reason.message : "Native inspection unavailable."); }
    } finally { if (isCurrent()) setLoading(false); }
  };
  const first = samples[firstIndex], second = samples[secondIndex];
  const draftRepair = () => {
    if (!plan || !first || !scene) return;
    try { onDraftRepair(createNativeReviewRepairDraft(project, scene, plan, first, finding)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not prepare scoped repair."); }
  };
  const record = async () => {
    if (!plan || !first || !scene) return;
    try {
      if (finding.nodeId && !scene.nodes.some(n => n.id === finding.nodeId)) throw new Error("Finding target is outside this scene.");
      await commit({ type: "add_review", kind: "creative", resource: "scene:" + scene.id,
        body: nativeFindingBody(plan, first, finding), start: first.timelineTime,
        end: addFrameOffset(first.timelineTime, 1, plan.frameRate), locale: null, profile_id: plan.profileId });
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not record native review."); }
  };
  return <div className="production-native-inspection">
    <div className="production-section-title"><Eye size={18}/><div><h2>Native frame inspection</h2><p>Motion Canvas v1 · bounded contact sheets, consecutive windows and evidence-linked review.</p></div></div>
    <div className="production-notice"><strong>Native source frames only</strong><span>No DOM study or placeholder is substituted when the renderer receipt is missing or stale. Thumbnail comparisons are inspection aids, not proof of pixel equivalence or creative quality.</span></div>
    {!overview ? <div className="production-empty"><h3>No current native frame grant</h3><p>Select a scene and matching output profile, then produce a native preview in Deliver. Changed revisions, expired grants, different profiles and oversized decode requests are refused.</p><button className="secondary-button" onClick={onOpenRender}>Open Deliver and render preview</button></div> : <>
      <div className="native-inspection-window-controls">
        <label className="production-field"><span>Native inspection sampling</span><select value={samplingMode} disabled={loading} onChange={event => setSamplingMode(event.target.value as "overview" | "window")}>
          <option value="overview">Sampled contact sheet</option><option value="window">Consecutive in-scene window</option>
        </select></label>
        {samplingMode === "window" && <label className="production-field"><span>Window first native frame</span>
          <input type="number" min={overview.samples[0].frameIndex} max={overview.samples.at(-1)?.frameIndex} step="1" value={windowStart} disabled={loading}
            onChange={event => setWindowSelection({ key: overviewKey, start: event.target.value })}/>
        </label>}
      </div>
      {planned.error && <p role="alert" className="production-error">{planned.error}</p>}
      <div className="production-button-row"><button className="primary-button" disabled={busy || loading || !plan} onClick={inspect}><RefreshCw size={14}/>{loading ? "Reading verified native frames…" : "Inspect current native frames"}</button><span className="production-help">Up to eight sequential reads · {overview.width} × {overview.height} · source r{overview.revision}</span></div>
      {error && <p className="production-error" role="alert">{error}</p>}
      <div className="native-inspection-grid">{samples.map((sample, index) => <button key={sample.frameIndex} className={firstIndex === index ? "selected" : ""} onClick={() => { setFirstIndex(index); onSeek(seconds(sample.timelineTime)); }} aria-label={`Inspect native frame ${sample.frameIndex}`}>
        <img src={sample.url} alt={`Native frame ${sample.frameIndex}, source r${overview.revision}`} draggable={false}/><span>f{sample.frameIndex} · {seconds(sample.timelineTime).toFixed(3)} s</span><small>SHA-256 {sample.pngSha256.slice(0, 12)}…</small></button>)}</div>
      {plan && samples.length > 0 && <NativeInspectionAnalysisPanel key={signature} project={project} plan={plan} samples={samples} busy={busy || loading} commit={commit}
        onInspect={(frame, reference) => {
          const a = samples.findIndex(s => s.frameIndex === frame), b = samples.findIndex(s => s.frameIndex === reference);
          if (a >= 0) { setFirstIndex(a); onSeek(seconds(samples[a].timelineTime)); }
          if (b >= 0) setSecondIndex(b);
        }}/>}
      {first && second && <><div className="production-button-row"><label className="production-field"><span>Compare with</span><select value={secondIndex} onChange={event => setSecondIndex(Number(event.target.value))}>{samples.map((sample, index) => <option key={sample.frameIndex} value={index}>Frame {sample.frameIndex} · {seconds(sample.timelineTime).toFixed(3)} s</option>)}</select></label><label className="production-field"><span>Inspection mode</span><select value={mode} onChange={event => setMode(event.target.value as "onion" | "difference")}><option value="onion">50% onion overlay</option><option value="difference">Channel difference</option></select></label></div>
        <NativeComparison first={first} second={second} mode={mode}/><p className="production-help">Comparison uses downsampled, color-managed browser thumbnails. Difference intensity is the maximum absolute RGBA channel delta, including alpha. Source PNG digests are preserved; zero thumbnail difference is not full-resolution equivalence.</p>
        <section className="native-inspection-finding"><h3>Record an actionable finding</h3><p className="production-help">This is a manual interpretation of the selected source frame. Recording it creates a revisioned review thread and makes the current frame grant stale. Drafting a repair only opens the scoped editor; neither action declares a technical or creative PASS.</p>
          <div className="production-plan-grid"><label className="production-field"><span>Severity</span><select value={finding.severity} onChange={event => setFinding({ ...finding, severity: event.target.value as ManualCreativeFinding["severity"] })}><option value="blocking">Blocking</option><option value="important">Important</option><option value="suggestion">Suggestion</option></select></label><label className="production-field"><span>Confidence</span><select value={finding.confidence} onChange={event => setFinding({ ...finding, confidence: event.target.value as ManualCreativeFinding["confidence"] })}><option value="certain">Certain</option><option value="probable">Probable</option><option value="uncertain">Uncertain</option></select></label></div>
          <label className="production-field"><span>Affected object</span><select value={finding.nodeId ?? ""} onChange={event => setFinding({ ...finding, nodeId: event.target.value || null })}><option value="">Scene-level finding</option>{scene?.nodes.map(node => <option key={node.id} value={node.id}>{node.name}</option>)}</select></label>
          {([ ["violatedConstraint", "Violated constraint"], ["observation", "What is visible in this frame"], ["proposedRepair", "Proposed localized repair"] ] as const).map(([field, label]) => <label className="production-field" key={field}><span>{label}</span><textarea rows={3} maxLength={800} value={finding[field]} onChange={event => setFinding({ ...finding, [field]: event.target.value })}/></label>)}
          <div className="production-button-row">
            <button className="primary-button" disabled={busy || loading || !finding.observation.trim() || !finding.violatedConstraint.trim() || !finding.proposedRepair.trim()} onClick={record}><Flag size={14}/> Record source-anchored review</button>
            <button className="secondary-button" disabled={busy || loading || !finding.nodeId || !finding.observation.trim() || !finding.violatedConstraint.trim() || !finding.proposedRepair.trim()} onClick={draftRepair}>Draft scoped repair</button>
          </div>
          <p className="production-help">Drafting selects the affected object and cites this frame SHA-256. It does not invent a replacement, create a patch, or bypass the existing commit approval.</p>
        </section></>}
    </>}
  </div>;
}
