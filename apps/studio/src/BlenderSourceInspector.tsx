import { useId, useLayoutEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import {
  readBlenderSource, readBlenderAttempt,
  type BlenderRecipe, type BlenderAttemptSummary, type BlenderLineage,
  type BlenderSourceSnapshot, type BlenderAttemptProjection,
} from "./blenderSourceApi";
import type { Project } from "./types";

interface Props { project: Project; sceneId: string; capsuleId: string; busy: boolean; }
const states = {
  completed: "Completed in the original journal",
  failed_known: "Original attempt failed with a known outcome",
  outcome_unknown: "Original attempt outcome unknown",
};
const unavailable: Record<string, string> = {
  original_attempt_has_no_completed_lineage: "This original attempt has no completed source lineage.",
  current_source_cas_or_lineage_unavailable: "The current source bytes or their lineage could not be verified.",
  original_attempt_generation_changed: "The recorded attempt belongs to an earlier project generation.",
  current_source_binding_changed: "The current capsule references a different source.",
};

function Recipe({ value }: { value: BlenderRecipe }) {
  return <div className="production-notice" aria-label="Saved Blender recipe">
    <strong>Saved product scene · {value.namespace}</strong>
    <span>Coating: {value.coating} · roughness: {value.roughness} · metallic: {value.metallic}</span>
    <span>Samples: {value.samples} · camera curve: {value.camera_curve === "linear" ? "Linear" : "Bezier auto clamped"}</span>
    <span style={{ whiteSpace: "pre-wrap" }}>Annotation: {value.annotation}</span>
  </div>;
}
function Attempt({ value }: { value: BlenderAttemptSummary }) {
  return <div className="production-notice" aria-label="Original Blender attempt status">
    <strong>{states[value.state]}</strong>
    <span>Request: {value.original_request_id}</span>
    <span>Source revision: r{value.expected_before.revision}</span>
    {value.error_code && <span>Recorded failure: {value.error_code}</span>}
    {value.state === "outcome_unknown" && <span>Reconcile this original attempt with its owner before any new native operation.</span>}
  </div>;
}
function Lineage({ project, value }: { project: Project; value: BlenderLineage }) {
  const name = (id: string) => project.assets.find(asset => asset.id === id)?.name ?? "Historical source asset";
  return <div className="production-notice" aria-label="Original Blender source versions">
    <strong>Preserved source versions</strong>
    <span title={value.original.sha256}>Original: {name(value.original.asset_id)} · {value.original.bytes} bytes · {value.original.sha256.slice(0, 12)}…</span>
    <span title={value.edited.sha256}>Edited: {name(value.edited.asset_id)} · {value.edited.bytes} bytes · {value.edited.sha256.slice(0, 12)}…</span>
    <span style={{ whiteSpace: "pre-wrap" }}>Saved annotation change: {value.annotation.before} → {value.annotation.after}</span>
  </div>;
}

/** Mount with the complete saved source key. A late reply cannot replace a new selection. */
export default function BlenderSourceInspector({ project, sceneId, capsuleId, busy }: Props) {
  const id = useId();
  const active = useRef(false), epoch = useRef(0);
  const [reading, setReading] = useState<"source" | "attempt" | null>(null);
  const [source, setSource] = useState<BlenderSourceSnapshot | null>(null);
  const [observation, setObservation] = useState<BlenderAttemptProjection | null>(null);
  const [requestId, setRequestId] = useState(""), [receiptId, setReceiptId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const desktop = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
  const pending = busy || reading !== null;
  useLayoutEffect(() => {
    active.current = true;
    return () => { active.current = false; epoch.current++; };
  }, []);

  const inspect = async (kind: "source" | "attempt") => {
    if (!desktop || pending || !active.current) return;
    const turn = ++epoch.current;
    const current = () => active.current && epoch.current === turn;
    setReading(kind); setError(null);
    setSource(null); setObservation(null);
    try {
      if (kind === "source") {
        const result = await readBlenderSource(project, sceneId, capsuleId);
        if (!current()) return;
        setSource(result);
        const original = result.attempt?.original_request_id;
        if (original) setRequestId(previous => previous || original);
      } else {
        const result = await readBlenderAttempt(project, sceneId, capsuleId, requestId.trim(), receiptId.trim() || null);
        if (!current()) return;
        setObservation(result);
      }
    } catch (reason) {
      if (current()) setError(reason instanceof Error ? reason.message : "The saved Blender read could not be completed.");
    } finally {
      if (current()) setReading(null);
    }
  };

  return <section className="production-shot" aria-label="Saved Blender source inspector">
    <div className="production-section-title"><RefreshCw size={18} aria-hidden="true"/><div>
      <h4>Inspect saved Blender source</h4>
      <p>Read the saved product-scene recipe and original attempt from this project.</p>
    </div></div>
    <p className="production-help">Saved revision r{project.revision}. Source inspection uses the current capsule and its preserved bytes.</p>
    {!desktop && <p className="production-help">Open this project in Desktop to inspect its saved Blender source.</p>}
    <button type="button" className="secondary-button" disabled={!desktop || pending}
      onClick={() => void inspect("source")}>Read saved Blender source</button>
    {reading && <p role="status" aria-live="polite">Reading {reading === "source" ? "saved Blender source" : "original Blender attempt"}…</p>}
    {error && <p role="alert" className="production-error">{error}</p>}
    {source && <div aria-label="Verified saved Blender source">
      <p className="production-help">Source bytes verified · {source.source_kind === "authored_recipe" ? "authored recipe" : "edited native source"} ·
        {" "}<span className="mono" title={source.current_source.sha256}>{source.current_source.sha256.slice(0, 12)}…</span></p>
      <Recipe value={source.effective_recipe}/>
      {source.attempt ? <Attempt value={source.attempt}/> :
        <p className="production-help">No original attempt is recorded for this saved recipe in this local project history.</p>}
      {source.lineage && <Lineage project={project} value={source.lineage}/>}
    </div>}
    <fieldset disabled={!desktop || pending} className="production-notice" aria-label="Read an original Blender attempt">
      <legend>Original attempt</legend>
      <label className="production-field" htmlFor={id + "-request"}><span>Original request ID</span>
        <input id={id + "-request"} value={requestId} maxLength={48} autoComplete="off" spellCheck={false}
          onChange={event => { setRequestId(event.target.value); setObservation(null); }}/></label>
      <label className="production-field" htmlFor={id + "-receipt"}><span>Receipt ID (optional)</span>
        <input id={id + "-receipt"} value={receiptId} maxLength={36} autoComplete="off" spellCheck={false}
          onChange={event => { setReceiptId(event.target.value); setObservation(null); }}/></label>
      <button type="button" className="secondary-button" disabled={!/^[A-Za-z0-9_-]{1,48}$/.test(requestId.trim())}
        onClick={() => void inspect("attempt")}>Read original Blender attempt</button>
    </fieldset>
    {observation && <div aria-label="Observed original Blender attempt">
      <Attempt value={observation.attempt}/>
      {observation.source_cas_verified && observation.effective_recipe ? <>
        <p className="production-help">The current capsule retains the verified source from this completed attempt.</p>
        <Recipe value={observation.effective_recipe}/>
      </> : <p className="production-help">{unavailable[observation.source_unavailable_reason ?? ""] ?? "No current source verification was returned."}</p>}
      {observation.lineage && <Lineage project={project} value={observation.lineage}/>}
    </div>}
    <p className="production-help">These reads preserve the original attempt state. Rendering, decoded-frame verification and human acceptance remain separate observations.</p>
  </section>;
}
