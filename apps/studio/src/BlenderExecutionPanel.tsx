import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createBlenderCycleIntent, prepareBlenderCycle, startBlenderCycle,
  type BlenderCycleIntent, type BlenderCyclePlan } from "./blenderCycleApi";
import { createBlenderLitIntent, inspectBlenderLitSource, prepareBlenderLitStill, startBlenderLitStill,
  readBlenderLitAttempt, readBlenderLitPng, type BlenderLitIntent, type BlenderLitPlan,
  type BlenderLitSource, type BlenderLitAttempt } from "./blenderLitApi";
import { readBlenderAttempt, type BlenderSourceSnapshot } from "./blenderSourceApi";
import { readBlenderOperations, retainBlenderOperation, type BlenderOperation } from "./blenderOperationRetention";
import type { Project } from "./types";

interface Props {
  project: Project; sceneId: string; busy: boolean; refreshProject: () => Promise<Project>;
  mode: "lit" | "cycle"; source?: BlenderSourceSnapshot;
}
type Prepared = { intent: BlenderCycleIntent; plan: BlenderCyclePlan; kind: "cycle" }
  | { intent: BlenderLitIntent; plan: BlenderLitPlan; kind: "lit" };
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const knownFailure = (value: unknown) => !!value && typeof value === "object" && "outcome_known" in value && value.outcome_known === true;
function operationState(value: BlenderLitAttempt): BlenderOperation["state"] {
  return value.state === "completed" ? "completed" : value.state === "failed_known" ? "failed_known" : "unconfirmed";
}

/** Source authoring stays in its original editor; this is explicit Foundation
 * preparation/dispatch and read-only recovery through the existing Desktop.
 */
