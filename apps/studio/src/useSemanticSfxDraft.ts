import { useEffect, useMemo, useRef, useState } from "react";
import type { Change, Project } from "./types";
import { bootstrap, projectHistory } from "./api";
import { sameValue } from "./creativeProduction";
import {
  acknowledgeSfxDraft, assertSfxOrigins, createSfxDraft, decodeSfxCopy, editSfxDraft,
  encodeSfxCopy, inspectSfxOutcome, prepareSfxSubmission, readSfxCatalogue, readSfxCopies,
  readSfxPointer, reconcileSfxDraft, recoverSfxRead, resolveSfxDraft, retainSfxFailure,
  retainSfxOrigin, separateSfxIntent, sfxChange, sfxConflict, sfxCopyKey, sfxHasWork,
  sfxIdentity, sfxLocked, sfxOutcomeMatches, sfxPointerKey, sfxScope, sfxUnknown,
  SFX_COPY_PREFIX, writeSfxCopy,
} from "./semanticSfxDraft";
import type { SfxCustody, SfxDraft, SfxOutcome, SfxRaw, SfxRecoveryEntry } from "./semanticSfxDraft";

interface SfxSession {
  draft: SfxDraft; custody: SfxCustody; pointerObserved: boolean; editedWhileRecoveryBlocked: boolean;
  saving: boolean; recoveryProblem: string | null; storageError: string | null; error: string | null;
  notice: string | null; copies: SfxRecoveryEntry[]; limited: boolean; notify?: () => void;
}
const sessions = new Map<string, SfxSession>();
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const owner = (scope: string) => JSON.stringify(sfxIdentity(scope).slice(0, 3));
const projectOwner = (project: Project) => JSON.stringify([project.id, project.generation, project.active_branch]);
function notify(session: SfxSession) {
  for (const value of sessions.values()) if (owner(value.draft.scope) === owner(session.draft.scope)) value.notify?.();
  window.dispatchEvent(new Event("motionwright-sfx-draft"));
}
function busy(project: Project) {
  return [...sessions.values()].some(value => owner(value.draft.scope) === projectOwner(project) && value.saving);
}
export function sfxLiveTargets(project: Project): string[] {
  return [...sessions.values()].filter(value => owner(value.draft.scope) === projectOwner(project))
    .map(value => value.draft.target);
}
function refresh(session: SfxSession) {
  try {
    const value = readSfxCopies(window.localStorage, session.draft.scope);
    session.copies = value.entries; session.limited = value.limited;
  } catch (reason) { session.storageError = message(reason); }
}
function fork(session: SfxSession) {
  session.custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: session.custody.expectedPointer };
}
function readOriginal(scope: string, writerId: string) {
  const key = sfxCopyKey(scope, writerId), raw = window.localStorage.getItem(key);
  if (raw === null) throw new Error("The earlier sound copy is missing. Its pointer and the current input were kept.");
  const draft = retainSfxOrigin(decodeSfxCopy(raw, scope, writerId).draft, { key, raw });
  assertSfxOrigins(window.localStorage, draft);
  if (window.localStorage.getItem(key) !== raw) throw new Error("The earlier sound copy changed while reading.");
  return { draft, key, raw };
}
function sessionFor(project: Project, target: string): SfxSession {
  const scope = sfxScope(project, target), found = sessions.get(scope); if (found) return found;
  let pointer: string | null = null, pointerObserved = false, recovered: SfxDraft | null = null, problem: string | null = null;
  let notice: string | null = null;
  try {
    pointer = window.sessionStorage.getItem(sfxPointerKey(scope)); pointerObserved = true;
    const reload = performance.getEntriesByType("navigation").some(entry => (entry as PerformanceNavigationTiming).type === "reload");
    if (pointer && reload && !window.opener) {
      recovered = readOriginal(scope, pointer).draft;
      if (window.sessionStorage.getItem(sfxPointerKey(scope)) !== pointer) throw new Error("The sound recovery pointer changed while reading.");
      notice = "Recovered this tab's sound draft. No request was repeated.";
    }
  } catch (reason) { problem = message(reason); recovered = null; }
  const missing = target !== "new" && !(project.audio.sfx_cues ?? []).some(cue => cue.id === target);
  if (!recovered && missing) {
    // The workspace only opens absent targets after explicit selection of a live or valid retained draft.
    // This restores author input; absence continues to prohibit an upsert or removal of this identity.
    const copies = readSfxCatalogue(window.localStorage, project).entries
      .filter(entry => entry.value?.draft.scope === scope).sort((a, b) => b.value!.updatedAt - a.value!.updatedAt);
    if (!copies.length) throw new Error("This absent sound event has no readable retained draft. Export the original bytes from its recovery list.");
    recovered = readOriginal(scope, copies[0].writerId).draft;
    notice = "Opened retained input for an absent sound event. Its original identity cannot be saved or removed.";
  }
  const session: SfxSession = {
    draft: recovered ?? createSfxDraft(project, target),
    custody: { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: pointer },
    pointerObserved, editedWhileRecoveryBlocked: false, saving: false,
    recoveryProblem: problem, storageError: problem, error: null, notice, copies: [], limited: false,
  };
  sessions.set(scope, session); refresh(session); return session;
}
function backup(session: SfxSession): boolean {
  try {
    if (session.recoveryProblem) throw new Error(session.recoveryProblem + " Read that source before continuing; current input remains exportable.");
    if (session.custody.expectedRaw !== null) {
      const prior = decodeSfxCopy(session.custody.expectedRaw, session.draft.scope, session.custody.writerId).draft;
      if (!sfxHasWork(prior) && !sameValue(prior, session.draft)) fork(session);
    }
    writeSfxCopy(window.localStorage, window.sessionStorage, session.draft, session.custody);
    session.storageError = null; refresh(session); return session.storageError === null;
  } catch (reason) { session.storageError = message(reason); refresh(session); return false; }
}
function replace(session: SfxSession, draft: SfxDraft, persist = true) {
  session.draft = draft; const retained = persist ? backup(session) : true; notify(session); return retained;
}
export function useSemanticSfxDraft(project: Project, target: string, commit: (change: Change) => Promise<Project>) {
  const scope = sfxScope(project, target);
  const session = useMemo(() => sessionFor(project, target), [scope]);
  const [, render] = useState(0), [outcome, setOutcome] = useState<SfxOutcome | null>(null), [reviewing, setReviewing] = useState(false);
  const current = useRef(project), mounted = useRef(true), epoch = useRef(0); current.current = project;
  const saving = busy(project), conflict = sfxConflict(session.draft, project);
  const locked = sfxLocked(project, target, session.draft.raw);
  const missing = target !== "new" && !(project.audio.sfx_cues ?? []).some(cue => cue.id === target);
  useEffect(() => {
    mounted.current = true; const update = () => render(value => value + 1); session.notify = update;
    const changed = (event: StorageEvent) => {
      if (event.key === null || event.key.startsWith(SFX_COPY_PREFIX) || event.key === sfxPointerKey(scope)) {
        try {
          assertSfxOrigins(window.localStorage, session.draft);
          if (session.custody.expectedRaw !== null
            && window.localStorage.getItem(sfxCopyKey(scope, session.custody.writerId)) !== session.custody.expectedRaw)
            throw new Error("The current sound recovery copy changed in another window. Current input was kept.");
        } catch (reason) { session.storageError = message(reason); }
        refresh(session); update();
      }
    };
    window.addEventListener("storage", changed);
    return () => { mounted.current = false; epoch.current++; if (session.notify === update) delete session.notify; window.removeEventListener("storage", changed); };
  }, [session, scope]);
  useEffect(() => {
    try {
      const next = reconcileSfxDraft(session.draft, project);
      if (!sameValue(next, session.draft)) replace(session, next, sfxHasWork(next));
      setOutcome(value => sfxOutcomeMatches(session.draft, project, value) ? value : null);
    } catch (reason) { session.error = message(reason); notify(session); }
  }, [project, session]);
  function invalidateReview() { epoch.current++; setOutcome(null); setReviewing(false); }
  function edit(patch: Partial<SfxRaw>) {
    try {
      invalidateReview(); session.error = null; session.notice = null;
      if (session.recoveryProblem) session.editedWhileRecoveryBlocked = true;
      replace(session, editSfxDraft(session.draft, patch));
    } catch (reason) { session.error = message(reason); notify(session); }
  }
  async function submit(operation: "save" | "remove") {
    if (busy(current.current) || session.storageError || session.draft.pending) return;
    const id = crypto.randomUUID(), submitted = structuredClone(current.current);
    let prepared: ReturnType<typeof prepareSfxSubmission>;
    try {
      prepared = prepareSfxSubmission(session.draft, submitted, id, operation);
      if (!replace(session, prepared.draft)) {
        session.draft = retainSfxFailure(session.draft, id, "The recovery copy was not confirmed before dispatch.", true);
        session.error = "NOT_SENT: confirm the recoverable sound input before an explicit save or removal."; notify(session); return;
      }
    } catch (reason) { session.error = message(reason); notify(session); return; }
    session.saving = true; session.error = null; session.notice = null; invalidateReview(); notify(session);
    try {
      const receipt = await commit(prepared.change);
      if ((session.draft as SfxDraft).pending?.id !== id) return;
      const next = acknowledgeSfxDraft(session.draft, id, receipt, submitted);
      fork(session);
      session.notice = (operation === "remove" ? "Removed this sound event" : "Saved this sound event") + " at r" + receipt.revision + "."
        + (next.intent ? " Your later input remains available." : "");
      replace(session, next);
    } catch (reason) {
      session.error = message(reason);
      replace(session, retainSfxFailure(session.draft, id, message(reason)));
    } finally { session.saving = false; notify(session); }
  }
  function choose(choice: "saved" | "draft") {
    if (busy(current.current) || sfxUnknown(session.draft)) return;
    try {
      if (!backup(session)) return;
      const next = resolveSfxDraft(session.draft, current.current, choice);
      invalidateReview(); fork(session); session.error = null;
      session.notice = choice === "saved" ? "Using saved sound values. The earlier input remains in its own copy."
        : "Using the displayed sound event as the explicit base. The earlier copy remains available.";
      replace(session, next);
    } catch (reason) { session.error = message(reason); notify(session); }
  }
  function retry() {
    if (busy(current.current)) return;
    try {
      if (session.recoveryProblem) {
        const observed = readSfxPointer(window.sessionStorage, scope, { observed: session.pointerObserved, value: session.custody.expectedPointer });
        session.pointerObserved = observed.observed; session.custody.expectedPointer = observed.value;
        let next = session.draft, original: ReturnType<typeof readOriginal> | null = null;
        if (observed.value !== null) {
          original = readOriginal(scope, observed.value);
          next = recoverSfxRead(session.draft, original.draft, session.editedWhileRecoveryBlocked);
        }
        assertSfxOrigins(window.localStorage, next);
        if (window.sessionStorage.getItem(sfxPointerKey(scope)) !== observed.value
          || (original && window.localStorage.getItem(original.key) !== original.raw))
          throw new Error("The sound recovery source changed before adoption. Earlier bytes were kept.");
        fork(session); session.draft = next; session.recoveryProblem = null; session.editedWhileRecoveryBlocked = false;
      }
      if (backup(session)) {
        // Only a live pre-dispatch failure has this authority. Reloaded copies decode as UNKNOWN.
        if (session.draft.pending?.outcome === "NOT_SENT") {
          fork(session); replace(session, { ...session.draft, pending: null });
        }
        if (!session.storageError) session.notice = "Confirmed the sound recovery copy. No project change was sent; save explicitly when ready.";
      }
    } catch (reason) { session.storageError = message(reason); }
    notify(session);
  }
  function restore(copy: SfxRecoveryEntry) {
    if (busy(current.current) || session.draft.pending || !copy.value) return;
    try {
      if (sfxHasWork(session.draft) && !backup(session)) return;
      const observed = readSfxPointer(window.sessionStorage, scope, { observed: session.pointerObserved, value: session.custody.expectedPointer });
      session.pointerObserved = observed.observed; session.custody.expectedPointer = observed.value;
      if (window.localStorage.getItem(copy.key) !== copy.raw) throw new Error("This sound copy changed. Refresh it before restoring.");
      const restored = retainSfxOrigin(decodeSfxCopy(copy.raw, scope, copy.writerId).draft, { key: copy.key, raw: copy.raw });
      assertSfxOrigins(window.localStorage, restored);
      if (window.sessionStorage.getItem(sfxPointerKey(scope)) !== observed.value || window.localStorage.getItem(copy.key) !== copy.raw)
        throw new Error("The sound recovery source changed before restoration.");
      fork(session); session.draft = restored; session.recoveryProblem = null; session.editedWhileRecoveryBlocked = false;
      invalidateReview(); session.error = null;
      backup(session); session.notice = "Restored this sound input into a separate copy. No project change was sent.";
    } catch (reason) { session.storageError = message(reason); refresh(session); }
    notify(session);
  }
  async function inspect() {
    if (busy(current.current) || reviewing || !sfxUnknown(session.draft)) return;
    const started = ++epoch.current; setReviewing(true); setOutcome(null);
    try {
      const result = await inspectSfxOutcome(session.draft, current.current, {
        readProject: async id => {
          const value = await bootstrap(id);
          if (!value.project || value.project_selection.status !== "selected") throw new Error("The saved sound project is unavailable.");
          return value.project;
        }, readHistory: projectHistory,
      });
      if (!mounted.current || started !== epoch.current) return;
      if (!sfxOutcomeMatches(session.draft, current.current, result)) throw new Error("The current sound input changed during inspection.");
      setOutcome(result); session.error = null; notify(session);
    } catch (reason) { if (mounted.current && started === epoch.current) { session.error = message(reason); notify(session); } }
    finally { if (mounted.current && started === epoch.current) setReviewing(false); }
  }
  function separate() {
    if (busy(current.current) || !outcome) return;
    try {
      if (!backup(session)) return;
      const retained = retainSfxOrigin(session.draft, { key: sfxCopyKey(scope, session.custody.writerId), raw: session.custody.expectedRaw! });
      // Inspection binds the pending request and full saved Project. Origins are added only to the successor.
      const next = { ...separateSfxIntent(session.draft, current.current, outcome), origins: retained.origins };
      assertSfxOrigins(window.localStorage, next);
      fork(session); invalidateReview(); session.error = null;
      replace(session, next);
      session.notice = "Kept the original uncertain attempt. No result was acknowledged or retried; this is a separate editable intent.";
      notify(session);
    } catch (reason) { session.storageError = message(reason); notify(session); }
  }
  function download(raw?: string) {
    try {
      const value = raw ?? encodeSfxCopy(session.draft, session.custody.writerId);
      const url = URL.createObjectURL(new Blob([value], { type: "application/json" })), anchor = document.createElement("a");
      anchor.href = url; anchor.download = "motionwright-sound-recovery-" + Date.now() + ".json";
      document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 0);
      session.notice = "Started downloading the retained sound input. This does not confirm a project save."; notify(session);
    } catch (reason) { session.error = message(reason); notify(session); }
  }
  let validation: string | null = null;
  if (!session.draft.pending && !conflict && !locked && !missing) {
    try { sfxChange(session.draft, project); } catch (reason) { validation = message(reason); }
  }
  return { draft: session.draft, session, saving, conflict, locked, missing, validation, unknown: sfxUnknown(session.draft),
    reviewing, outcome, blocked: saving || !!session.storageError || conflict || locked || missing || !!session.draft.pending || !!validation,
    edit, save: () => submit("save"), remove: () => submit("remove"), choose, retry, restore, inspect, separate, download,
    refresh: () => { refresh(session); notify(session); } };
}
