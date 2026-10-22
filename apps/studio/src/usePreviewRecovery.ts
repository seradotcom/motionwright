import { useEffect, useRef, useState } from "react";
import {
  captureDecodedPreview, PREVIEW_RECOVERY_EVENT, PreviewRecoveryStore,
  previewRecoveryScopeKey, type DecodedPreviewFrame, type PreviewRecoveryScope,
  type RetainedPreviewFrame,
} from "./previewRecovery";

interface RecoveryState {
  scopeKey: string;
  frame: RetainedPreviewFrame | null;
  owned: RetainedPreviewFrame[];
  damagedOwned: RetainedPreviewFrame[];
  confirmed: boolean;
  busy: boolean;
  error: string | null;
}
const empty = (scopeKey: string): RecoveryState => ({ scopeKey, frame: null, owned: [], damagedOwned: [], confirmed: false, busy: false, error: null });
const message = (error: unknown) => error instanceof Error ? error.message : "Preview recovery could not be confirmed.";

/** No native I/O. All restoration stays historical, including same-revision
 * reloads. Only NativeFrameStage's real image onLoad supplies new captures. */
export function usePreviewRecovery(
  scope: PreviewRecoveryScope | null, revision: number, authorityKey: string,
) {
  const scopeKey = scope ? previewRecoveryScopeKey(scope) : "";
  const [state, setState] = useState<RecoveryState>(() => empty(scopeKey));
  const [reload, setReload] = useState(0);
  const store = useRef<PreviewRecoveryStore | null>(null);
  if (store.current === null) store.current = new PreviewRecoveryStore();
  const current = useRef({ scope, scopeKey, revision, authorityKey });
  current.current = { scope, scopeKey, revision, authorityKey };
  const mounted = useRef(false);
  const wanted = useRef<RetainedPreviewFrame | null>(null);
  const saving = useRef(false);
  const lastAccepted = useRef<string | null>(null);
  const loadEpoch = useRef(0);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; loadEpoch.current++; };
  }, []);

  useEffect(() => {
    if (!scope) return;
    const epoch = ++loadEpoch.current;
    const acceptedBeforeRead = lastAccepted.current;
    setState(previous => previous.scopeKey === scopeKey ? previous : empty(scopeKey));
    void store.current!.read(scope, revision).then(result => {
      if (!mounted.current || epoch !== loadEpoch.current || current.current.scopeKey !== scopeKey
          || lastAccepted.current !== acceptedBeforeRead) return;
      setState(previous => {
        // A background local notification must not replace a freshly decoded
        // in-memory successor whose durable write has not yet finished.
        if (previous.scopeKey === scopeKey && previous.frame && !previous.confirmed) return {
          ...previous, owned: result.owned, damagedOwned: result.damagedOwned };
        return { scopeKey, frame: result.frame, owned: result.owned, damagedOwned: result.damagedOwned, confirmed: result.frame !== null,
          busy: false, error: result.issues.join(" ") || null };
      });
    }).catch(reason => {
      if (!mounted.current || epoch !== loadEpoch.current || current.current.scopeKey !== scopeKey) return;
      setState(previous => ({ ...(previous.scopeKey === scopeKey ? previous : empty(scopeKey)), error: message(reason) }));
    });
    return () => { if (loadEpoch.current === epoch) loadEpoch.current++; };
  }, [scopeKey, revision, reload]); // The key fully identifies all scope fields.

  useEffect(() => {
    const changed = (event: Event) => {
      if ((event as CustomEvent<unknown>).detail === current.current.scopeKey && !saving.current) setReload(value => value + 1);
    };
    window.addEventListener(PREVIEW_RECOVERY_EVENT, changed);
    return () => window.removeEventListener(PREVIEW_RECOVERY_EVENT, changed);
  }, []);

  const drain = async () => {
    if (saving.current) return;
    saving.current = true;
    // One write plus one latest pending capture: scrubbing never builds an
    // unbounded PNG queue. Already decoded captures may finish after navigation,
    // but cannot update another scope's UI or replace its retained image.
    try {
      while (wanted.current) {
        const capture = wanted.current;
        wanted.current = null;
        try {
          const saved = await store.current!.save(capture);
          if (mounted.current && current.current.scopeKey === capture.scopeKey) {
            setState(previous => previous.scopeKey !== capture.scopeKey ? previous
              : previous.frame?.id !== capture.id || lastAccepted.current !== capture.id
                ? { ...previous, owned: [saved.frame] }
                : { ...previous, frame: saved.frame, owned: [saved.frame], confirmed: true, busy: false, error: saved.issue });
          }
        } catch (reason) {
          if (mounted.current && current.current.scopeKey === capture.scopeKey
              && lastAccepted.current === capture.id) {
            setState(previous => previous.frame?.id !== capture.id ? previous : {
              ...previous, confirmed: false, busy: false, error: message(reason),
            });
          }
        }
      }
    } finally { saving.current = false; }
  };

  const remember = (decoded: DecodedPreviewFrame, expectedAuthorityKey: string, sceneName: string) => {
    const active = current.current;
    if (!mounted.current || !active.scope || active.authorityKey !== expectedAuthorityKey) return;
    try {
      const capture = captureDecodedPreview(active.scope, active.revision, sceneName, decoded, store.current!.writerId);
      lastAccepted.current = capture.id;
      loadEpoch.current++;
      setState(previous => ({ scopeKey: active.scopeKey, frame: capture,
        owned: previous.scopeKey === active.scopeKey ? previous.owned : [],
        damagedOwned: previous.scopeKey === active.scopeKey ? previous.damagedOwned : [],
        confirmed: false, busy: true, error: null }));
      wanted.current = capture;
      void drain();
    } catch (reason) {
      setState(previous => ({ ...(previous.scopeKey === active.scopeKey ? previous : empty(active.scopeKey)), error: message(reason) }));
    }
  };
  const visible = state.scopeKey === scopeKey && (!state.frame || state.frame.sourceRevision <= revision)
    ? state : empty(scopeKey);
  const retry = () => {
    if (visible.frame && !visible.confirmed && !saving.current) {
      const capture = { ...visible.frame, id: crypto.randomUUID(), capturedAt: Date.now(), sha256: "" };
      lastAccepted.current = capture.id;
      wanted.current = capture;
      setState({ ...visible, frame: capture, busy: true, error: null });
      void drain();
    } else setReload(value => value + 1);
  };
  const discardFrame = async (frame: RetainedPreviewFrame | null) => {
    if (!frame || visible.busy || frame.writerId !== store.current!.writerId) return;
    const expected = frame.id, expectedScope = frame.scopeKey;
    try {
      await store.current!.discard(frame);
      if (mounted.current && current.current.scopeKey === expectedScope) {
        setState(previous => previous.scopeKey !== expectedScope ? previous : previous.frame?.id !== expected
          ? { ...previous, owned: previous.owned.filter(copy => copy.id !== expected),
            damagedOwned: previous.damagedOwned.filter(copy => copy.id !== expected) }
          : empty(expectedScope));
        setReload(value => value + 1);
      }
    } catch (reason) {
      if (mounted.current && current.current.scopeKey === expectedScope) {
        setState(previous => previous.scopeKey !== expectedScope
          || (previous.frame?.id !== expected && ![...previous.owned, ...previous.damagedOwned].some(copy => copy.id === expected))
          ? previous : { ...previous, error: message(reason) });
      }
    }
  };
  const earlier = visible.owned.find(frame => frame.id !== visible.frame?.id) ?? null;
  return { ...visible, writerId: store.current.writerId, remember, retry,
    discard: () => discardFrame(visible.confirmed ? visible.frame : null),
    canDiscardEarlier: !visible.confirmed && earlier !== null,
    discardEarlier: () => discardFrame(earlier),
    discardDamaged: () => discardFrame(visible.damagedOwned[0] ?? null) };
}
