import { CircleDashed, RefreshCw, TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { readNativeAvAudioQc, readVerifiedNativeAvReview, readNativeAudioQcReceiptInventory, readNativeAudioQcReceipt } from "./api";
import {
  audioQcStatusLabel, audioQcUnavailableText, createNativeAvReadGuard,
  formatAudioMilli, nativeAvReadScope,
} from "./nativeAvAudioQc";
import type { DeliveryAudioQcReport, NativeAvAudioQcResponse, NativeAudioQcReceiptHint, NativeAudioQcReceiptInventoryResponse, Project } from "./types";

type PlaybackState =
  | { kind: "loading" }
  | { kind: "ready"; url: string }
  | { kind: "unavailable"; message: string };

/**
 * Real H.264/AAC MP4 bytes from the existing Semwright owner artifact.
 * The browser receives no host path, only the verified bounded binary IPC.
 */
export default function NativeAvReview({
  project,
  exportToken,
  reviewOnly = false,
  seekTimeSeconds,
  onMediaTime,
  onMediaPlay,
  embedded = false,
  editorialPlaying = false,
  deliverableId = null,
  masterSha256 = null,
}: {
  project: Project;
  exportToken: string;
  /** Owner source SHA passed, but no installed decoded caption glyph proof. */
  reviewOnly?: boolean;
  /** Optional exact Film output-relative time when embedded in Program. */
  seekTimeSeconds?: number;
  onMediaTime?: (seconds: number) => void;
  onMediaPlay?: () => void;
  embedded?: boolean;
  editorialPlaying?: boolean;
  /** Public identity narrows the view; the opaque token owns native readback. */
  deliverableId?: string | null;
  masterSha256?: string | null;
}) {
  const [retry, setRetry] = useState(0);
  const guard = useRef(createNativeAvReadGuard());
  const identity = nativeAvReadScope(project, exportToken, deliverableId, masterSha256);
  const readKey = guard.current.viewKey(JSON.stringify([identity, retry]));
  const visibleKey = useRef(readKey);
  visibleKey.current = readKey;
  const [storedPlayback, storePlayback] = useState<{ key: string; value: PlaybackState } | null>(null);
  const playback: PlaybackState = storedPlayback?.key === readKey ? storedPlayback.value : { kind: "loading" };
  const setPlayback = (value: PlaybackState) => storePlayback({ key: readKey, value });
  const [storedMetadata, storeMetadata] = useState<{ key: string; value: string | null } | null>(null);
  const metadata = storedMetadata?.key === readKey ? storedMetadata.value : null;
  const setMetadata = (value: string | null) => storeMetadata({ key: readKey, value });
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const lastReportedMediaTimeRef = useRef<number | null>(null);
  // React Strict Mode replays effect setup/cleanup during mount. Reuse the
  // same in-flight read without caching settled media beyond the active view.
  const pendingRef = useRef<{ key: string; promise: Promise<Uint8Array> } | null>(null);

  useEffect(() => {
    const ticket = guard.current.begin(readKey);
    let objectUrl: string | null = null;
    setPlayback({ kind: "loading" });
    setMetadata(null);
    lastReportedMediaTimeRef.current = null;
    const existing = pendingRef.current;
    const promise = existing?.key === readKey ? existing.promise
      : readVerifiedNativeAvReview(project, exportToken);
    if (!existing || existing.key !== readKey) {
      pendingRef.current = { key: readKey, promise };
      const release = () => {
        if (pendingRef.current?.promise === promise) pendingRef.current = null;
      };
      // Both fulfillment and rejection release the deduplicated request.
      void promise.then(release, release);
    }
    void promise
      .then((bytes) => {
        if (!guard.current.isCurrent(ticket, visibleKey.current)) return;
        // Copy the read-only Tauri IPC view before it is owned by a Blob.
        const media = new Uint8Array(bytes.byteLength);
        media.set(bytes);
        objectUrl = URL.createObjectURL(new Blob([media.buffer], { type: "video/mp4" }));
        setPlayback({ kind: "ready", url: objectUrl });
      })
      .catch(() => {
        if (guard.current.isCurrent(ticket, visibleKey.current)) {
          setPlayback({
            kind: "unavailable",
            message: "The original master could not be read in this session. No render or measurement was requested.",
          });
        }
      });
    return () => {
      guard.current.cancel(ticket);
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
    };
  }, [readKey]);

  useEffect(() => {
    if (playback.kind !== "ready" || typeof seekTimeSeconds !== "number"
      || !Number.isFinite(seekTimeSeconds)) return;
    const player = videoRef.current;
    if (!player) return;
    const requested = Math.max(0, seekTimeSeconds);
    // A media clock update should not seek the decoder back to itself;
    // explicit editor stepping or timeline scrubbing *does* change the source.
    if (lastReportedMediaTimeRef.current !== null &&
      Math.abs(lastReportedMediaTimeRef.current - requested) < 0.015) {
      lastReportedMediaTimeRef.current = null;
      return;
    }
    if (Math.abs(player.currentTime - requested) >= 0.015) {
      try {
        player.currentTime = requested;
      } catch {
        // Some WebViews require loadedmetadata; that event re-applies the seek.
      }
    }
  }, [playback.kind, seekTimeSeconds]);

  useEffect(() => {
    if (editorialPlaying) videoRef.current?.pause();
  }, [editorialPlaying]);

  return (
    <div className={"native-av-review" + (embedded ? " in-program" : "")}
      role="region" aria-label="Native master playback review">
      <div className="native-av-review-header">
        <strong>Native H.264/AAC review</strong>
        <span>Original master · source r{project.revision}{reviewOnly ? " · review only · no export" : ""} · at most 16 MiB</span>
      </div>
      {playback.kind === "ready" ? (
        <video
          key={playback.url}
          ref={videoRef}
          src={playback.url}
          controls
          playsInline
          preload="metadata"
          aria-label="Verified native MP4 review player"
          onLoadedMetadata={(event) => {
            if (visibleKey.current !== readKey) return;
            const video = event.currentTarget;
            if (typeof seekTimeSeconds === "number" && Number.isFinite(seekTimeSeconds)
              && Number.isFinite(video.duration)) {
              try {
                video.currentTime = Math.max(0, Math.min(seekTimeSeconds, video.duration));
              } catch {
                // Some native decoders reject seeks until the initial frame is ready.
              }
            }
            if (Number.isFinite(video.duration)) {
              setMetadata(
                video.videoWidth + " × " + video.videoHeight + " · " +
                video.duration.toFixed(2) + " s decoded metadata",
              );
            }
          }}
          onPlay={() => { if (visibleKey.current === readKey) onMediaPlay?.(); }}
          onTimeUpdate={(event) => {
            if (visibleKey.current !== readKey) return;
            const current = event.currentTarget.currentTime;
            if (!Number.isFinite(current)) return;
            lastReportedMediaTimeRef.current = current;
            onMediaTime?.(current);
          }}
          onError={() => {
            if (visibleKey.current !== readKey) return;
            setPlayback({
              kind: "unavailable",
              message:
                reviewOnly
                  ? "The review-only captioned MP4 cannot be decoded by this WebView. Export remains blocked until original master glyph pixels are verified on this device."
                  : "This WebView could not decode the verified MP4. Use Export verified MP4 to review the file in a compatible local player.",
            });
          }}
        />
      ) : playback.kind === "loading" ? (
        <div className="native-av-review-state" role="status">
          <CircleDashed size={19} aria-hidden="true" />
          <span>Checking source digest and loading a bounded MP4…</span>
        </div>
      ) : (
        <div className="native-av-review-state" role="alert">
          <TriangleAlert size={19} aria-hidden="true" />
          <strong>Native MP4 review unavailable</strong>
          <span>{playback.message}</span>
          <button className="button compact" type="button" onClick={() => setRetry((index) => index + 1)}>
            <RefreshCw size={13} aria-hidden="true" /> Retry readback
          </button>
        </div>
      )}
      <div className="native-av-review-footnote">
        {metadata ?? "Source-bound MP4; playback availability depends on this WebView decoder."}
        {reviewOnly && <span> · Installed decoded caption glyph pixels NOT VERIFIED · publication blocked</span>}
        <span>{embedded
          ? "Native media clock follows source-scoped editorial seeks"
          : "Independent media review · not timeline-synchronized"}</span>
      </div>
      {!embedded && <NativeAudioQcReview project={project} selection={{ kind: "session", exportToken }}
        deliverableId={deliverableId} masterSha256={masterSha256} />}
    </div>
  );
}

type AudioQcReadState = { kind: "loading" } | { kind: "unavailable" }
  | { kind: "ready"; response: NativeAvAudioQcResponse };

/** Reopening this view always revalidates the original native report. A retained
 * token is a lookup hint, not cached QC, an export permission or app recovery. */
export function NativeAudioQcReview({ project, selection, deliverableId, masterSha256 }: {
  project: Project;
  selection: { kind: "session"; exportToken: string } | { kind: "receipt"; hint: NativeAudioQcReceiptHint };
  deliverableId: string | null; masterSha256: string | null;
}) {
  const [retry, setRetry] = useState(0);
  const guard = useRef(createNativeAvReadGuard());
  const selector = selection.kind === "session" ? selection.exportToken
    : JSON.stringify([selection.hint.decoded_measurement_receipt_id, selection.hint.master_sha256]);
  const identity = JSON.stringify([selection.kind, nativeAvReadScope(project, selector, deliverableId, masterSha256)]);
  const readKey = guard.current.viewKey(JSON.stringify([identity, retry]));
  const visibleKey = useRef(readKey);
  visibleKey.current = readKey;
  const [stored, setStored] = useState<{ key: string; value: AudioQcReadState } | null>(null);
  const state: AudioQcReadState = stored?.key === readKey ? stored.value : { kind: "loading" };
  const pending = useRef<{ key: string; promise: Promise<NativeAvAudioQcResponse> } | null>(null);
  useEffect(() => {
    const ticket = guard.current.begin(readKey);
    setStored({ key: readKey, value: { kind: "loading" } });
    const previous = pending.current;
    const promise = previous?.key === readKey ? previous.promise
      : selection.kind === "session"
        ? readNativeAvAudioQc(project, selection.exportToken, masterSha256)
        : deliverableId === null ? Promise.reject(new Error("Output profile unavailable"))
          : readNativeAudioQcReceipt(project, deliverableId, selection.hint);
    if (!previous || previous.key !== readKey) {
      pending.current = { key: readKey, promise };
      const release = () => { if (pending.current?.promise === promise) pending.current = null; };
      void promise.then(release, release);
    }
    void promise.then(response => {
      if (guard.current.isCurrent(ticket, visibleKey.current)) {
        setStored({ key: readKey, value: { kind: "ready", response } });
      }
    }, () => {
      if (guard.current.isCurrent(ticket, visibleKey.current)) {
        setStored({ key: readKey, value: { kind: "unavailable" } });
      }
    });
    return () => guard.current.cancel(ticket);
  }, [readKey]);
  const response = state.kind === "ready" ? state.response : null;
  return <section aria-label="Native numeric audio QC" className="caption-export-panel">
    <header><div><strong>Final decoded audio · numeric QC</strong>
      <span>Reads the original master and measurement receipts.</span></div></header>
    {state.kind === "loading" && <p role="status">Reading the original audio report…</p>}
    {state.kind === "unavailable" && <p role="alert">
      Audio QC unavailable. The original report could not be read or its public response was invalid.
      No render, measurement or automatic retry was requested.
    </p>}
    {response?.kind === "unavailable" && <p role="status">
      Audio QC unavailable · {audioQcUnavailableText[response.reason]}
    </p>}
    {response?.kind === "not_requested" && <p role="status">
      Audio QC not requested for this master. No numeric report is available.
    </p>}
    {response?.kind === "report" && <AudioQcReport report={response.report} />}
    <button className="button compact" type="button" disabled={state.kind === "loading"}
      onClick={() => setRetry(value => value + 1)}>
      <RefreshCw size={13} aria-hidden="true" /> Read original audio QC again
    </button>
    <p className="delivery-truth-note">{selection.kind === "session"
      ? "This is a read of the same master token. It does not render, measure again, normalize audio or grant export permission. An expired app session remains unavailable until an original selector can be recovered and revalidated."
      : "This revalidates the exact original receipt and master. It does not render, measure again, reconcile uncertain work or restore playback/export permission."}</p>
  </section>;
}


type AudioQcInventoryState =
  | { kind: "idle" } | { kind: "loading" } | { kind: "error" }
  | { kind: "ready"; response: NativeAudioQcReceiptInventoryResponse };

/** Explicit discovery and selection, independent of playback/export tokens. */
export function NativeAudioQcHistory({ project, deliverableId }: {
  project: Project; deliverableId: string;
}) {
  const guard = useRef(createNativeAvReadGuard());
  const scope = JSON.stringify([project.id, project.generation, project.active_branch, project.revision, deliverableId]);
  const readKey = guard.current.viewKey(scope);
  const visibleKey = useRef(readKey);
  visibleKey.current = readKey;
  const active = useRef<{ scope: string } | null>(null);
  const [stored, setStored] = useState<{ key: string; state: AudioQcInventoryState } | null>(null);
  const [selected, setSelected] = useState<{ key: string; hint: NativeAudioQcReceiptHint } | null>(null);
  const state = stored?.key === readKey ? stored.state : { kind: "idle" as const };
  const response = state.kind === "ready" ? state.response : null;
  const inventory = response?.kind === "inventory" ? response.inventory : null;
  const selectedHint = selected?.key === readKey && inventory?.items.some(item =>
    item.decoded_measurement_receipt_id === selected.hint.decoded_measurement_receipt_id
    && item.master_sha256 === selected.hint.master_sha256) ? selected.hint : null;
  useEffect(() => () => {
    if (active.current) guard.current.cancel(active.current);
  }, [readKey]);
  async function discover() {
    const ticket = guard.current.begin(readKey);
    active.current = ticket;
    setSelected(null);
    setStored({ key: readKey, state: { kind: "loading" } });
    try {
      const response = await readNativeAudioQcReceiptInventory(project, deliverableId);
      if (guard.current.isCurrent(ticket, visibleKey.current)) {
        setStored({ key: readKey, state: { kind: "ready", response } });
      }
    } catch {
      if (guard.current.isCurrent(ticket, visibleKey.current)) {
        setStored({ key: readKey, state: { kind: "error" } });
      }
    }
  }
  return <section className="caption-export-panel" aria-label="Saved audio measurement recovery">
    <header><div><strong>Saved audio measurements</strong>
      <span>Read original receipts for this saved source and output profile.</span></div></header>
    <button className="button compact" type="button" disabled={state.kind === "loading"}
      onClick={() => void discover()}>Find saved audio measurements</button>
    {state.kind === "loading" && <p role="status">Reading original receipt references...</p>}
    {state.kind === "error" && <p role="alert">
      Saved audio measurement references are unavailable. No production or automatic retry was requested.
    </p>}
    {response?.kind === "unavailable" && <p role="status">
      Audio history unavailable: {audioQcUnavailableText[response.reason]}
    </p>}
    {inventory && <>
      {inventory.items.length === 0 && <p role="status">No completed measurement reference is available for this saved profile.</p>}
      {inventory.legacy_unbound_count > 0 && <p role="status">
        {inventory.legacy_unbound_count} current-source observations have no original recovery metadata.
        Their output profile cannot be inferred; history was not rewritten.
      </p>}
      {inventory.unavailable_count > 0 && <p role="status">
        {inventory.unavailable_count} original measurement chains have an incomplete or unavailable latest observation.
        No older completion was substituted.
      </p>}
      <ul>{inventory.items.map(hint => <li key={hint.decoded_measurement_receipt_id}>
        <span>Recorded master <code>{hint.master_sha256}</code></span>
        <button className="button compact" type="button"
          onClick={() => setSelected({ key: readKey, hint })}>
          Read audio QC receipt {hint.decoded_measurement_receipt_id}
        </button>
      </li>)}</ul>
    </>}
    <p className="delivery-truth-note">References are not measurement proof or export permission.
      Choose an original receipt to revalidate its source, master and complete history.
      This read does not render, measure, retry uncertain work or create a media token.</p>
    {selectedHint && <NativeAudioQcReview project={project}
      selection={{ kind: "receipt", hint: selectedHint }}
      deliverableId={deliverableId} masterSha256={selectedHint.master_sha256} />}
  </section>;
}

function AudioQcReport({ report }: { report: DeliveryAudioQcReport }) {
  const { qc, decoded_input: decoded, measurement_windows: windows, measured_levels: levels } = report;
  const metricRows = [
    ["Integrated loudness", levels.integrated_lufs_milli, report.units.integrated_lufs_milli],
    ["Momentary loudness at input end", levels.momentary_lufs_milli, report.units.momentary_lufs_milli],
    ["Short-term loudness at input end", levels.short_term_lufs_milli, report.units.short_term_lufs_milli],
    ["Loudness range", levels.loudness_range_milli, report.units.loudness_range_milli],
    ["True peak", levels.true_peak_millidbtp, report.units.true_peak_millidbtp],
    ["Sample peak", levels.sample_peak_millidbfs, report.units.sample_peak_millidbfs],
  ] as const;
  const target = (value: number | null, unit: string) => value === null
    ? "Not requested (null)" : formatAudioMilli(value) + " " + unit;
  const originalTarget = (value: number | null) => value === null ? "null" : String(value);
  return <>
    <p role="status"><strong>{audioQcStatusLabel[qc.status]}</strong> · {qc.status}</p>
    <div style={{ overflowX: "auto" }}>
      <table>
        <caption>Saved targets and final decoded measurements</caption>
        <thead><tr><th scope="col">Metric</th><th scope="col">Saved target</th>
          <th scope="col">Final measurement</th><th scope="col">Native numeric outcome</th></tr></thead>
        <tbody>
          <tr><th scope="row">Integrated loudness</th><td>{target(qc.target_lufs_milli, report.units.target_lufs_milli)}</td>
            <td>{formatAudioMilli(qc.measured_integrated_lufs_milli)} {report.units.integrated_lufs_milli}</td>
            <td>{audioQcStatusLabel[qc.lufs_status]}</td></tr>
          <tr><th scope="row">True peak ceiling</th><td>{target(qc.target_true_peak_millidbtp, report.units.target_true_peak_millidbtp)}</td>
            <td>{formatAudioMilli(qc.measured_true_peak_millidbtp)} {report.units.true_peak_millidbtp}</td>
            <td>{audioQcStatusLabel[qc.true_peak_status]}</td></tr>
        </tbody>
      </table>
    </div>
    <p>Loudness difference: {formatAudioMilli(qc.lufs_delta_milli)} {report.units.lufs_delta_milli}.
      Reported tolerance: ±{formatAudioMilli(qc.lufs_tolerance_milli)} {report.units.lufs_tolerance_milli}.</p>
    <p className="delivery-truth-note">These are the native numeric outcomes for the final decoded master.
      They do not certify normalization, intelligibility, listening, caption quality or human approval.</p>
    <details>
      <summary>Measurement values, actual interval and custody references</summary>
      <table>
        <caption>Public measured values · resolution {report.published_resolution.numerator}/{report.published_resolution.denominator}</caption>
        <thead><tr><th scope="col">Metric</th><th scope="col">Value</th><th scope="col">Unit</th></tr></thead>
        <tbody>{metricRows.map(([label, value, unit]) => <tr key={label}>
          <th scope="row">{label}</th><td>{formatAudioMilli(value)}</td><td>{unit}</td>
        </tr>)}</tbody>
      </table>
      <p>Method: {report.measurement_method.engine} {report.measurement_method.version};
        producer schema {report.measurement_method.producer_schema_version}.
        True peak oversampling: {report.true_peak_oversample}×.</p>
      <p>Actual decoded interval: [{decoded.sample_interval.start_sample_frame}, {decoded.sample_interval.end_sample_frame_exclusive})
        sample frames · {decoded.format} · {decoded.sample_rate_hz} Hz · {decoded.channels} channels ({decoded.channel_layout}).
        This includes any codec tail; it is not trimmed to the requested video duration.</p>
      <p>Momentary window: {windows.momentary_window_ms} ms. Short-term window: {windows.short_term_window_ms} ms.
        Both end at the decoded input end; neither is a maximum over the whole master.
        Integrated loudness and loudness range use the complete decoded input with provider gating.
        True peak and sample peak cover the complete decoded input.</p>
      <p>Saved target object: {qc.targets === null ? "absent (null)"
        : "LUFS " + originalTarget(qc.targets.target_lufs) + "; true peak dBTP " + originalTarget(qc.targets.target_true_peak_dbtp)}.
        Null values remain unavailable or not requested; they are never converted to zero.</p>
      <p>Published resolution authority: <code>{report.published_resolution.rounding_authority}</code>.</p>
      <dl style={{ overflowWrap: "anywhere" }}>
        <dt>Master SHA-256</dt><dd><code>{report.master_artifact_sha256}</code></dd>
        <dt>Final decoded WAV SHA-256</dt><dd><code>{decoded.sha256}</code></dd>
        <dt>Measurement receipt reference</dt><dd><code>{qc.measurement_receipt_id}</code></dd>
        <dt>Measurement proof digest reference</dt><dd><code>{qc.measurement_proof_sha256}</code></dd>
        <dt>Report contract</dt><dd>{report.schema} / {qc.schema}; {qc.evaluated_phase}</dd>
        <dt>Numeric evidence scope</dt><dd>{report.evidence_scope}</dd>
      </dl>
      <p>Receipt and digest references do not contain the private proof and cannot recreate it.</p>
    </details>
  </>;
}
