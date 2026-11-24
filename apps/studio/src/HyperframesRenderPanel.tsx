import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import NativeFrameStage from "./NativeFrameStage";
import type { DecodedPreviewFrame } from "./previewRecovery";
import type { Project } from "./types";
import type { HyperframesSourceSnapshot } from "./hyperframesSourceApi";
import {
  dispatchHyperframesRender, hyperframesIntentUnconfirmed, hyperframesRuntimeDigest,
  prepareHyperframesRender, readHyperframesRenderIntents, readOriginalHyperframesRender,
  type HyperframesRenderIntent, type HyperframesRenderResponse,
} from "./hyperframesRenderApi";

export default function HyperframesRenderPanel({ project, source, desktopMode }: {
  project: Project; source: HyperframesSourceSnapshot | null; desktopMode: boolean;
}) {
  const id = useId(), epoch = useRef(0), live = useRef(false), busy = useRef(false);
  const [runtime, setRuntime] = useState("");
  const [attempts, setAttempts] = useState<HyperframesRenderIntent[]>([]);
  const [selected, setSelected] = useState("");
  const [response, setResponse] = useState<HyperframesRenderResponse | null>(null);
  const [phase, setPhase] = useState<"render" | "read" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [storageError, setStorageError] = useState<string | null>(null);
  const [frame, setFrame] = useState(0);
  const [decoded, setDecoded] = useState<DecodedPreviewFrame | null>(null);
  const [download, setDownload] = useState<string | null>(null);
  const scope = JSON.stringify([project, source?.fingerprint ?? null]);
  const scopeRef = useRef(scope);
  const refresh = () => {
    try {
      const retained = readHyperframesRenderIntents(project);
      setAttempts(retained); setStorageError(null);
      setSelected(prior => retained.some(item => item.request_id === prior) ? prior : retained.at(-1)?.request_id ?? "");
    } catch (reason) {
      setStorageError(reason instanceof Error ? reason.message : "The original request copy cannot be read.");
    }
  };
  useLayoutEffect(() => {
    live.current = true;
    return () => { live.current = false; epoch.current++; };
  }, []);
  useLayoutEffect(() => {
    epoch.current++; scopeRef.current = scope; busy.current = false;
    setResponse(null); setError(null); setPhase(null); setFrame(0); setDecoded(null);
    refresh();
    // The complete source scope is the ownership boundary.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);
  useEffect(() => {
    if (!decoded || decoded.frameIndex !== frame || !response?.preview) { setDownload(null); return; }
    const copy = new Uint8Array(decoded.bytes);
    const url = URL.createObjectURL(new Blob([copy.buffer], { type: "image/png" }));
    setDownload(url);
    return () => URL.revokeObjectURL(url);
  }, [decoded, frame, response?.preview]);
  const currentAttempt = attempts.find(item => item.request_id === selected);
  const pending = attempts.some(hyperframesIntentUnconfirmed);
  const perform = async (kind: "render" | "read") => {
    if (!desktopMode || busy.current || !live.current || storageError) return;
    if (kind === "render" && (!source || !hyperframesRuntimeDigest(runtime) || pending)) return;
    if (kind === "read" && !currentAttempt) return;
    const turn = ++epoch.current, key = scope;
    const current = () => live.current && epoch.current === turn && scopeRef.current === key;
    const beforeDispatch = () => { if (!current()) throw new Error("The saved source selection changed before Native dispatch."); };
    busy.current = true; setPhase(kind); setError(null); setResponse(null); setDecoded(null);
    try {
      let result: HyperframesRenderResponse;
      if (kind === "render" && source) {
        const intent = prepareHyperframesRender(project, source, runtime);
        setSelected(intent.request_id); refresh();
        result = await dispatchHyperframesRender(project, source, intent, beforeDispatch);
      } else if (currentAttempt) {
        result = await readOriginalHyperframesRender(project, currentAttempt);
      } else { return; }
      if (current()) { setResponse(result); setFrame(0); refresh(); }
    } catch (reason) {
      if (current()) {
        setError(reason instanceof Error ? reason.message : "The original render could not be confirmed.");
        refresh();
      }
    } finally {
      if (current()) { busy.current = false; setPhase(null); }
    }
  };
  return <section aria-label="HyperFrames native PNG rendering" className="portable-operation">
    <h4>Render a native PNG sequence</h4>
    <p className="production-help">Use the saved composition with the explicitly installed HyperFrames 0.8.181 profile
      and the Semwright connection selected in Integrations. The provider checks the runtime before changing its native source.
      This action does not install software or grant provider permissions.</p>
    <fieldset disabled={!desktopMode || phase !== null || !!storageError}>
      <legend>Installed runtime</legend>
      <label className="production-field" htmlFor={id + "-runtime"}><span>Runtime lock SHA-256</span>
        <input id={id + "-runtime"} value={runtime} autoComplete="off" spellCheck={false} maxLength={64}
          onChange={event => setRuntime(event.target.value)}/></label>
      <p className="production-help">Use the digest of the owner's installed runtime-lock.json. A different runtime is rejected before rendering.</p>
      <button type="button" className="secondary-button"
        disabled={!source || !hyperframesRuntimeDigest(runtime) || pending}
        onClick={() => void perform("render")}>Render saved HyperFrames source</button>
    </fieldset>
    {pending && <p role="status">An original request is retained and has not been confirmed.
      Read that operation before starting another render.</p>}
    {attempts.length > 0 && <fieldset disabled={!desktopMode || phase !== null || !!storageError}>
      <legend>Original operations</legend>
      <label className="production-field" htmlFor={id + "-attempt"}><span>Retained HyperFrames request</span>
        <select id={id + "-attempt"} value={selected} onChange={event => {
          setSelected(event.target.value); setResponse(null); setDecoded(null);
        }}>{attempts.map(item => <option key={item.request_id} value={item.request_id}>
          {"r" + item.source.revision + " · " + item.retention.replaceAll("_", " ") + " · " + item.request_id.slice(-8)}
        </option>)}</select></label>
      <button type="button" className="secondary-button" disabled={!currentAttempt}
        onClick={() => void perform("read")}>Read original HyperFrames result</button>
      <p className="production-help">Reads the saved operation and original PNG files. This never restarts a render.</p>
    </fieldset>}
    {phase && <p role="status">{phase === "render" ? "The original native operation is running…" : "Reading the original retained operation…"}</p>}
    {storageError && <p role="alert" className="production-error">{storageError} Source export remains available.</p>}
    {error && <p role="alert" className="production-error">{error}</p>}
    {response && <div role="status">
      {response.attempt.state === "completed"
        ? <p>The provider completed the original {response.attempt.frame_count}-frame PNG sequence.
            {response.attempt.source_current ? "" : " It belongs to an earlier saved source."}</p>
        : response.attempt.state === "failed_known"
          ? <p>The original operation has a known failure. Review the runtime and source before a new explicit attempt.</p>
          : <p>The original outcome is unconfirmed. Its request is preserved and will not be replayed.</p>}
      {response.preview_problem && <p>{response.preview_problem}</p>}
    </div>}
    {response?.preview && <div aria-label="HyperFrames original frame viewer">
      <label className="production-field" htmlFor={id + "-frame"}><span>Native frame {frame + 1} of {response.preview.frame_count}</span>
        <input id={id + "-frame"} type="range" min={0} max={response.preview.frame_count - 1} step={1} value={frame}
          onChange={event => { setFrame(Number(event.target.value)); setDecoded(null); }}/></label>
      <div style={{ position: "relative", minHeight: 320 }}>
        <NativeFrameStage project={project} editorialPlaying={false} onDecoded={setDecoded}
          selection={{ token: response.preview.token, segmentId: response.preview.segment_id,
            frameIndex: frame, frameCount: response.preview.frame_count }}/>
      </div>
      {download && <a className="secondary-button" href={download}
        download={"hyperframes-frame_" + String(frame + 1).padStart(6, "0") + ".png"}>Save displayed PNG</a>}
      <p className="production-help">Original PNG sequence at 30 fps. Each displayed file is checked against the saved render's digest.
        A final audiovisual master and visual acceptance are separate.</p>
    </div>}
  </section>;
}
