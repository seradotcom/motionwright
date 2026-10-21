import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import {
  appendProjectLibraryItems,
  DEFAULT_PROJECT_PAGE_SIZE,
  type ListProjectsRequest,
  type ProjectLibraryCursor,
  type ProjectLibraryPage,
  type ProjectLibrarySummary,
  type ProjectCreationCopy,
} from "./projectLibrary";
import type { Project } from "./types";
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
  onCreate?: (title: string) => Promise<Project>;
  creationCopies?: ProjectCreationCopy[];
  creationLimited?: boolean;
  creationError?: string | null;
  onRecoverCreation?: (copy: ProjectCreationCopy) => Promise<Project | null>;
  onDiscardCreation?: (copy: ProjectCreationCopy) => void;
  onRefreshCreations?: () => void;
  notice?: string | null;
}

type Opening = { kind: "open"; projectId: string; title: string } | { kind: "create" } | { kind: "recover"; title: string };
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
}: ProjectLibraryDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const createId = useId();
  const createHintId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
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

  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const create = callbacks.current.onCreate;
    if (!create || operationRef.current) return;
    const submittedTitle = newTitle;
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
      const project = await create(submittedTitle);
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
        {onCreate && (
          <form className="project-library-create" onSubmit={event => void createProject(event)}>
            <h3>Create a project</h3>
            <label htmlFor={createId}>Project title</label>
            <input id={createId} value={newTitle} autoComplete="off"
              aria-describedby={createHintId} onChange={event => { titleInputVersion.current++; setNewTitle(event.target.value); }} />
            <p id={createHintId}>Choose a short name you can recognize later.</p>
            <button type="submit" className="project-library-button project-library-primary"
              disabled={busy || unresolvedCreation || !newTitle.trim()}>{opening?.kind === "create" ? "Creating…" : "Create project"}</button>
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
            {closeNotice ?? (opening?.kind === "recover" ? "Checking the original creation request…" : opening?.kind === "create" ? "Creating the new project…"
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
