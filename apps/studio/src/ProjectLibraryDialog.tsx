import { checkedBundlePlan, PortableImportSourceChanged, type BundleImportCopy } from "./projectBundleImport";
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import {
  appendProjectLibraryItems,
  DEFAULT_PROJECT_PAGE_SIZE,
  type ListProjectsRequest,
  type ProjectLibraryCursor,
  type ProjectLibraryPage,
  type ProjectLibrarySummary,
  type ProjectCreationCopy,
  type ProjectTemplate,
} from "./projectLibrary";
import type { Project, PortableBundlePlan } from "./types";
import type { PortableProjectSourceKind } from "./api";
import "./project-library.css";

export interface ProjectLibraryDialogProps {
  open: boolean;
  currentProjectId: string | null;
  onClose: () => void;
  onList: (request: ListProjectsRequest) => Promise<ProjectLibraryPage>;
  /** Resolve only after the existing App coordinator has admitted this identity. */
  onOpen: (projectId: string) => Promise<Pick<Project, "id">>;
  onBrowse?: (projectId: string) => Promise<Pick<Project, "id">>;
  /** Omit until creation is supported by the reviewed desktop authority. */
  onCreate?: (title: string, template?: ProjectTemplate) => Promise<Project>;
  creationCopies?: ProjectCreationCopy[];
  creationLimited?: boolean;
  creationError?: string | null;
  onRecoverCreation?: (copy: ProjectCreationCopy) => Promise<Project | null>;
  onDiscardCreation?: (copy: ProjectCreationCopy) => void;
  onRefreshCreations?: () => void;
  notice?: string | null;
  onChooseBundleSource?: (kind: PortableProjectSourceKind) => Promise<string | null>;
  onInspectBundle?: (path: string) => Promise<PortableBundlePlan>;
  onImportBundle?: (path: string, plan: PortableBundlePlan) => Promise<Pick<Project, "id">>;
  bundleCopies?: BundleImportCopy[];
  bundleLimited?: boolean;
  bundleError?: string | null;
  onRecoverBundle?: (copy: BundleImportCopy) => Promise<Pick<Project, "id"> | null>;
  onDiscardBundle?: (copy: BundleImportCopy) => void;
  onRefreshBundles?: () => void;
  focusRetainedImports?: boolean;
}

type Opening = { kind: "open"; projectId: string; title: string } | { kind: "create" } | { kind: "recover"; title: string } | { kind: "bundle"; title: string };
type PageFailure = { message: string; cursor: ProjectLibraryCursor | null; append: boolean };

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

function updatedLabel(value: string): string {
  const at = Date.parse(value);
  return Number.isFinite(at) ? new Date(at).toLocaleString() : value;
}

/**
 * Presentation over injected real list/open commands. Keep this component
 * mounted when closing it to retain an unsubmitted new-project title.
 * It never publishes a Project or writes a remembered selection. The parent
 * owns canonical admission, effect grants, and the outcome of an operation
 * whose response arrives after the parent has unmounted this dialog.
 */
