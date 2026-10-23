import { useEffect, useRef, useState } from "react";
import type { RetainedPreviewFrame as Frame } from "./previewRecovery";

export default function RetainedPreviewFrame({ frame, currentRevision, confirmed }: {
  frame: Frame; currentRevision: number; confirmed: boolean;
}) {
  const [image, setImage] = useState<{ id: string; url: string } | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const activeId = useRef<string | null>(frame.id);
  activeId.current = frame.id;
  useEffect(() => {
    activeId.current = frame.id;
    const url = URL.createObjectURL(frame.blob);
    setImage({ id: frame.id, url });
    setFailed(null);
    // This component owns this URL exclusively. A native reader's cleanup can
    // never revoke the retained image, and an old decode cannot clear a new one.
    return () => { activeId.current = null; URL.revokeObjectURL(url); };
  }, [frame.id]); // Immutable IDs bind the bytes; readback may clone the Blob.
  const current = image?.id === frame.id ? image : null;
  return <div className="native-frame-stage" role="region" aria-label="Retained historical preview">
    {current && failed !== frame.id && <img className="native-frame-image" src={current.url}
      alt={`Retained preview from revision ${frame.sourceRevision} · frame ${frame.frameIndex + 1} of ${frame.frameCount} · not current`}
      draggable={false}
      onLoad={event => {
        if (activeId.current !== frame.id) return;
        if (event.currentTarget.naturalWidth !== frame.pixelWidth || event.currentTarget.naturalHeight !== frame.pixelHeight) setFailed(frame.id);
      }}
      onError={() => { if (activeId.current === frame.id) setFailed(frame.id); }}/>} 
    {failed === frame.id && <div className="native-frame-error" role="alert">
      <strong>The retained preview could not be decoded</strong>
      <span>Its stored copy was preserved. Read recovery again or inspect the original render in Deliver.</span>
    </div>}
    <div className="native-frame-held" role="status">
      HISTORICAL PREVIEW · NOT CURRENT · source r{frame.sourceRevision} · project r{currentRevision}
    </div>
    <div className="native-frame-caption">
      <span>Previously decoded PNG · frame {frame.frameIndex + 1} / {frame.frameCount}</span>
      <span>{confirmed ? "Local recovery copy verified · no current render authority" : "Memory copy · recovery not yet confirmed"}</span>
    </div>
  </div>;
}
