import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import NativeFrameStage from "./NativeFrameStage";
import type { DecodedPreviewFrame } from "./previewRecovery";
import type { Project } from "./types";
import {
  readOriginalRuntimeProfile, readRuntimeRetention, runtimeProfileInventory, type RuntimeInstallation,
} from "./runtimeProfileApi";
import type { HyperframesSourceSnapshot } from "./hyperframesSourceApi";
import {
  dispatchHyperframesRender, hyperframesIntentUnconfirmed, hyperframesRuntimeDigest,
  prepareHyperframesRender, readHyperframesRenderIntents, readOriginalHyperframesRender, observeOriginalHyperframesJob,
  readHyperframesRenderHistory,
  type HyperframesRenderHistory, type HyperframesRenderIntent, type HyperframesRenderResponse,
} from "./hyperframesRenderApi";

export default function HyperframesRenderPanel({ project, source, desktopMode }: {
  project: Project; source: HyperframesSourceSnapshot | null; desktopMode: boolean;
}) {
  const id = useId(), epoch = useRef(0), live = useRef(false), busy = useRef(false);
  const [runtime, setRuntime] = useState("");
  const [profiles, setProfiles] = useState<RuntimeInstallation[]>([]);
  const [selectedProfile, setSelectedProfile] = useState("");
  const [verifiedProfile, setVerifiedProfile] = useState<RuntimeInstallation | null>(null);
  const [attempts, setAttempts] = useState<HyperframesRenderIntent[]>([]);
  const [history, setHistory] = useState<HyperframesRenderHistory | null>(null);
  const [selected, setSelected] = useState("");
  const [response, setResponse] = useState<HyperframesRenderResponse | null>(null);
  const [phase, setPhase] = useState<"render" | "read" | "observe" | "profiles" | "runtime" | "history" | "older" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [storageError, setStorageError] = useState<string | null>(null);
  const [frame, setFrame] = useState(0);
  const [decoded, setDecoded] = useState<DecodedPreviewFrame | null>(null);
  const [download, setDownload] = useState<string | null>(null);
  const scope = JSON.stringify([project, source?.fingerprint ?? null]);
  const scopeRef = useRef(scope);
  const refresh = (saved: HyperframesRenderIntent[] = history?.items ?? []) => {
    try {
      const retained = readHyperframesRenderIntents(project);
      setAttempts(retained); setStorageError(null);
      setSelected(prior => [...retained, ...saved].some(item => item.request_id === prior)
        ? prior : retained.at(-1)?.request_id ?? saved[0]?.request_id ?? "");
    } catch (reason) {
      setAttempts([]);
      setSelected(prior => saved.some(item => item.request_id === prior) ? prior : saved[0]?.request_id ?? "");
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
    setRuntime(""); setProfiles([]); setSelectedProfile(""); setVerifiedProfile(null);
    setHistory(null); refresh([]);
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
  const savedAttempts = (history?.items ?? []).filter(item => !attempts.some(local => local.request_id === item.request_id));
  const localAttempt = attempts.find(item => item.request_id === selected);
  const currentAttempt = localAttempt ?? savedAttempts.find(item => item.request_id === selected);
  const originalState = response?.attempt.request_id === currentAttempt?.request_id ? response?.attempt.state : null;
  const canObserve = currentAttempt && hyperframesIntentUnconfirmed(currentAttempt)
    && originalState !== "completed" && originalState !== "failed_known";
  const pending = attempts.some(hyperframesIntentUnconfirmed);
  const currentProfile = profiles.find(item => item.install_id === selectedProfile);
  const perform = async (kind: "render" | "read" | "observe" | "profiles" | "runtime" | "history" | "older") => {
    if (!desktopMode || busy.current || !live.current || (kind === "render" && storageError)) return;
    if (kind === "older" && !history?.next) return;
    if (kind === "render" && (!source || !hyperframesRuntimeDigest(runtime) || pending)) return;
    if (kind === "read" && !currentAttempt) return;
    if (kind === "observe" && !canObserve) return;
    if (kind === "runtime" && !currentProfile) return;
    const turn = ++epoch.current, key = scope;
    const current = () => live.current && epoch.current === turn && scopeRef.current === key;
    const beforeDispatch = () => {
      if (!current()) throw new Error("The saved source selection changed before Native dispatch.");
      if (kind === "render" && verifiedProfile && readRuntimeRetention(project).some(item =>
        item.install_id === verifiedProfile.install_id && item.kind === "rollback" && item.state !== "not_sent")) {
        setRuntime(""); setVerifiedProfile(null);
        throw new Error("The selected runtime has a retained rollback. Review its original installation before rendering.");
      }
    };
    busy.current = true; setPhase(kind); setError(null);
    if (kind === "render" || kind === "read" || kind === "observe") { setResponse(null); setDecoded(null); }
    else if (kind === "profiles" || kind === "runtime") { setRuntime(""); setVerifiedProfile(null); }
    try {
      if (kind === "history" || kind === "older") {
        const page = await readHyperframesRenderHistory(project, kind === "older" ? history?.next : null);
        if (current()) {
          setHistory(page);
          setResponse(null); setDecoded(null);
          setSelected(prior => [...attempts, ...page.items].some(item => item.request_id === prior)
            ? prior : page.items[0]?.request_id ?? attempts.at(-1)?.request_id ?? "");
        }
        return;
      }
      if (kind === "profiles") {
        const inventory = await runtimeProfileInventory(project);
        if (current()) {
          const available = inventory.profiles.filter(item => item.profile === "hyperframes" && item.state !== "rolled_back");
          setProfiles(available);
          setSelectedProfile(available[0]?.install_id ?? "");
          if (!inventory.supported) setError(inventory.alternatives);
          else if (!available.length) setError("No local HyperFrames installation is recorded for this project. Choose an offline package in Integrations, or use a separately provisioned runtime.");
        }
        return;
      }
      if (kind === "runtime" && currentProfile) {
        const installed = await readOriginalRuntimeProfile(project, currentProfile);
        if (!current()) return;
        if (installed.profile !== "hyperframes" || installed.lock_sha256 !== currentProfile.lock_sha256
          || installed.node_path !== currentProfile.node_path || installed.entry_path !== currentProfile.entry_path
          || installed.lock_path !== currentProfile.lock_path)
          throw new Error("The original runtime selection does not match the installed HyperFrames profile.");
        if (installed.state !== "installed" || !installed.content_verified)
          throw new Error("The original HyperFrames files could not be verified (" + installed.state.replaceAll("_", " ") + "). Review this installation in Integrations. The saved source is unchanged.");
        setRuntime(installed.lock_sha256);
        setVerifiedProfile(installed);
        setProfiles(rows => rows.map(item => item.install_id === installed.install_id ? installed : item));
        return;
      }
      let result: HyperframesRenderResponse;
      if (kind === "render" && source) {
        const intent = prepareHyperframesRender(project, source, runtime);
        setSelected(intent.request_id); refresh();
        result = await dispatchHyperframesRender(project, source, intent, beforeDispatch);
      } else if (kind === "observe" && currentAttempt) {
        result = await observeOriginalHyperframesJob(project, currentAttempt, beforeDispatch, localAttempt !== undefined);
      } else if (currentAttempt) {
        result = await readOriginalHyperframesRender(project, currentAttempt, localAttempt !== undefined);
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
      <button type="button" className="secondary-button" onClick={() => void perform("profiles")}>
        Read installed HyperFrames profiles</button>
      {profiles.length > 0 && <>
        <label className="production-field" htmlFor={id + "-profile"}><span>Local HyperFrames installation</span>
          <select id={id + "-profile"} value={selectedProfile} onChange={event => {
            setSelectedProfile(event.target.value); setRuntime(""); setVerifiedProfile(null);
          }}>{profiles.map(item => <option key={item.install_id} value={item.install_id}>
            {item.label + " " + item.version + " · " + item.state.replaceAll("_", " ") + " · " + item.install_id.slice(-8)}
          </option>)}</select></label>
        {currentProfile && <p className="production-help">Installed folder: {currentProfile.destination}</p>}
        <button type="button" className="secondary-button" disabled={!currentProfile}
          onClick={() => void perform("runtime")}>Use selected HyperFrames profile</button>
      </>}
      {verifiedProfile && <p role="status">Installed HyperFrames files verified for this selection.
        The selected provider connection must use this same runtime and still requires its own permission.</p>}
      <details><summary>Use a separately provisioned runtime</summary>
        <label className="production-field" htmlFor={id + "-runtime"}><span>Runtime lock SHA-256</span>
          <input id={id + "-runtime"} value={runtime} autoComplete="off" spellCheck={false} maxLength={64}
            onChange={event => { setRuntime(event.target.value); setVerifiedProfile(null); }}/></label>
        <p className="production-help">Use the digest of the owner's installed runtime-lock.json. A different runtime is rejected before rendering.</p>
      </details>
      <button type="button" className="secondary-button"
        disabled={!source || !hyperframesRuntimeDigest(runtime) || pending}
        onClick={() => void perform("render")}>Render saved HyperFrames source</button>
    </fieldset>
    {pending && <p role="status">An original request is retained and has not been confirmed.
      Read that operation before starting another render.</p>}
    <div className="portable-actions">
      <button type="button" className="secondary-button" disabled={!desktopMode || phase !== null}
        onClick={() => void perform("history")}>Find saved HyperFrames renders</button>
      {history?.next && <button type="button" className="secondary-button" disabled={phase !== null}
        onClick={() => void perform("older")}>Show older operations</button>}
    </div>
    {history && history.items.length === 0 && <p role="status">This page has no saved HyperFrames renders.</p>}
    {(attempts.length > 0 || savedAttempts.length > 0) && <fieldset disabled={!desktopMode || phase !== null}>
      <legend>Original operations</legend>
      <label className="production-field" htmlFor={id + "-attempt"}><span>Retained HyperFrames request</span>
        <select id={id + "-attempt"} value={selected} onChange={event => {
          setSelected(event.target.value); setResponse(null); setDecoded(null);
        }}>{[...attempts, ...savedAttempts].map(item => <option key={item.request_id} value={item.request_id}>
          {"r" + item.source.revision + " · " + (savedAttempts.includes(item) ? "saved operation" : item.retention.replaceAll("_", " "))
            + " · " + item.request_id.slice(-8)}
        </option>)}</select></label>
      <button type="button" className="secondary-button" disabled={!currentAttempt}
        onClick={() => void perform("read")}>Read original HyperFrames result</button>
      <p className="production-help">Reads the saved operation and original PNG files. This never restarts a render.</p>
      <button type="button" className="secondary-button"
        disabled={!canObserve}
        onClick={() => void perform("observe")}>Check original native job</button>
      <p className="production-help">For a pending operation, use its original owner connection to read the native job's status
        and finished result. This does not apply source, start another render or cancel the original job.</p>
    </fieldset>}
    {phase && <p role="status">{{
      render: "The original native operation is running…",
      read: "Reading the original retained operation…",
      observe: "Checking the original native job without restarting it…",
      profiles: "Reading original local installations…",
      runtime: "Checking the original installed HyperFrames files…",
      history: "Finding saved HyperFrames renders…",
      older: "Reading older saved operations…",
    }[phase]}</p>}
    {storageError && <p role="alert" className="production-error">{storageError} Saved render history and source export remain available.</p>}
    {error && <p role="alert" className="production-error">{error}</p>}
    {response && <div role="status">
      {response.attempt.state === "completed"
        ? <p>The provider completed the original {response.attempt.frame_count}-frame PNG sequence.
            {response.attempt.source_current ? "" : " It belongs to an earlier saved source."}</p>
        : response.attempt.state === "failed_known"
          ? <p>The original operation has a known failure. Review the runtime and source before a new explicit attempt.</p>
          : response.attempt.state === "running" || response.attempt.state === "cancelling"
            ? <p>Last native check: the original job was {response.attempt.state}. Check again to refresh its status.
                The original request remains retained.</p>
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
