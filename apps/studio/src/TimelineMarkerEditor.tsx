import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { observeMarkerReceipt } from "./api";
import { formatBeatTime } from "./sceneBeatTiming";
import { buildMarkerEdit, finishMarkerAttempt, readMarkerAttempts, retainMarkerAttempt, sameMarker } from "./timelineMarkerDraft";
import type { MarkerAttempt } from "./timelineMarkerDraft";
import type { TimecodeMode } from "./timecode";
import type { Marker, MarkerChange, Project, RationalTime } from "./types";
import { timelineSelectionScope } from "./timelineSelection";

type Pane = { kind: "edit"; marker: Marker; revision: number } | { kind: "recovery" };
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

export default function TimelineMarkerEditor({ marker, project, rate, mode, commit, refresh }: {
  marker: Marker | undefined;
  project: Project;
  rate: RationalTime;
  mode: TimecodeMode;
  commit: (change: MarkerChange, requestId: string) => Promise<Project>;
  refresh: (project: Project) => Promise<Project>;
}) {
  const titleId = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const running = useRef(false);
  const [pane, setPane] = useState<Pane | null>(null);
  const [attempts, setAttempts] = useState<MarkerAttempt[]>([]);
  const [storageError, setStorageError] = useState("");
  const [name, setName] = useState("");
  const [position, setPosition] = useState("");
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [notFound, setNotFound] = useState<string>();
  const scope = timelineSelectionScope(project);

  const loadAttempts = () => {
    try {
      const saved = readMarkerAttempts(window.localStorage, project);
      setAttempts(saved);
      setStorageError("");
      return saved;
    } catch (error) {
      setStorageError(errorText(error));
      return null;
    }
  };
  useEffect(() => {
    loadAttempts();
    const onStorage = (event: StorageEvent) => {
      if (!event.key || (event.key.startsWith("motionwright.marker-attempt.v1:pending:") && event.key.includes(scope))) loadAttempts();
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [scope]);
  useEffect(() => {
    if (pane && !dialog.current?.open) dialog.current?.showModal();
  }, [pane]);

  const scopedLocks = pane?.kind === "edit"
    ? project.locks.filter(lock => lock.resource === "marker:" + pane.marker.id || lock.resource === "project:" + project.id)
    : [];
  const labelLocked = scopedLocks.some(lock => lock.kind === "content");
  const timeLocked = scopedLocks.some(lock => lock.kind === "timing");
  const removeLocked = labelLocked || timeLocked
    || (pane?.kind === "edit" && scopedLocks.some(lock => lock.resource === "marker:" + pane.marker.id));
  const stale = pane?.kind === "edit" && project.revision !== pane.revision;

  const openEditor = (selected: Marker) => {
    const saved = loadAttempts();
    if (!saved) return;
    setNotice("");
    setNotFound(undefined);
    if (saved.some(attempt => attempt.change.expected.id === selected.id)) {
      setPane({ kind: "recovery" });
      return;
    }
    setName(selected.label);
    setPosition(formatBeatTime(selected.at));
    setConfirmRemove(false);
    setPane({ kind: "edit", marker: structuredClone(selected), revision: project.revision });
  };

  const send = async (change: MarkerChange) => {
    if (running.current || pane?.kind !== "edit") return;
    const saved = loadAttempts();
    if (!saved) return;
    const current = project.markers.find(item => item.id === change.expected.id);
    if (stale || !current || !sameMarker(current, change.expected)) {
      setNotice("The saved project changed. Close this draft and inspect the current marker before editing.");
      return;
    }
    if (saved.some(item => item.change.expected.id === current.id) || saved.length >= 32) {
      setNotice("Inspect the retained marker attempt first.");
      return;
    }
    const attempt = { requestId: crypto.randomUUID(), revision: pane.revision, change };
    try {
      retainMarkerAttempt(window.localStorage, project, attempt);
    } catch (error) {
      setNotice(errorText(error) + " No marker change was sent.");
      return;
    }

    running.current = true;
    setBusy(true);
    setNotice("");
    try {
      const next = await commit(change, attempt.requestId);
      if (timelineSelectionScope(next) !== scope || next.revision !== attempt.revision + 1)
        throw new Error("The original reply did not identify the expected project revision.");
      const storedMarker = next.markers.find(item => item.id === change.expected.id);
      if (change.type === "remove_marker" ? storedMarker !== undefined
        : !storedMarker || !sameMarker(storedMarker, { id: change.expected.id, label: change.label, at: change.at })) {
        throw new Error("The original reply did not confirm the marker change.");
      }
      finishMarkerAttempt(window.localStorage, project, attempt);
      dialog.current?.close();
    } catch (error) {
      setPane({ kind: "recovery" });
      setNotice(errorText(error) + " Inspect the original marker request before editing it again.");
    } finally {
      running.current = false;
      setBusy(false);
      loadAttempts();
    }
  };

  const inspect = async (attempt: MarkerAttempt, keepOriginal = false) => {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setNotice("");
    setNotFound(undefined);
    try {
      const confirmed = await observeMarkerReceipt(project, attempt.revision, attempt.requestId, attempt.change);
      if (!confirmed && !keepOriginal) {
        setNotFound(attempt.requestId);
        setNotice("The original request has no verified receipt yet. Its result remains unconfirmed.");
        return;
      }
      const visible = await refresh(project);
      if (timelineSelectionScope(visible) !== scope || visible.revision < attempt.revision + (confirmed ? 1 : 0))
        throw new Error("Open the saved project and branch for this original marker request.");
      finishMarkerAttempt(window.localStorage, project, attempt, !confirmed);
      setNotice(confirmed ? "The original receipt confirmed the marker edit. The saved project was refreshed."
        : "The original request remains retained. You can now review the saved marker and start a separate edit.");
    } catch (error) {
      setNotice(errorText(error));
    } finally {
      running.current = false;
      setBusy(false);
      loadAttempts();
    }
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pane?.kind !== "edit") return;
    try {
      const proposed = buildMarkerEdit(pane.marker, name, position, rate, mode);
      void send(proposed);
    } catch (error) {
      setNotice(errorText(error));
    }
  };

  return <>
    {marker && <button type="button" className="button compact" disabled={!!storageError} onClick={() => openEditor(marker)}>
      Edit marker
    </button>}
    {attempts.length > 0 && <button type="button" className="button compact" onClick={() => {
      setNotice(""); setNotFound(undefined); setPane({ kind: "recovery" });
    }}>Marker recovery</button>}
    {storageError && <span role="status" className="timeline-view-warning">{storageError}</span>}
    <dialog ref={dialog} className="timeline-marker-dialog" aria-labelledby={titleId}
      onCancel={event => { if (running.current) event.preventDefault(); }}
      onClose={() => { setPane(null); setNotice(""); }}>
      <h2 id={titleId}>{pane?.kind === "recovery" ? "Marker recovery" : "Edit marker"}</h2>
      {pane?.kind === "edit" && <form onSubmit={submit}>
        <label>Marker name<input value={name} maxLength={160} disabled={busy || labelLocked}
          onChange={event => { setName(event.target.value); setNotice(""); }} /></label>
        <label>Marker position<input value={position} maxLength={96} disabled={busy || timeLocked}
          onChange={event => { setPosition(event.target.value); setNotice(""); }} /></label>
        <p>Enter seconds, a fraction such as 1001/30000, or a timecode.</p>
        {stale ? <p role="status">The saved revision changed. Close this draft and reopen the marker.</p> : null}
        {labelLocked || timeLocked ? <p role="status">Locked: {labelLocked && timeLocked ? "name and position" : labelLocked ? "name" : "position"}.</p> : null}
        {confirmRemove && <p>Remove this marker from the current branch?</p>}
        <div className="sheet-actions">
          <button className="button button-primary compact" type="submit" disabled={busy || stale || confirmRemove || (labelLocked && timeLocked)}>Save marker</button>
          {confirmRemove
            ? <button className="button compact" type="button" disabled={busy || stale || removeLocked}
              onClick={() => void send({ type: "remove_marker", expected: pane.marker })}>Confirm removal</button>
            : <button className="button compact" type="button" disabled={busy || stale || removeLocked}
              onClick={() => setConfirmRemove(true)}>Remove marker</button>}
          {confirmRemove && <button className="button compact" type="button" disabled={busy}
            onClick={() => setConfirmRemove(false)}>Keep marker</button>}
        </div>
      </form>}
      {pane?.kind === "recovery" && <div className="marker-recovery-list">
        {attempts.map(attempt => <section key={attempt.requestId}>
          <strong>{attempt.change.expected.label || "Unnamed marker"}</strong>
          <p>Original position {formatBeatTime(attempt.change.expected.at)} s · revision {attempt.revision}</p>
          {attempt.change.type === "remove_marker" ? <p>Requested removal.</p>
            : <p>Requested {attempt.change.label || "unnamed marker"} at {formatBeatTime(attempt.change.at)} s.</p>}
          <button className="button compact" type="button" disabled={busy}
            onClick={() => void inspect(attempt)}>Inspect original request</button>
          {notFound === attempt.requestId && <button className="button compact" type="button" disabled={busy}
            onClick={() => void inspect(attempt, true)}>Keep original and start a separate edit</button>}
        </section>)}
        {attempts.length === 0 && <p>No pending marker requests.</p>}
      </div>}
      {notice && <p role="status">{notice}</p>}
      <button className="button compact" type="button" disabled={busy} onClick={() => dialog.current?.close()}>Close</button>
    </dialog>
  </>;
}
