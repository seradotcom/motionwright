import { graphFrameTime } from "./semanticGraph";
import { editorFps, validEditorRate } from "./timecode";
import { rationalSeconds, seconds } from "./types";
import type { RationalTime } from "./types";

/** Preserve an exact frame-clock moment when authoring a marker. Continuous
 * seeks keep the existing timestamp conversion instead of snapping to a frame.
 */
export function timelineMarkerTime(playhead: number, rate: RationalTime): RationalTime {
  if (Number.isFinite(playhead) && playhead >= 0 && validEditorRate(rate)) {
    const fps = editorFps(rate);
    const frame = Math.round(playhead * fps);
    if (Number.isSafeInteger(frame) && frame >= 0) {
      try {
        const exact = graphFrameTime(frame, rate);
        // Rounding only finds a candidate. Equality with a clock projection is
        // required, so neighboring continuous instants receive no tolerance.
        if (playhead === frame / fps || playhead === seconds(exact)) return exact;
      } catch {
        // A noncanonical rate or unrepresentable frame keeps the established
        // conversion; selecting a display rate does not add an authoring gate.
      }
    }
  }
  return rationalSeconds(playhead);
}
