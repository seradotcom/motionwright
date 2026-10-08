import { CircleDashed, RefreshCw, TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { readNativePreviewFrame } from "./api";
import type { NativeFrameSelection } from "./nativeFrameSelection";
import type { Project } from "./types";

type FrameState =
  | { kind: "loading"; frameIndex: number }
  | { kind: "ready"; frameIndex: number; url: string }
  | { kind: "failed"; frameIndex: number; message: string };

/**
 * Sampled PNG readback from the real Semwright-rendered immutable artifact.
 * This is a still-frame scrubber, NOT an AV player or realtime decoder.
 */
export default function NativeFrameStage({
  project,
  selection,
}: {
  project: Project;
  selection: NativeFrameSelection;
}) {
  const [sample, setSample] = useState(selection);
  const [frame, setFrame] = useState<FrameState>({ kind: "loading", frameIndex: selection.frameIndex });
  const [retry, setRetry] = useState(0);
  const desired = useRef(selection);
  desired.current = selection;

  // Bound native readback to four sampled frame requests per second while
  // editorial transport runs at its independent (10Hz) display clock.
  useEffect(() => {
    setSample(desired.current);
    const timer = window.setInterval(() => {
      const latest = desired.current;
      setSample((previous) => previous.token === latest.token
        && previous.frameIndex === latest.frameIndex ? previous : latest);
    }, 250);
    return () => window.clearInterval(timer);
  }, [selection.token, project.id, project.generation, project.revision]);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    setFrame({ kind: "loading", frameIndex: sample.frameIndex });
    readNativePreviewFrame(project, sample.token, sample.frameIndex)
      .then((bytes) => {
        const copy = new Uint8Array(bytes.byteLength);
        copy.set(bytes);
        objectUrl = URL.createObjectURL(new Blob([copy.buffer], { type: "image/png" }));
        if (active) {
          setFrame({ kind: "ready", frameIndex: sample.frameIndex, url: objectUrl });
        } else {
          URL.revokeObjectURL(objectUrl);
          objectUrl = null;
        }
      })
      .catch((reason) => {
        if (active) {
          setFrame({
            kind: "failed",
            frameIndex: sample.frameIndex,
            message: reason instanceof Error ? reason.message : String(reason),
          });
        }
      });
    return () => {
      active = false;
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
    };
  }, [project.id, project.generation, project.revision, sample.token, sample.frameIndex, retry]);

  const frameLabel = "Frame " + (frame.frameIndex + 1) + " / " + selection.frameCount;
  return (
    <div className="native-frame-stage" role="region" aria-label="Verified native PNG frame preview">
      {frame.kind === "ready" ? (
        <img
          className="native-frame-image"
          src={frame.url}
          alt={"Verified native rendered PNG · " + frameLabel}
          draggable={false}
          onError={() => setFrame({
            kind: "failed",
            frameIndex: frame.frameIndex,
            message: "The decoded PNG cannot be displayed by this WebView.",
          })}
        />
      ) : frame.kind === "loading" ? (
        <div className="native-frame-loading" role="status">
          <CircleDashed size={21} aria-hidden="true" />
          <span>Reading verified native PNG…</span>
        </div>
      ) : (
        <div className="native-frame-error" role="alert">
          <TriangleAlert size={20} aria-hidden="true" />
          <strong>Native frame readback unavailable</strong>
          <span>{frame.message}</span>
          <button className="button compact" type="button" onClick={() => setRetry((value) => value + 1)}>
            <RefreshCw size={13} aria-hidden="true" /> Retry frame
          </button>
        </div>
      )}
      <div className="native-frame-caption">
        <span>{frameLabel} · source r{project.revision}</span>
        <span>SHA-256 checked · sampled PNG, not real-time playback</span>
      </div>
    </div>
  );
}