export default function BlenderExecutionPanel({ project, sceneId, busy, refreshProject, mode, source }: Props) {
  const id = useId(), live = useRef(false), epoch = useRef(0), sending = useRef(false), externalBusy = useRef(busy);
  externalBusy.current = busy;
  const scope = JSON.stringify([project, sceneId, mode, source?.current_source ?? null]);
  const scopeRef = useRef(scope); scopeRef.current = scope;
  const [litSource, setLitSource] = useState<BlenderLitSource | null>(null);
  const [prepared, setPrepared] = useState<Prepared | null>(null);
  const [annotation, setAnnotation] = useState("");
  const [originals, setOriginals] = useState<BlenderOperation[]>([]);
  const [selected, setSelected] = useState("");
  const [result, setResult] = useState<BlenderLitAttempt | null>(null);
  const [phase, setPhase] = useState<string | null>(null), [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null), [retentionError, setRetentionError] = useState<string | null>(null);
  const [image, setImage] = useState<{ blob: Blob; width: number; height: number } | null>(null), [url, setUrl] = useState<string | null>(null);
  const [decoded, setDecoded] = useState(false);
  const desktop = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
  const refreshCopies = () => {
    try {
      const rows = readBlenderOperations(project.id).filter(row => row.kind === mode && row.intent.source.scene_id === sceneId
        && (mode !== "cycle" || ("capsule_id" in row.intent.source && row.intent.source.capsule_id === source?.capsule_id)));
      setOriginals(rows); setRetentionError(null);
      setSelected(prior => rows.some(row => row.intent.request_id === prior) ? prior : rows.at(-1)?.intent.request_id ?? "");
    } catch (reason) { setRetentionError(message(reason)); }
  };
  useLayoutEffect(() => { live.current = true; return () => { live.current = false; epoch.current++; }; }, []);
  useLayoutEffect(() => {
    epoch.current++; sending.current = false; setPhase(null); setPrepared(null); setLitSource(null);
    setResult(null); setImage(null); setError(null); setNotice(null); refreshCopies();
    // Full saved source ownership invalidates preparations, never authored text.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);
  useEffect(() => {
    if (!image) { setUrl(null); setDecoded(false); return; }
    const local = URL.createObjectURL(image.blob); setUrl(local); setDecoded(false);
    return () => URL.revokeObjectURL(local);
  }, [image]);
  const selectedOriginal = originals.find(row => row.intent.request_id === selected);
  const sameSource = (row: BlenderOperation) => mode === "lit"
    ? "fingerprint" in row.intent && row.intent.fingerprint === litSource?.fingerprint
    : row.intent.source_sha256 === source?.current_source.sha256;
  const originalAttempted = originals.some(row => row.state !== "prepared" && sameSource(row));
  const pending = busy || phase !== null;
  const canPrepare = mode === "lit" ? !!litSource && !litSource.attempted && !originalAttempted
    : !!source && source.source_kind === "authored_recipe" && !source.attempt && !originalAttempted
      && /^[\x20-\x7e]+(?:\n[\x20-\x7e]+)?$/.test(annotation)
      && annotation.split("\n").every(line => line.length <= 32) && annotation !== source.effective_recipe.annotation;
  const retain = (value: Prepared, state: BlenderOperation["state"]) => {
    retainBlenderOperation({ schema: "motionwright.blender.original-operation/1", kind: value.kind, intent: value.intent, state });
    if (live.current && scopeRef.current === scope) refreshCopies();
  };
  const markRead = (original: BlenderOperation | undefined, state: BlenderOperation["state"]) => {
    if (!original || original.state === "prepared" || original.state === "completed" || original.state === "failed_known") return;
    retainBlenderOperation({ ...original, state });
    if (live.current && scopeRef.current === scope) refreshCopies();
  };
  const perform = async (kind: "inspect" | "prepare" | "start" | "read" | "reload" | "preview") => {
    if (!desktop || sending.current || externalBusy.current || !live.current) return;
    if ((kind === "prepare" || kind === "start") && retentionError) return;
    if (kind === "prepare" && !canPrepare) return;
    if (kind === "start" && (!prepared || originalAttempted)) return;
    const turn = ++epoch.current, submittedScope = scope;
    const current = () => live.current && epoch.current === turn && scopeRef.current === submittedScope;
    const barrier = () => {
      if (!current() || externalBusy.current) throw new Error("The saved selection or authored input changed before Blender dispatch.");
    };
    sending.current = true; setPhase(kind); setError(null); setNotice(null);
    try {
      if (kind === "reload") { barrier(); await refreshProject(); return; }
      if (kind === "inspect") {
        const inspected = await inspectBlenderLitSource(project, sceneId);
        if (!current()) return;
        setLitSource(inspected); setPrepared(null); setResult(null); setImage(null);
        if (inspected.attempted) {
          const original = await readBlenderLitAttempt(project, sceneId, inspected.attempt_key);
          if (current()) setResult(original);
        }
      } else if (kind === "prepare") {
        if (mode === "lit" && litSource) {
          const intent = createBlenderLitIntent(project, litSource);
          const plan = await prepareBlenderLitStill(project, intent, barrier);
          if (current()) {
            setPrepared(plan ? { kind: "lit", intent, plan } : null);
            setNotice(plan ? "Blender session prepared. Review the output below, then start explicitly." : "No Blender session was selected.");
          }
        } else if (source) {
          const intent = createBlenderCycleIntent(project, source, annotation);
          const plan = await prepareBlenderCycle(project, intent, barrier);
          if (current()) {
            setPrepared(plan ? { kind: "cycle", intent, plan } : null);
            setNotice(plan ? "Three source sessions prepared. Review the annotation change, then start explicitly." : "Source preparation was canceled.");
          }
        }
      } else if (kind === "start" && prepared) {
        const original = prepared;
        let gates = 0;
        const beforeDispatch = () => {
          barrier();
          retain(original, ++gates === 1 ? "prepared" : "sent");
        };
        try {
          if (original.kind === "lit") {
            const outcome = await startBlenderLitStill(project, original.intent, original.plan, beforeDispatch);
            retain(original, operationState(outcome));
            if (current()) {
              setResult(outcome); setPrepared(null);
              setNotice(outcome.state === "completed"
                ? "The original PNG and editable Blender source are saved. Read the latest saved project to open them."
                : "The original operation is retained. Read its result before any new render.");
            }
          } else {
            const outcome = await startBlenderCycle(project, original.intent, original.plan, beforeDispatch);
            const state = "Ok" in outcome.outcome ? "completed" : knownFailure(outcome.outcome.Err) ? "failed_known" : "unconfirmed";
            retain(original, state);
            if (current()) {
              setPrepared(null);
              setNotice(state === "completed"
                ? "The original and edited Blender sources are saved, and the edited source was reopened. Read the latest saved project to inspect both versions."
                : "The original source cycle is retained. Read its result before another native operation.");
            }
          }
        } catch (reason) {
          if (gates >= 2) {
            try { retain(original, "unconfirmed"); } catch { /* Never replace the original transport failure or retry it. */ }
          }
          if (current()) { setPrepared(null); throw reason; }
        }
      } else if (kind === "read") {
        if (mode === "lit") {
          const key = selectedOriginal && "fingerprint" in selectedOriginal.intent
            ? "blender-lit-" + selectedOriginal.intent.fingerprint : litSource?.attempt_key;
          if (!key) throw new Error("Inspect the saved scene or select an original operation.");
          const original = await readBlenderLitAttempt(project, sceneId, key);
          if (original && selectedOriginal && (original.original_request_id !== selectedOriginal.intent.request_id
              || original.source_sha256 !== selectedOriginal.intent.source_sha256)) {
            throw new Error("The readback belongs to a different original Blender request.");
          }
          if (original) markRead(selectedOriginal, operationState(original));
          if (current()) {
            setResult(original); setImage(null);
            if (!original) setNotice("No original receipt was returned for this key. No render was sent or repeated.");
          }
        } else if (source && selectedOriginal) {
          const original = await readBlenderAttempt(project, sceneId, source.capsule_id, selectedOriginal.intent.request_id, null);
          markRead(selectedOriginal, original.attempt.state === "completed" ? "completed"
            : original.attempt.state === "failed_known" ? "failed_known" : "unconfirmed");
          if (current()) setNotice(original.attempt.state === "completed"
            ? "The original source cycle is complete. The saved source inspector shows its retained versions after reading the current project."
            : "Original source cycle: " + original.attempt.state.replaceAll("_", " ") + ". No new operation was started.");
        }
      } else if (kind === "preview" && result?.saved_assets) {
        const blob = await readBlenderLitPng(project, result);
        if (current()) setImage({ blob, width: result.saved_assets.width, height: result.saved_assets.height });
      }
    } catch (reason) { if (current()) setError(message(reason)); }
    finally { if (current()) { sending.current = false; setPhase(null); } }
  };

  return <section className="production-shot" aria-label={mode === "lit" ? "Blender illuminated still" : "Blender editable source cycle"}>
    <h4>{mode === "lit" ? "Render an illuminated still" : "Build and reopen the editable product source"}</h4>
    <p className="production-help">{mode === "lit"
      ? "Render the saved static scene with an area light, and keep the PNG together with its editable Blender original."
      : "Create the original from this saved recipe, apply the annotation below in a separate Blender session, and reopen the edited source. Both source versions stay in this project."}</p>
    {!desktop && <p className="production-help">Open this project in Desktop to use its Blender session.</p>}
    <fieldset disabled={!desktop || pending}>
      <legend>{mode === "lit" ? "Saved scene" : "Annotation change"}</legend>
      {mode === "lit" ? <>
        <button type="button" className="secondary-button" onClick={() => void perform("inspect")}>Inspect saved Blender scene</button>
        {litSource && <p>{litSource.object_count} objects · {litSource.width} × {litSource.height} · saved revision r{litSource.source.revision}</p>}
      </> : <label className="production-field" htmlFor={id + "-annotation"}><span>Edited Blender annotation</span>
        <textarea id={id + "-annotation"} value={annotation} maxLength={65} rows={2} onChange={event => {
          setAnnotation(event.target.value); setPrepared(null);
        }}/><small>One or two lines, up to 32 ASCII characters each. Choose text different from the saved annotation.</small>
      </label>}
      <button type="button" className="secondary-button" disabled={!canPrepare || !!retentionError}
        onClick={() => void perform("prepare")}>{mode === "lit" ? "Prepare Blender still" : "Prepare editable source cycle"}</button>
      {prepared && <div className="production-notice">
        <strong>{prepared.kind === "lit" ? "Ready to create the original still" : "Ready to create, edit and reopen the original"}</strong>
        {prepared.kind === "cycle" && <span style={{ whiteSpace: "pre-wrap" }}>
          {prepared.plan.annotation_before} → {prepared.plan.annotation_after}</span>}
        <span>{prepared.kind === "lit" ? "A PNG image and an editable .blend will be added to this project."
          : "Three independent Blender sessions will preserve the original, save the annotation edit and verify its reopening."}</span>
        <button type="button" className="primary-button" disabled={originalAttempted || !!retentionError}
          onClick={() => void perform("start")}>{mode === "lit" ? "Render and save Blender still" : "Create, edit and reopen Blender source"}</button>
      </div>}
    </fieldset>
    {(originalAttempted || litSource?.attempted) && <p role="status">This saved source already has an original operation. Read its retained result.</p>}
    {originals.length > 0 && <label className="production-field" htmlFor={id + "-original"}><span>Original Blender operation</span>
      <select id={id + "-original"} value={selected} disabled={pending} onChange={event => {
        setSelected(event.target.value); setResult(null); setImage(null);
      }}>{originals.map(row => <option key={row.intent.request_id} value={row.intent.request_id}>
        r{row.intent.source.revision} · {row.state.replaceAll("_", " ")} · {row.intent.request_id.slice(-8)}
      </option>)}</select>
    </label>}
    <div className="production-button-row">
      <button type="button" className="secondary-button" disabled={!desktop || pending || (!selectedOriginal && !(mode === "lit" && litSource?.attempted))}
        onClick={() => void perform("read")}>Read original Blender result</button>
      <button type="button" className="secondary-button" disabled={!desktop || pending} onClick={() => void perform("reload")}>Open latest saved Blender project</button>
      {mode === "lit" && result?.state === "completed" && <button type="button" className="secondary-button" disabled={pending || !desktop}
        onClick={() => void perform("preview")}>Preview saved Blender PNG</button>}
    </div>
    {phase && <p role="status" aria-live="polite">{phase === "start" ? "The original Blender operation is running…" : "Reading or preparing the saved Blender operation…"}</p>}
    {notice && <p role="status" className="production-help">{notice}</p>}
    {retentionError && <p role="alert" className="production-error">{retentionError} Source inspection and export remain available.</p>}
    {error && <p role="alert" className="production-error">{error}</p>}
    {result && <div role="status" className="production-notice">
      <strong>{result.state === "completed" ? "PNG and editable original saved" : "Original still: " + result.state.replaceAll("_", " ")}</strong>
      <span>{result.source_current ? "Matches the current saved scene." : "Belongs to its original saved source. Read the latest project before previewing."}</span>
      {result.state === "native_completed_adoption_unconfirmed" && <span>Blender returned a result, but saving all project assets is unconfirmed. The original import receipts retain any committed part.</span>}
    </div>}
    {url && image && <figure className="native-frame-stage">
      <img src={url} alt="Original illuminated Blender still" style={{ maxWidth: "100%", height: "auto" }}
        onLoad={event => {
          const loaded = event.currentTarget;
          if (loaded.naturalWidth === image.width && loaded.naturalHeight === image.height) setDecoded(true);
          else { setDecoded(false); setError("The displayed PNG does not match its saved dimensions."); }
        }}
        onError={() => { setDecoded(false); setError("The browser could not decode the retained PNG."); }}/>
      {decoded && <figcaption>{image.width} × {image.height} · <a href={url} download="motionwright-blender-still.png">Download displayed PNG</a></figcaption>}
    </figure>}
    <p className="production-help">Use an existing Blender session with permission for this project.
      {mode === "lit" ? " This static lighting profile preserves source geometry; it does not replace the Canvas preview." : " This source cycle does not render the full movie."}
      {" "}The saved originals are also included in portable project export.</p>
  </section>;
}
