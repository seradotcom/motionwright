import { CircleDashed, RefreshCw, TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { readVerifiedNativeAvReview } from "./api";
import type { Project } from "./types";

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
  seekTimeSeconds,
  onMediaTime,
  onMediaPlay,
  embedded = false,
  editorialPlaying = false,
}: {
  project: Project;
  exportToken: string;
  /** Optional exact Film output-relative time when embedded in Program. */
  seekTimeSeconds?: number;
  onMediaTime?: (seconds: number) => void;
  onMediaPlay?: () => void;
  embedded?: boolean;
  editorialPlaying?: boolean;
}) {
  const [playback, setPlayback] = useState<PlaybackState>({ kind: "loading" });
  const [retry, setRetry] = useState(0);
  const [metadata, setMetadata] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const lastReportedMediaTimeRef = useRef<number | null>(null);
  // React Strict Mode replays effect setup/cleanup during mount. Reuse the
  // same in-flight read without caching settled media beyond the active view.
  const pendingRef = useRef<{ key: string; promise: Promise<Uint8Array> } | null>(null);
  const sourceKey = [project.id, project.generation, project.revision, exportToken].join(":");

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    setPlayback({ kind: "loading" });
    setMetadata(null);
    const existing = pendingRef.current;
    const promise = existing?.key === sourceKey ? existing.promise
      : readVerifiedNativeAvReview(project, exportToken);
    if (!existing || existing.key !== sourceKey) {
      pendingRef.current = { key: sourceKey, promise };
      const release = () => {
        if (pendingRef.current?.promise === promise) pendingRef.current = null;
      };
      // Both fulfillment and rejection release the deduplicated request.
      void promise.then(release, release);
    }
    void promise
      .then((bytes) => {
        if (!active) return;
        // Copy the read-only Tauri IPC view before it is owned by a Blob.
        const media = new Uint8Array(bytes.byteLength);
        media.set(bytes);
        objectUrl = URL.createObjectURL(new Blob([media.buffer], { type: "video/mp4" }));
        setPlayback({ kind: "ready", url: objectUrl });
      })
      .catch((reason) => {
        if (active) {
          setPlayback({
            kind: "unavailable",
            message: reason instanceof Error ? reason.message : String(reason),
          });
        }
      });
    return () => {
      active = false;
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
    };
  }, [project.id, project.generation, project.revision, exportToken, retry]);

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
        <span>Verified current r{project.revision} · at most 16 MiB</span>
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
          onPlay={onMediaPlay}
          onTimeUpdate={(event) => {
            const current = event.currentTarget.currentTime;
            if (!Number.isFinite(current)) return;
            lastReportedMediaTimeRef.current = current;
            onMediaTime?.(current);
          }}
          onError={() => {
            setPlayback({
              kind: "unavailable",
              message:
                "This WebView could not decode the verified MP4. Use Export verified MP4 to review the file in a compatible local player.",
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
        <span>{embedded
          ? "Native media clock follows source-scoped editorial seeks"
          : "Independent media review · not timeline-synchronized"}</span>
      </div>
    </div>
  );
}
