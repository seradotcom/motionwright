import { useEffect, useRef, useState } from "react";
import {
  commitPrivateDelivery, inspectPrivateDelivery, preparePrivateDelivery,
  type PreparedPrivateDelivery, type PrivateDeliveryManifest,
} from "./api";
import { captureDeliveryDispatch, type DeliveryProjectState } from "./deliveryProfileDraft";
import type { SavedMasterHint } from "./savedNativeMedia";
import type { Project } from "./types";

export default function PrivateDeliverySlot({ project, deliverableId, hint, blocked, currentProjectState }: {
  project: Project;
  deliverableId: string;
  hint: SavedMasterHint | null;
  blocked: boolean;
  currentProjectState: () => DeliveryProjectState;
}) {
  const [prepared, setPrepared] = useState<PreparedPrivateDelivery | null>(null);
  const [manifest, setManifest] = useState<PrivateDeliveryManifest | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [needsInspection, setNeedsInspection] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef(false);
  const mounted = useRef(true);
  const ready = !blocked && !needsInspection && hint !== null
    && project.deliverables.find(profile => profile.id === deliverableId)?.burn_in_captions === false;
  const live = useRef({ project, deliverableId, prepared, ready, blocked, currentProjectState });
  live.current = { project, deliverableId, prepared, ready, blocked, currentProjectState };
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  async function run(work: () => Promise<void>, committing = false) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError(null);
    try { await work(); }
    catch (cause) {
      if (mounted.current) {
        if (committing) {
          setManifest(undefined);
          setNeedsInspection(true);
        }
        const message = typeof cause === "string" ? cause
          : cause instanceof Error ? cause.message : "Private delivery did not return a confirmed result.";
        setError(committing ? message + " Inspect the slot before approving another delivery." : message);
      }
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  async function inspect() {
    const result = await inspectPrivateDelivery(project, deliverableId);
    if (mounted.current) {
      setManifest(result);
      setPrepared(null);
      setNeedsInspection(false);
    }
  }

  async function prepare() {
    if (!hint || !ready) return;
    const result = await preparePrivateDelivery(project, deliverableId, hint);
    if (mounted.current) setPrepared(result);
  }

  async function approve() {
    if (!prepared || !ready) return;
    const gesture = captureDeliveryDispatch({
      project, profileId: deliverableId, inputSerial: 0, actionKey: prepared.subject,
    }, () => {
      const now = live.current;
      return {
        currentProject: now.currentProjectState(), renderedProject: now.project, profileId: now.deliverableId,
        inputSerial: 0, renderedInputSerial: 0, actionKey: now.prepared?.subject ?? "",
        ready: now.ready, draftBlocked: now.blocked, mounted: mounted.current,
      };
    });
    const result = await commitPrivateDelivery(project, prepared, gesture.beforeDispatch);
    if (gesture.stillCurrent()) {
      setManifest(result);
      setPrepared(null);
    }
  }

  return <section aria-label="Private delivery slot" className="caption-export-panel">
    <header><div><strong>Private delivery slot</strong>
      <span>This project keeps one approved master per output profile in its local media store.</span></div></header>
    <button type="button" className="button compact" disabled={busy} onClick={() => void run(inspect)}>
      Inspect private slot
    </button>
    {manifest === null && <p role="status">This profile has no private delivery.</p>}
    {manifest && <p role="status">Stored revision {manifest.plan.revision} · <code>{manifest.plan.artifact.sha256}</code></p>}
    {hint && <button type="button" className="button compact" disabled={!ready || busy} onClick={() => void run(prepare)}>
      Prepare private delivery
    </button>}
    {prepared && <div>
      <p>Approve saved revision {prepared.plan.revision} · {prepared.plan.artifact.size_bytes} bytes · <code>{prepared.plan.artifact.sha256}</code></p>
      <p>Expected slot commit {prepared.plan.previous_commit_id ?? "empty"}.</p>
      <button type="button" className="button button-primary" disabled={!ready || busy}
        onClick={() => void run(approve, true)}>Approve and commit privately</button>
    </div>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
