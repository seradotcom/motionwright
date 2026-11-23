import { useId, useLayoutEffect, useRef, useState } from "react";
import { FileCode2 } from "lucide-react";
import type { Project } from "./types";
import { exportHyperframesSource, readHyperframesSource,
  type HyperframesSourceFormat, type HyperframesSourceSnapshot } from "./hyperframesSourceApi";

export default function HyperframesSourceWorkbench({ project, desktopMode }: { project: Project; desktopMode: boolean }) {
  const id = useId();
  const [profileId, setProfileId] = useState(project.deliverables[0]?.id ?? "");
  const [format, setFormat] = useState<HyperframesSourceFormat>("html");
  const [path, setPath] = useState("");
  const [source, setSource] = useState<{ key: string; value: HyperframesSourceSnapshot } | null>(null);
  const [phase, setPhase] = useState<"inspect" | "export" | null>(null);
  const [error, setError] = useState<string | null>(null), [status, setStatus] = useState<string | null>(null);
  const active = useRef(false), epoch = useRef(0), inFlight = useRef(false), scope = useRef("");
  const sourceKey = JSON.stringify([project, profileId]);
  const selected = project.deliverables.some(item => item.id === profileId);
  const inspected = source?.key === sourceKey ? source.value : null;
  const filename = format === "html" ? "index.html" : "semwright-hyperframes.json";
  useLayoutEffect(() => {
    active.current = true;
    return () => { active.current = false; epoch.current++; };
  }, []);
  useLayoutEffect(() => {
    scope.current = sourceKey; epoch.current++; inFlight.current = false;
    setSource(null); setPhase(null); setStatus(null); setError(null);
    if (!project.deliverables.some(item => item.id === profileId)) setProfileId(project.deliverables[0]?.id ?? "");
  }, [sourceKey, project.deliverables, profileId]);
  const run = async (kind: "inspect" | "export") => {
    if (!desktopMode || !selected || inFlight.current || !active.current || (kind === "export" && !inspected)) return;
    const turn = ++epoch.current, key = sourceKey;
    const current = () => active.current && epoch.current === turn && scope.current === key;
    const beforeDispatch = () => { if (!current()) throw new Error("The source selection changed before export."); };
    inFlight.current = true; setPhase(kind); setError(null); setStatus(null);
    if (kind === "inspect") setSource(null);
    try {
      if (kind === "inspect") {
        const value = await readHyperframesSource(project, profileId);
        if (current()) setSource({ key, value });
      } else if (inspected) {
        const receipt = await exportHyperframesSource(project, inspected, format, path, beforeDispatch);
        if (current()) setStatus("Saved " + receipt.path + " from revision r" + receipt.source.revision
          + " (" + Number(receipt.size_bytes).toLocaleString() + " bytes).");
      }
    } catch (reason) {
      if (current()) setError(reason instanceof Error ? reason.message : "The source operation could not be confirmed.");
    } finally {
      if (current()) { inFlight.current = false; setPhase(null); }
    }
  };
  return <section className="portable-project" aria-label="HyperFrames editable source">
    <header className="portable-heading"><div>
      <strong><FileCode2 size={17} aria-hidden="true"/> HyperFrames editable source</strong>
      <span>Export the saved composition as native HTML or its editable JSON source.</span>
    </div></header>
    <p className="production-help">The current source profile preserves rectangular clips, layering, opacity and exact whole-second timing at 30 fps.
      Source inspection explains any unsupported saved content before export.</p>
    {!desktopMode && <p className="production-help">Open this project in Desktop to inspect and export its saved source.</p>}
    <fieldset className="portable-operation" disabled={!desktopMode || phase !== null}>
      <legend>Saved composition</legend>
      <label className="production-field" htmlFor={id + "-profile"}><span>Delivery profile</span>
        <select id={id + "-profile"} value={profileId} onChange={event => setProfileId(event.target.value)}>
          {!project.deliverables.length && <option value="">No saved profile</option>}
          {project.deliverables.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
        </select></label>
      <button type="button" className="secondary-button" disabled={!selected}
        onClick={() => void run("inspect")}>Inspect saved HyperFrames source</button>
    </fieldset>
    {inspected && <div className="portable-plan" aria-label="Inspected HyperFrames source">
      <div><span>Saved revision</span><strong>r{inspected.project.revision}</strong></div>
      <div><span>Composition</span><strong>{inspected.width} × {inspected.height}, {inspected.frame_count} frames at 30 fps</strong></div>
      <div><span>Native format</span><strong>HyperFrames {inspected.hyperframes_version}</strong></div>
      <details><summary>Source identity</summary><p className="mono">{inspected.fingerprint}</p>
        <p>HTML: {inspected.html_bytes} bytes · JSON: {inspected.source_bytes} bytes</p></details>
    </div>}
    <fieldset className="portable-operation" disabled={!desktopMode || phase !== null || !inspected}>
      <legend>Export one native source file</legend>
      <label className="production-field" htmlFor={id + "-format"}><span>File format</span>
        <select id={id + "-format"} value={format} onChange={event => {
          const next = event.target.value as HyperframesSourceFormat;
          setFormat(next); setStatus(null); setError(null);
          setPath(previous => previous.replace(/(?:index\.html|semwright-hyperframes\.json)$/, next === "html" ? "index.html" : "semwright-hyperframes.json"));
        }}><option value="html">Native HTML (index.html)</option><option value="json">Editable JSON (semwright-hyperframes.json)</option></select></label>
      <label className="production-field" htmlFor={id + "-path"}><span>Full destination path ending in {filename}</span>
        <input id={id + "-path"} value={path} autoComplete="off" spellCheck={false}
          onChange={event => { setPath(event.target.value); setStatus(null); setError(null); }}/></label>
      <button type="button" className="secondary-button" disabled={!path.trim()}
        onClick={() => void run("export")}>Export {format === "html" ? "native HTML" : "editable JSON"}</button>
      <p className="production-help">Choose an existing directory and a new file. To save both formats, export each into the same directory.
        The saved Motionwright project remains unchanged; opening an editor or rendering is a separate action.</p>
    </fieldset>
    {phase && <p role="status" aria-live="polite">{phase === "inspect" ? "Inspecting saved source…" : "Exporting the inspected source…"}</p>}
    {error && <p role="alert" className="production-error">{error}</p>}
    {status && <p role="status" aria-live="polite">{status}</p>}
  </section>;
}
