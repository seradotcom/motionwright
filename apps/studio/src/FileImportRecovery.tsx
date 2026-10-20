import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Download, FileCheck2, RefreshCw } from "lucide-react";
import {
  FILE_IMPORT_CHANGED, FILE_IMPORT_PREFIX, fileImportDownload, readFileImports, removeFileImport,
  type FileImportEntry, type FileImportRead,
  type RetainedFileImport,
} from "./fileImport";
import type { Project } from "./types";
import "./file-import.css";

/** Observed after canonical publication; never persisted as execution authority. */
export interface FileImportConfirmation {
  retained: RetainedFileImport;
  cleanupWarning: string;
}

interface Props {
  project: Project;
  busy: boolean;
  confirmation: FileImportConfirmation | null;
  onRecover: (retained: RetainedFileImport) => Promise<boolean>;
  onClearConfirmation: () => void;
  onDismiss: () => void;
}

export default function FileImportRecovery({ project, busy, confirmation, onRecover, onClearConfirmation, onDismiss }: Props) {
  const headingId = useId();
  const [collection, setCollection] = useState(() => readFileImports(project.id));
  const [selectedKey, setSelectedKey] = useState<string | null>(() => {
    const first = collection.copies.at(0);
    return first ? first.kind === "retained" ? first.retained.key : first.key : null;
  });
  const [checking, setChecking] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const [removeKey, setRemoveKey] = useState<string | null>(null);
  const confirmButton = useRef<HTMLButtonElement>(null);
  const removeButton = useRef<HTMLButtonElement>(null);
  const checkButton = useRef<HTMLButtonElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const [focusAfter, setFocusAfter] = useState<"remove" | "heading" | null>(null);
  const refresh = useCallback(() => setCollection(readFileImports(project.id)), [project.id]);
  const entryKey = (entry: (typeof collection.copies)[number]) => entry.kind === "retained" ? entry.retained.key : entry.key;
  const selected: FileImportEntry | undefined = collection.copies.find(entry => entryKey(entry) === selectedKey) ?? collection.copies.at(0);
  const state: FileImportRead = selected ?? (collection.error || collection.limited
    ? { kind: "unreadable", error: collection.error ?? "The saved-request list could not be read completely. Existing recovery copies were kept." }
    : { kind: "empty" });

  useEffect(() => {
    refresh();
    const storageChanged = (event: StorageEvent) => {
      if (event.key === null || event.key === FILE_IMPORT_PREFIX + project.id
          || event.key.startsWith(FILE_IMPORT_PREFIX + project.id + ":")) refresh();
    };
    const copyChanged = (event: Event) => {
      if ((event as CustomEvent<unknown>).detail === project.id) refresh();
    };
    window.addEventListener("storage", storageChanged);
    window.addEventListener(FILE_IMPORT_CHANGED, copyChanged);
    return () => {
      window.removeEventListener("storage", storageChanged);
      window.removeEventListener(FILE_IMPORT_CHANGED, copyChanged);
    };
  }, [project.id, refresh]);

  useEffect(() => { if (confirmRemove !== null) confirmButton.current?.focus(); }, [confirmRemove]);

  const retained = state.kind === "retained" ? state.retained : null;
  const raw = state.kind === "invalid" ? state.raw : retained?.raw ?? null;
  const key = state.kind === "invalid" ? state.key : retained?.key ?? null;
  const foreign = retained !== null && retained.copy.preparation.generation !== project.generation;
  const pending = busy || checking;
  // A changed or different local copy never inherits another request's success.
  const confirmed = confirmation?.retained.copy.preparation.project_id === project.id
    && confirmation.retained.copy.preparation.generation === project.generation ? confirmation : null;
  const confirmsSelected = confirmed !== null && key === confirmed.retained.key && raw === confirmed.retained.raw;

  useEffect(() => {
    if (key !== null && key !== selectedKey) setSelectedKey(key);
    if (confirmRemove !== null && (key !== removeKey || raw !== confirmRemove)) {
      setConfirmRemove(null); setRemoveKey(null); setFocusAfter("heading");
      setNotice("The saved request changed. Review the selected copy before removing it.");
    }
  }, [key, raw, selectedKey, removeKey, confirmRemove]);

  useEffect(() => {
    if (focusAfter === "remove") (removeButton.current && !removeButton.current.disabled ? removeButton.current : heading.current)?.focus();
    if (focusAfter === "heading") heading.current?.focus();
    if (focusAfter !== null) setFocusAfter(null);
  }, [focusAfter]);

  const recover = async () => {
    if (!retained || pending || foreign) return;
    setChecking(true); setError(null); setNotice(null); setConfirmRemove(null); setRemoveKey(null);
    try {
      const found = await onRecover(retained);
      setNotice(found
        ? `The original import of ${retained.copy.preparation.intent.name} is confirmed. The latest saved project is open.`
        : "The original import was not found. This check did not send an import. Keep the copy while you check the project.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally {
      // A completed check may remove its button. Keep the user's focus if they
      // deliberately moved to another control while the read was in flight.
      if (document.activeElement === document.body || document.activeElement === checkButton.current) setFocusAfter("heading");
      setChecking(false); refresh();
    }
  };

  const download = () => {
    if (raw === null) return;
    try {
      const content = fileImportDownload(project.id, raw);
      const url = URL.createObjectURL(new Blob([content], { type: "application/json" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `motionwright-import-recovery-${project.id}.json`;
      anchor.click();
      // Leave enough time for the browser to start the download before revoking.
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };

  const remove = () => {
    if (confirmRemove === null || removeKey === null || pending) return;
    if (removeKey !== key || confirmRemove !== raw) {
      setError("The selected recovery copy changed. Review it before removing it.");
      setConfirmRemove(null); setRemoveKey(null); setFocusAfter("heading"); return;
    }
    setError(null);
    try {
      removeFileImport(project.id, removeKey, confirmRemove);
      onClearConfirmation();
      setConfirmRemove(null); setRemoveKey(null);
      setNotice("The local recovery copy was removed. Any committed import remains in the project.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { refresh(); setFocusAfter("heading"); }
  };

  if (state.kind === "empty" && !notice && !error && !confirmed) return null;
  const title = state.kind === "empty" ? "Import recovery"
    : confirmsSelected ? "Import confirmed; recovery copy kept"
    : state.kind === "retained" ? "An import is awaiting confirmation" : "A saved import needs attention";

  return (
    <section className="file-import-recovery" aria-labelledby={headingId} aria-busy={checking} onKeyDown={event => {
      if (event.key === "Escape" && confirmRemove !== null) {
        // Escape cancels only the local deletion question. Neither the retained
        // bytes nor the original import receipt is changed by this shortcut.
        event.preventDefault(); event.stopPropagation();
        setConfirmRemove(null); setRemoveKey(null); setFocusAfter("remove");
        return;
      }
      if (event.code === "Space" && !event.ctrlKey && !event.metaKey && !event.altKey
          && event.target instanceof HTMLElement) event.stopPropagation();
    }}>
      <div className="file-import-copy">
        <h2 ref={heading} tabIndex={-1} id={headingId}><FileCheck2 size={16} aria-hidden="true" />{title}</h2>
        {collection.copies.length > 1 && <label className="file-import-selector">
          <span>{collection.copies.length} retained requests</span>
          <select aria-label="Retained import request" value={key ?? ""} disabled={pending} onChange={event => {
            setSelectedKey(event.target.value); setConfirmRemove(null); setRemoveKey(null); setError(null); setNotice(null);
          }}>
            {collection.copies.map((entry, index) => <option key={entryKey(entry)} value={entryKey(entry)}>
              {index + 1}. {entry.kind === "retained" ? entry.retained.copy.preparation.intent.name : "Unreadable recovery copy"}
            </option>)}
          </select>
        </label>}
        {selected && (collection.error || collection.limited) && <p role="alert">{collection.error ?? "Some saved requests are outside this bounded page."} Existing copies were kept; new imports are paused.</p>}
        {retained && <>
          <p><strong>{retained.copy.preparation.intent.name}</strong>{" · "}
            {retained.copy.preparation.intent.kind === "voice" ? "Voice take import" : "Project media"}</p>
          <p>{foreign
            ? "This request belongs to an earlier restored version of this project. Its recovery copy has been kept."
            : confirmsSelected ? "The import is saved. You can download or review the local copy before removing it."
            : "Check the original result before importing again. Your later saved edits will be loaded with it."}</p>
          <details>
            <summary>Original import details</summary>
            <dl>
              <dt>File</dt><dd>{retained.copy.input.path}</dd>
              <dt>Prepared at</dt><dd>Revision {retained.copy.preparation.revision}</dd>
              {retained.copy.preparation.intent.kind === "voice" && <><dt>Take label</dt><dd>{retained.copy.preparation.intent.label}</dd></>}
              <dt>Request</dt><dd>{retained.copy.request_id}</dd>
            </dl>
          </details>
        </>}
        {(state.kind === "invalid" || state.kind === "unreadable") && <p>{state.error}</p>}
        {state.kind === "invalid" && <p>Download the saved copy before removing it. A new import is paused for this project.</p>}
        {confirmed ? <div className="file-import-notice" role="status">
          <p>The original import of {confirmed.retained.copy.preparation.intent.name} is confirmed at revision r{confirmed.retained.copy.preparation.revision + 1}. The latest saved project is open.</p>
          {state.kind !== "empty" && <p>The local recovery copy could not be cleared: {confirmed.cleanupWarning}</p>}
          {!confirmsSelected && state.kind !== "empty" && <p>This confirmation does not apply to the selected copy. It belongs to original request {confirmed.retained.copy.request_id}; review the current copy separately.</p>}
        </div> : notice && <p className="file-import-notice" role="status">{notice}</p>}
        {error && <p className="file-import-error" role="alert">{error}</p>}
        {confirmRemove !== null && <div className="file-import-remove">
          <p>Removing this copy does not undo an import. Check the project before importing the file again.</p>
          <button ref={confirmButton} className="button compact" type="button" disabled={pending} onClick={remove}>Remove local recovery copy</button>
          <button className="button compact" type="button" onClick={() => { setConfirmRemove(null); setRemoveKey(null); setFocusAfter("remove"); }}>Keep copy</button>
        </div>}
      </div>
      <div className="file-import-actions">
        {retained && <button ref={checkButton} className="button button-primary compact" type="button" disabled={pending || foreign} onClick={() => void recover()}>
          <RefreshCw size={14} aria-hidden="true" className={checking ? "spin" : undefined} />
          {checking ? "Checking original import…" : "Check original import"}
        </button>}
        {raw !== null && <button className="button compact" type="button" onClick={download}><Download size={14} aria-hidden="true" />Download recovery copy</button>}
        {raw !== null && key !== null && confirmRemove === null && <button ref={removeButton} className="button compact" type="button" disabled={pending}
          onClick={() => { setSelectedKey(key); setConfirmRemove(raw); setRemoveKey(key); }}>Remove copy…</button>}
        {state.kind === "unreadable" && <button className="button compact" type="button" disabled={pending} onClick={refresh}>Read saved request again</button>}
        {state.kind === "empty" && <button className="button compact" type="button" onClick={() => { setNotice(null); setError(null); onDismiss(); }}>Dismiss</button>}
      </div>
    </section>
  );
}
