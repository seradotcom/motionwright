import { useEffect, useRef, useState } from "react";
import { adoptWorkflowDraft, closeKnownWorkflowAttempt, exportWorkflowDraft, importWorkflowDraft, persistWorkflowSession,
  readWorkflowPayload, restoreWorkflowCopy, retryWorkflowCustody, workflowCommand, workflowCopies, workflowDiagnostic } from "./workflowActionDraft";
import type { WorkflowSession, WorkflowStore, WorkflowPointers } from "./workflowActionDraft";

type Props = {
  session: WorkflowSession; storage: WorkflowStore; pointers: WorkflowPointers; inFlight: boolean; backupError: string | null;
  onOperation(operation: () => void): void;
};
function download(raw: string, filename: string) {
  const url = URL.createObjectURL(new Blob([raw], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url);
}
export default function WorkflowActionRecovery({ session, storage, pointers, inFlight, backupError, onOperation }: Props) {
  const [copies, setCopies] = useState(() => workflowCopies(session.draft.scope, storage));
  const [selectedCopy, setSelectedCopy] = useState("");
  const [fileError, setFileError] = useState<string | null>(null);
  const [reading, setReading] = useState(false), reader = useRef(0);
  useEffect(() => () => { reader.current++; }, [session]);
  const pending = session.draft.pending;
  const request = pending ? readWorkflowPayload(JSON.parse(pending.requestJson)) : null;
  const chosen = copies.entries.find(copy => copy.key === selectedCopy);
  const refresh = () => { setCopies(workflowCopies(session.draft.scope, storage)); setSelectedCopy(""); };
  async function readFile(file?: File) {
    if (!file || inFlight) return;
    const ticket = ++reader.current, scope = session.draft.scope;
    setReading(true); setFileError(null);
    try {
      if (file.size > 8 * 1024 * 1024) throw new Error("The workflow input file exceeds 8 MiB. Its original text is not truncated.");
      const raw = await file.text(); if (ticket !== reader.current || scope !== session.draft.scope) return;
      const imported = importWorkflowDraft(raw, scope);
      onOperation(() => adoptWorkflowDraft(session, imported, storage, pointers)); refresh();
    } catch (error) { if (ticket === reader.current) setFileError(workflowDiagnostic(error)); }
    finally { if (ticket === reader.current) setReading(false); }
  }
  return <section className="workflow-control-panel" aria-label="Workflow author recovery">
    <header><div><span className="section-label">Author input</span><strong>Recoverable workflow draft</strong></div></header>
    <p>Recording details, trace choices and each candidate's input stay with this project, generation and branch. Restoring input never starts a workflow.</p>
    {backupError && <div className="jobs-inline-error" role="alert"><div><strong>Workflow backup needs attention.</strong><span>{backupError}</span></div></div>}
    {session.failedSource && <p role="alert">The original selected copy is unreadable or unavailable. It may contain an unconfirmed action. Keep its bytes and repair that copy before sending anything.</p>}
    {pending && request && <section className="workflow-receipt" aria-label="Retained original workflow action">
      <header><div><span className="section-label">{pending.outcome}</span><strong>{workflowCommand(request.action)}</strong></div></header>
      <p role={pending.outcome === "UNKNOWN" ? "alert" : "status"}>{pending.outcome === "UNKNOWN"
        ? "UNKNOWN: the original action has no confirmed result in this editor. Its identity and arguments are retained; sending another action is blocked."
        : pending.outcome === "NOT_SENT" ? "This attempt did not invoke the workflow action. Keep or acknowledge its original input explicitly."
        : "The original live invocation returned a known failure. Review its details before acknowledging the failure."}</p>
      {pending.diagnostic && <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{pending.diagnostic}</p>}
      <dl><dt>Original request</dt><dd className="mono" style={{ overflowWrap: "anywhere" }}>{request.request_id}</dd>
        <dt>Original source</dt><dd>Project {request.project_id}; generation {request.generation}; revision {request.revision}.</dd></dl>
      <details><summary>Original action and arguments</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{pending.requestJson}</pre></details>
      {pending.failure && <details><summary>Reported failure and receipt observation</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(pending.failure, null, 2)}</pre></details>}
      {pending.outcome === "UNKNOWN" ? <p>Refresh reads the current workflow evidence. It does not determine this request's outcome, and an absent or unavailable receipt does not authorize another send.</p>
        : <button className="button compact" type="button" disabled={inFlight || !!backupError} onClick={() => onOperation(() => {
          closeKnownWorkflowAttempt(session, storage, pointers); persistWorkflowSession(session, storage, pointers);
        })}>Acknowledge known outcome and keep drafting</button>}
    </section>}
    <div className="workflow-form-actions">
      <button className="button compact" type="button" onClick={() => download(exportWorkflowDraft(session.draft), "workflow-author-input.json")}>Export workflow author input</button>
      <button className="button compact" type="button" disabled={inFlight} onClick={() => onOperation(() => retryWorkflowCustody(session, storage, pointers))}>Confirm workflow backup again</button>
      {session.failedSource?.raw !== null && session.failedSource?.raw !== undefined && <button className="button compact" type="button"
        onClick={() => download(session.failedSource!.raw!, "workflow-original-unreadable-copy.json")}>Export original unreadable copy</button>}
    </div>
    <div className="workflow-form"><label><span>Import workflow author input</span><input type="file" accept="application/json,.json"
      disabled={inFlight || reading} onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; void readFile(file); }} /></label></div>
    {reading && <p role="status">Reading author input…</p>}{fileError && <p role="alert">{fileError}</p>}
    <details><summary>Independent local recovery copies</summary>
      <p>Each writer keeps its own copy. Restoring merges input into a new writer and preserves any later input already in this editor.</p>
      <div className="workflow-form"><label><span>Workflow recovery copy</span><select value={selectedCopy} onChange={event => setSelectedCopy(event.target.value)}>
        <option value="">Choose an exact copy</option>{copies.entries.map(copy => <option key={copy.key} value={copy.key}>{copy.writer} — {copy.error ? "unreadable; export available" : copy.draft?.pending ? "original action retained" : "author input"}</option>)}
      </select></label></div>
      <div className="workflow-form-actions">
        <button className="button compact" type="button" onClick={refresh}>Refresh workflow copies</button>
        <button className="button compact" type="button" disabled={!chosen} onClick={() => chosen && download(chosen.raw, "workflow-original-copy.json")}>Export selected original copy</button>
        <button className="button compact" type="button" disabled={!chosen?.draft || inFlight || !!backupError} onClick={() => chosen && onOperation(() => {
          restoreWorkflowCopy(session, chosen, storage, pointers); refresh();
        })}>Restore selected workflow input</button>
      </div>
      {copies.error && <p role="alert">{copies.error}</p>}{copies.limited && <p role="alert">The copy list is incomplete. No missing entry is treated as evidence that an action was not sent.</p>}
      {chosen?.error && <p role="alert">{chosen.error}</p>}
    </details>
    {session.draft.lastOutcome && <details><summary>Retained response from an earlier action</summary>
      <p>This is the saved response associated with that original invocation. It does not observe the current backend state or resolve a different unconfirmed action.</p>
      <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(session.draft.lastOutcome, null, 2)}</pre>
    </details>}
  </section>;
}
