import { CircleDashed, RefreshCw, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
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
}: {
  project: Project;
  exportToken: string;
}) {
  const [playback, setPlayback] = useState<PlaybackState>({ kind: "loading" });
  const [retry, setRetry] = useState(0);
  const [metadata, setMetadata] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    setPlayback({ kind: "loading" });
    setMetadata(null);
    void readVerifiedNativeAvReview(project, exportToken)
      .then((bytes) => {
        // Copy the read-only Tauri IPC view before it is owned by a Blob.
        const media = new Uint8Array(bytes.byteLength);
        media.set(bytes);
        objectUrl = URL.createObjectURL(new Blob([media.buffer], { type: "video/mp4" }));
        if (active) {
          setPlayback({ kind: "ready", url: objectUrl });
        } else {
          URL.revokeObjectURL(objectUrl);
          objectUrl = null;
        }
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

  return (
    <div className="native-av-review" role="region" aria-label="Native master playback review">
      <div className="native-av-review-header">
        <strong>Native H.264/AAC review</strong>
        <span>Verified current r{project.revision} · at most 16 MiB</span>
      </div>
      {playback.kind === "ready" ? (
        <video
          key={playback.url}
          src={playback.url}
          controls
          playsInline
          preload="metadata"
          aria-label="Verified native MP4 review player"
          onLoadedMetadata={(event) => {
            const video = event.currentTarget;
            if (Number.isFinite(video.duration)) {
              setMetadata(
                video.videoWidth + " × " + video.videoHeight + " · " +
                video.duration.toFixed(2) + " s decoded metadata",
              );
            }
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
        <span>Independent media review · not timeline-synchronized</span>
      </div>
    </div>
  );
}