export function ProjectLibraryDialog({
  open, currentProjectId, onClose, onList, onOpen, onBrowse, onCreate, notice,
  creationCopies = [], creationLimited = false, creationError = null,
  onRecoverCreation, onDiscardCreation, onRefreshCreations,
  onChooseBundleSource, onInspectBundle, onImportBundle, bundleCopies = [], bundleLimited = false, bundleError = null,
  onRecoverBundle, onDiscardBundle, onRefreshBundles, focusRetainedImports,
}: ProjectLibraryDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const createId = useId();
  const createHintId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const retainedImportsRef = useRef<HTMLElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const visibleRef = useRef(open);
  const callbacks = useRef({ onClose, onList, onOpen, onBrowse, onCreate });
  visibleRef.current = open;
  callbacks.current = { onClose, onList, onOpen, onBrowse, onCreate };

  const session = useRef(0);
  const pageRequest = useRef(0);
  const operationRequest = useRef(0);
  const operationRef = useRef<Opening | null>(null);
  const [items, setItems] = useState<ProjectLibrarySummary[]>([]);
  const [nextCursor, setNextCursor] = useState<ProjectLibraryCursor | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState<"refresh" | "more" | null>(null);
  const [pageFailure, setPageFailure] = useState<PageFailure | null>(null);
  const [opening, setOpening] = useState<Opening | null>(null);
  const [operationError, setOperationError] = useState<string | null>(null);
  const [closeNotice, setCloseNotice] = useState<string | null>(null);
  const [newTitle, setNewTitle] = useState("");
  const [bundlePath, setBundlePath] = useState("");
  const [inspectedBundle, setInspectedBundle] = useState<{ path: string; plan: PortableBundlePlan } | null>(null);
  const unresolvedBundle = bundleLimited || Boolean(bundleError) || bundleCopies.length > 0;
  const bundlePlan = inspectedBundle?.path === bundlePath.trim() ? inspectedBundle.plan : null;
  const titleInputVersion = useRef(0);
  const busy = opening !== null;
  const unresolvedCreation = creationLimited || Boolean(creationError)
    || creationCopies.some(copy => !copy.value || copy.value.phase === "unconfirmed");

  const readPage = useCallback(async (after: ProjectLibraryCursor | null, append: boolean) => {
    const startedSession = session.current;
    const request = ++pageRequest.current;
    setLoading(append ? "more" : "refresh");
    setPageFailure(null);
    const current = () => visibleRef.current && session.current === startedSession
      && pageRequest.current === request;
    try {
      const page = await callbacks.current.onList({ cursor: after, limit: DEFAULT_PROJECT_PAGE_SIZE });
      if (!current()) return;
      setItems(previous => append ? appendProjectLibraryItems(previous, page.items) : page.items);
      setNextCursor(page.next_cursor);
      setLoaded(true);
    } catch (reason) {
      if (current()) setPageFailure({ message: errorMessage(reason), cursor: after, append });
    } finally {
      if (current()) setLoading(null);
    }
  }, []);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    session.current++;
    // Parent-forced closure invalidates only this dialog's presentation. It
    // must not be described as cancellation of a submitted store operation.
    operationRef.current = null;
    setOpening(null);
    setCloseNotice(null);
    if (!open) {
      if (dialog.open) dialog.close();
      return;
    }
    returnFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement : null;
    setOperationError(null);
    if (!dialog.open) dialog.showModal();
    headingRef.current?.focus();
    void readPage(null, false);
    return () => {
      session.current++;
      pageRequest.current++;
      operationRequest.current++;
      if (dialog.open) dialog.close();
      const previous = returnFocusRef.current;
      if (previous?.isConnected && !dialog.contains(previous)) previous.focus();
    };
  }, [open, readPage]);

  useEffect(() => {
    if (open && focusRetainedImports) retainedImportsRef.current?.focus();
  }, [open, focusRetainedImports]);

  const requestClose = useCallback(() => {
    if (operationRef.current) {
      setCloseNotice("Wait for the project operation to finish. Its result has not been confirmed yet.");
      return;
    }
    callbacks.current.onClose();
  }, []);

  async function openSaved(item: ProjectLibrarySummary, mode: "auto" | "pages" = "auto") {
    const open = mode === "pages" ? callbacks.current.onBrowse : callbacks.current.onOpen;
    if (!open || operationRef.current) return;
    const startedSession = session.current;
    const request = ++operationRequest.current;
    const operation: Opening = { kind: "open", projectId: item.id, title: item.title };
    operationRef.current = operation;
    setOpening(operation);
    setOperationError(null);
    setCloseNotice(null);
    const current = () => visibleRef.current && session.current === startedSession
      && operationRequest.current === request;
    let accepted = false;
    try {
      const project = await open(item.id);
      if (!current()) return;
      if (project.id.toLowerCase() !== item.id.toLowerCase()) {
        throw new Error("The accepted project does not match the requested identity. Refresh the library to reconcile the result.");
      }
      accepted = true;
    } catch (reason) {
      if (current()) setOperationError(`Couldn’t open “${item.title}”. ${errorMessage(reason)}`);
    } finally {
      if (current()) {
        operationRef.current = null;
        setOpening(null);
        setCloseNotice(null);
      }
    }
    // Closing is not part of service error handling: a parent callback error
    // must not be reported as a failed project open after canonical admission.
    if (accepted && current()) callbacks.current.onClose();
  }

  async function createProject(event?: FormEvent<HTMLFormElement>, example = false) {
    event?.preventDefault();
    const create = callbacks.current.onCreate;
    if (!create || operationRef.current) return;
    const submittedTitle = example && !newTitle.trim() ? "Motion study" : newTitle;
    const submittedInputVersion = titleInputVersion.current;
    if (!submittedTitle.trim() || new TextEncoder().encode(submittedTitle).length > 200) {
      setOperationError(!submittedTitle.trim()
        ? "Enter a project title."
        : "This project title is too long. Try a shorter title.");
      return;
    }
    const startedSession = session.current;
    const request = ++operationRequest.current;
    operationRef.current = { kind: "create" };
    setOpening({ kind: "create" });
    setOperationError(null);
    setCloseNotice(null);
    const current = () => visibleRef.current && session.current === startedSession
      && operationRequest.current === request;
    let accepted = false;
    try {
      const project = await create(submittedTitle, example ? "motion-study-v1" : undefined);
      if (!current()) return;
      if (!project.id) {
        throw new Error("The accepted project has no identity. Recover the original request; no automatic retry was made.");
      }
      accepted = true;
      if (titleInputVersion.current === submittedInputVersion) setNewTitle(value => value === submittedTitle ? "" : value);
    } catch (reason) {
      if (current()) setOperationError(`Couldn’t create the project. ${errorMessage(reason)}`);
    } finally {
      if (current()) {
        operationRef.current = null;
        setOpening(null);
        setCloseNotice(null);
      }
    }
    if (accepted && current()) callbacks.current.onClose();
  }

  async function recoverCreation(copy: ProjectCreationCopy) {
    if (!onRecoverCreation || !copy.value || operationRef.current) return;
    const startedSession = session.current;
    const request = ++operationRequest.current;
    operationRef.current = { kind: "recover", title: copy.value.intent.title };
    setOpening(operationRef.current); setOperationError(null); setCloseNotice(null);
    const current = () => visibleRef.current && session.current === startedSession && operationRequest.current === request;
    let accepted = false;
    try { accepted = (await onRecoverCreation(copy)) !== null; }
    catch (reason) { if (current()) setOperationError("The original creation could not be reconciled. " + errorMessage(reason)); }
    finally { if (current()) { operationRef.current = null; setOpening(null); } }
    if (accepted && current()) callbacks.current.onClose();
  }

  async function bundleOperation(kind: "inspect" | "import" | "recover" | PortableProjectSourceKind, copy?: BundleImportCopy) {
    if (operationRef.current) return;
    const path = bundlePath.trim();
    const choosing = kind === "folder" || kind === "archive";
    if (choosing && !onChooseBundleSource) return;
    if (kind === "inspect" && (!path || !onInspectBundle)) return;
    if (kind === "import" && (!bundlePlan || !onImportBundle || unresolvedBundle)) return;
    if (kind === "recover" && (!copy?.value || !onRecoverBundle)) return;
    const startedSession = session.current, request = ++operationRequest.current;
    const titles = { folder: "Choosing a project folder", archive: "Choosing a project archive",
      inspect: "Inspecting the portable project", import: "Importing the portable project",
      recover: "Checking the original import" };
    const operation: Opening = { kind: "bundle", title: titles[kind] };
    operationRef.current = operation; setOpening(operation); setOperationError(null); setCloseNotice(null);
    const current = () => visibleRef.current && session.current === startedSession && operationRequest.current === request;
    let accepted = false;
    try {
      if (choosing) {
        const selected = await onChooseBundleSource!(kind);
        if (current() && selected !== null && selected !== path) {
          setBundlePath(selected);
          setInspectedBundle(null);
        }
      } else if (kind === "inspect") {
        setInspectedBundle(null);
        const plan = checkedBundlePlan(await onInspectBundle!(path));
        if (current()) setInspectedBundle({ path, plan });
      } else {
        const opened = kind === "import" ? await onImportBundle!(path, bundlePlan!)
          : await onRecoverBundle!(copy!);
        if (!current()) return;
        const expected = kind === "import" ? bundlePlan!.project_id : copy!.value!.plan.project_id;
        if (opened && opened.id !== expected) throw new Error("The opened project does not match the portable import.");
        accepted = opened !== null;
        if (accepted) setInspectedBundle(null);
      }
    } catch (reason) {
      if (current()) {
        if (reason instanceof PortableImportSourceChanged) setInspectedBundle(null);
        setOperationError(errorMessage(reason));
      }
    }
    finally { if (current()) { operationRef.current = null; setOpening(null); setCloseNotice(null); } }
    if (accepted && current()) callbacks.current.onClose();
  }
  function removeBundleCopy(copy: BundleImportCopy) {
    if (!onDiscardBundle || operationRef.current) return;
    if (!window.confirm("Remove only this local import copy? This does not undo an import or prove that repeating it is safe. Download the copy first if you need to keep it.")) return;
    try { onDiscardBundle(copy); } catch (reason) { setOperationError(errorMessage(reason)); }
  }

  function discardCreation(copy: ProjectCreationCopy) {
    if (!onDiscardCreation || operationRef.current) return;
    try { onDiscardCreation(copy); setOperationError(null); }
    catch (reason) { setOperationError(errorMessage(reason)); }
  }

  return (
    <dialog
      ref={dialogRef}
      className="project-library-dialog"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      onCancel={event => { event.preventDefault(); requestClose(); }}
      onKeyDown={event => {
        if (event.key !== "Tab") return;
        const stops = [...event.currentTarget.querySelectorAll<HTMLElement>(
          "button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex]",
        )].filter(element => element.tabIndex >= 0 && element.getClientRects().length > 0);
        const index = stops.indexOf(document.activeElement as HTMLElement);
        if (stops.length === 0) {
          event.preventDefault();
          headingRef.current?.focus();
        } else if (event.shiftKey && index <= 0) {
          event.preventDefault();
          stops.at(-1)?.focus();
        } else if (!event.shiftKey && (index < 0 || index === stops.length - 1)) {
          event.preventDefault();
          stops[0].focus();
        }
      }}
    >
      <header className="project-library-header">
        <div>
          <span className="project-library-eyebrow">Local projects</span>
          <h2 ref={headingRef} id={titleId} tabIndex={-1}>Project library</h2>
        </div>
        <button type="button" className="project-library-close" onClick={requestClose}
          disabled={busy} aria-label="Close project library">×</button>
      </header>
      <p id={descriptionId} className="project-library-description">
        Choose a saved project to continue editing. Unreadable projects remain in the library for recovery.
      </p>
      {notice && <p className="project-library-notice" role="status">{notice}</p>}
      <div className="project-library-content">
        <div className="project-library-list-heading">
          <h3>Saved projects</h3>
          <button type="button" className="project-library-button" onClick={() => void readPage(null, false)}
            disabled={busy || loading !== null}>Refresh</button>
        </div>
        <p className="project-library-list-status" role="status" aria-live="polite">
          {loading === "refresh" ? "Refreshing saved projects…"
            : loading === "more" ? "Loading more projects…"
              : loaded ? `${items.length} ${items.length === 1 ? "project" : "projects"} shown${nextCursor ? "; more available" : ""}.`
                : pageFailure ? "The project index could not be loaded." : "Loading saved projects…"}
        </p>
        {pageFailure && (
          <section className="project-library-error" role="alert">
            <strong>Couldn’t load {pageFailure.append ? "the next page" : "saved projects"}</strong>
            <p>{pageFailure.message}</p>
            <button type="button" className="project-library-button"
              disabled={busy || loading !== null}
              onClick={() => void readPage(pageFailure.cursor, pageFailure.append)}>Retry loading projects</button>
          </section>
        )}
        {loaded && items.length === 0 && !loading && !pageFailure && (
          <div className="project-library-empty">
            <strong>No saved projects</strong>
            <p>{onCreate ? "Create a project below to begin." : "Saved projects will appear here. Refresh to check again."}</p>
          </div>
        )}
        {items.length > 0 && (
          <ul className="project-library-list" aria-label="Saved projects" aria-busy={loading !== null}>
            {items.map(item => {
              const selected = item.id === currentProjectId?.toLowerCase();
              const pending = opening?.kind === "open" && opening.projectId === item.id;
              return (
                <li key={item.id} className={"project-library-item" + (selected ? " is-current" : "")}>
                  <div className="project-library-item-copy">
                    <div className="project-library-item-title">
                      <strong>{item.title || "Untitled stored project"}</strong>
                      {selected && <span className="project-library-current">Current</span>}
                    </div>
                    <p>Revision {item.revision} <span aria-hidden="true">·</span> <time dateTime={item.updated_at}>{updatedLabel(item.updated_at)}</time></p>
                    <span className="project-library-id">{item.id}</span>
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
                  <button type="button" className="project-library-button project-library-open"
                    disabled={busy} aria-label={`${selected ? "Reopen" : "Open"} ${item.title}`}
                    onClick={() => void openSaved(item)}>
                    {pending ? "Opening…" : selected ? "Reopen" : "Open"}
                  </button>
                    {onBrowse && <button type="button" className="project-library-button"
                      disabled={busy} aria-label={"Browse pages of " + item.title}
                      onClick={() => void openSaved(item, "pages")}>Browse pages</button>}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {nextCursor && !pageFailure && (
          <button type="button" className="project-library-button project-library-more"
            disabled={busy || loading !== null} onClick={() => void readPage(nextCursor, true)}>
            {loading === "more" ? "Loading…" : "Load more projects"}
          </button>
        )}
        {onInspectBundle && onImportBundle && <section className="project-library-create" aria-label="Import portable project">
          <h3>Import a portable project</h3>
          <p>Restore a Motionwright project folder or migrate a Sequencewright archive (.json). Motionwright checks the original source and assets before import.</p>
          <label htmlFor={createId + "-bundle"}>Project folder or archive path</label>
          <input id={createId + "-bundle"} value={bundlePath} autoComplete="off"
            disabled={busy} onChange={event => { setBundlePath(event.target.value); setInspectedBundle(null); }} />
          {onChooseBundleSource && <div className="portable-actions">
            <button type="button" className="project-library-button" disabled={busy}
              onClick={() => void bundleOperation("folder")}>Choose project folder</button>
            <button type="button" className="project-library-button" disabled={busy}
              onClick={() => void bundleOperation("archive")}>Choose project archive</button>
          </div>}
          <button type="button" className="project-library-button" disabled={busy || !bundlePath.trim() || unresolvedBundle}
            onClick={() => void bundleOperation("inspect")}>Inspect portable project</button>
          {bundlePlan && <div role="status" aria-label="Portable project inspection">
            <strong>{bundlePlan.title}</strong>
            <p>{bundlePlan.migration_notes ? "New editable snapshot" : "Saved revision " + bundlePlan.revision}
              {" · "}{bundlePlan.event_count} changes · {bundlePlan.blob_count} original assets · {bundlePlan.total_blob_bytes} bytes</p>
            {bundlePlan.migration_notes ? <>
              <p>Migration creates a new local project and never overwrites an existing project. Review what stays in the archive before importing.</p>
              <ul>{bundlePlan.migration_notes.map((note) => <li key={note}>{note}</li>)}</ul>
            </> : <p>Import preserves the project identity and creates a fresh execution generation. An existing project is never overwritten.</p>}
            <button type="button" className="project-library-button project-library-primary" disabled={busy || unresolvedBundle}
              onClick={() => void bundleOperation("import")}>Import inspected project</button>
          </div>}
          {unresolvedBundle && <p role="status">Check the retained import below before importing another project. Existing saved projects remain available.</p>}
        </section>}
        {(bundleCopies.length > 0 || bundleError || bundleLimited) && <section ref={retainedImportsRef} tabIndex={-1} className="project-library-creations" aria-label="Retained portable imports">
          <div className="project-library-list-heading"><h3>Retained portable imports</h3>
            {onRefreshBundles && <button type="button" className="project-library-button" disabled={busy} onClick={onRefreshBundles}>Refresh portable imports</button>}
          </div>
          <p>Check the original saved result after a missing reply. Recovery reads the existing commit and opens current saved edits.</p>
          {bundleError && <p className="project-library-error" role="alert">{bundleError}</p>}
          {bundleLimited && <p role="status">The retained import list exceeded its reading limit. Its original copies were kept.</p>}
          {bundleCopies.map((copy, index) => <article key={copy.key} aria-label={"Retained portable import " + (index + 1)}>
            <h4>{copy.value?.plan.title ?? "Unreadable portable import"}</h4>
            <p>{copy.value ? "Result unconfirmed · " + copy.value.path : "The original local bytes were preserved."}</p>
            {copy.error && <p className="project-library-error">{copy.error}</p>}
            <div className="project-library-creation-actions">
              {copy.value && onRecoverBundle && <button type="button" className="project-library-button" disabled={busy}
                onClick={() => void bundleOperation("recover", copy)}>Check original import</button>}
              <button type="button" className="project-library-button" disabled={busy} onClick={() => {
                const url = URL.createObjectURL(new Blob([copy.raw], { type: "application/json" }));
                const anchor = document.createElement("a"); anchor.href = url; anchor.download = "motionwright-portable-import.json";
                anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
              }}>Download import copy</button>
              {onDiscardBundle && <button type="button" className="project-library-button" disabled={busy}
                onClick={() => removeBundleCopy(copy)}>Remove local import copy</button>}
            </div>
          </article>)}
        </section>}
        {onCreate && (
          <form className="project-library-create" onSubmit={event => void createProject(event)}>
            <h3>Create a project</h3>
            <label htmlFor={createId}>Project title</label>
            <input id={createId} value={newTitle} autoComplete="off"
              aria-describedby={createHintId} onChange={event => { titleInputVersion.current++; setNewTitle(event.target.value); }} />
            <p id={createHintId}>Choose a short name you can recognize later.</p>
            <button type="submit" className="project-library-button project-library-primary"
              disabled={busy || unresolvedCreation || !newTitle.trim()}>{opening?.kind === "create" ? "Creating…" : "Create project"}</button>
            <button type="button" className="project-library-button"
              disabled={busy || unresolvedCreation} onClick={() => void createProject(undefined, true)}>
              Create editable motion example</button>
            <p>Start with a six-second motion study: two scenes, editable shapes, colors and movement.
              Edit and preview on the Canvas, then export your saved project. No account or paid AI is needed.
              Native rendering uses the separately installed runtime shown in Integrations.</p>
            {unresolvedCreation && <p role="status">Review the retained creation request below before creating another project. You can still open and edit saved projects.</p>}
          </form>
        )}
        {(creationCopies.length > 0 || creationError || creationLimited) && <section className="project-library-creations" aria-label="Retained project creations">
          <div className="project-library-list-heading"><h3>Retained creation requests</h3>
            {onRefreshCreations && <button type="button" className="project-library-button" disabled={busy} onClick={onRefreshCreations}>Refresh requests</button>}
          </div>
          <p>Check the original request after a missing response. Finding it opens the current project, including later edits.</p>
          {creationError && <p className="project-library-error" role="alert">{creationError}</p>}
          {creationLimited && <p role="status">The local request list exceeded its inspection limit. No copies were removed automatically.</p>}
          {creationCopies.map((copy, index) => <article key={copy.key} aria-label={"Retained creation " + (index + 1)}>
            <h4>{copy.value?.intent.title ?? "Unreadable creation request"}</h4>
            {copy.value?.intent.template && <p>Editable motion example · motion-study-v1</p>}
            <p>{copy.value ? new Date(copy.value.createdAt).toLocaleString() + " · "
              + (copy.value.phase === "observed" ? "Creation receipt observed" : "Result unconfirmed")
              : "The original local bytes were preserved. Download this copy before removing it if you need to inspect it."}</p>
            {copy.error && <p className="project-library-error">{copy.error}</p>}
            <div className="project-library-creation-actions">
              {copy.value && onRecoverCreation && <button type="button" className="project-library-button" disabled={busy}
                onClick={() => void recoverCreation(copy)}>Check creation result</button>}
              <button type="button" className="project-library-button" disabled={busy} onClick={() => {
                const url = URL.createObjectURL(new Blob([copy.raw], { type: "application/json" }));
                const anchor = document.createElement("a"); anchor.href = url; anchor.download = "motionwright-creation-request.json";
                anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
              }}>Download request</button>
              {onDiscardCreation && <button type="button" className="project-library-button" disabled={busy}
                onClick={() => discardCreation(copy)}>Remove local request</button>}
            </div>
            <p className="project-library-copy-note">Removing this local request does not cancel creation or delete a saved project.</p>
          </article>)}
        </section>}
        {operationError && <p className="project-library-error" role="alert">{operationError}</p>}
        {(opening || closeNotice) && (
          <p className="project-library-operation-status" role="status" aria-live="polite">
            {closeNotice ?? (opening?.kind === "bundle" ? opening.title + "…" : opening?.kind === "recover" ? "Checking the original creation request…" : opening?.kind === "create" ? "Creating the new project…"
              : `Opening “${opening?.title}”…`)}
          </p>
        )}
      </div>
      <footer className="project-library-footer">
        <span>Choose a project to continue editing.</span>
        <button type="button" className="project-library-button" disabled={busy} onClick={requestClose}>Close</button>
      </footer>
    </dialog>
  );
}
