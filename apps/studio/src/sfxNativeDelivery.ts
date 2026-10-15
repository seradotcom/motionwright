// Native AV readiness for original semantic SFX. This is an EDITORIAL
// preflight only; the desktop still verifies the actual source CAS bytes,
// revision, preview token and one-use render permission before any mux.
import type { DeliverableProfile, Project, RationalTime } from "./types";
import { validateSfxProject } from "./semanticSfx";

export type OriginalSfxAvReadiness = {
  ready: boolean;
  reason: string;
  cueCount: number;
  frames: number | null;
  sourceAuthority: "editorial_only";
};

function framesAt30(time: RationalTime): bigint | null {
  try {
    const n = BigInt(time.num), d = BigInt(time.den);
    if (d <= 0n || n < 0n || (n * 30n) % d !== 0n) return null;
    return n * 30n / d;
  } catch {
    return null;
  }
}

function rejected(reason: string, cues: number): OriginalSfxAvReadiness {
  return { ready: false, reason, cueCount: cues, frames: null, sourceAuthority: "editorial_only" };
}

/** Never assert that the browser has opened, decoded or rendered actual WAVs. */
export function originalSfxAvReadiness(
  project: Project,
  profile: DeliverableProfile | null,
  nativeFrames: number | null,
  registeredSingleSegment: boolean,
  desktopMode: boolean,
  profileDirty: boolean,
): OriginalSfxAvReadiness {
  const count = project.audio.sfx_cues?.length ?? 0;
  if (!desktopMode) return rejected("SFX mastering requires the installed desktop runtime.",count);
  if (!profile || profileDirty) return rejected("Save the selected output profile first.",count);
  if (profile.voice_track_id) return rejected(
    "This SFX-only master cannot combine narration or music. Choose a profile without a voice track.",count);
  if (profile.frame_rate.num !== "30" || profile.frame_rate.den !== "1"
    || profile.audio_sample_rate_hz !== 48000 || profile.video_codec !== "h264"
    || profile.audio_codec !== "aac" || profile.color_space !== "rec709"
    || profile.container !== "mp4" || profile.included_scene_ids.length !== 0) {
    return rejected("SFX master requires the full source cut, 30 fps, 48 kHz and Rec.709 H.264/AAC MP4.",count);
  }
  if (count === 0 || count > 64)
    return rejected("Author at least one original semantic SFX cue (maximum 64).",count);
  if (!registeredSingleSegment || nativeFrames === null
    || !Number.isSafeInteger(nativeFrames) || nativeFrames < 1 || nativeFrames > 900) {
    return rejected("A current, registered 1–900-frame native Motion Canvas preview is required.",count);
  }
  if (project.scenes.some(scene => scene.renderer !== "motion-canvas")) {
    return rejected("This source-only path requires every included scene to use Motion Canvas.",count);
  }
  let last = 0n;
  for (const scene of project.scenes) {
    const start = framesAt30(scene.start), duration = framesAt30(scene.duration);
    if (start === null || duration === null || duration <= 0n || start !== last) {
      return rejected("Every scene must have exact contiguous 30-fps timing.",count);
    }
    last += duration;
  }
  if (last !== BigInt(nativeFrames))
    return rejected("Rendered native frame count differs from the authored scene clock.",count);
  try {
    const cues = validateSfxProject(project);
    if (!cues.length || cues.some(item => item.absolute_end_frame > nativeFrames)) {
      return rejected("A scene-bound SFX extends beyond the current native cut.",count);
    }
    // Asset metadata is not actual CAS admission. The desktop rechecks bytes
    // from the original imported source for every SFX before native mux.
    return {
      ready: true,
      reason: "Editorial cues align to this render. Native CAS byte verification and listening approval are still required.",
      cueCount: count,
      frames: nativeFrames,
      sourceAuthority: "editorial_only",
    };
  } catch {
    return rejected("Original semantic SFX source/schedule failed its editorial contract.",count);
  }
}
