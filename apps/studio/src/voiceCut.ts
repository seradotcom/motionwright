import type { DeliverableProfile, Project, RationalTime } from "./types";

const maxSamples = 48_000 * 180;
const sha = /^[a-f0-9]{64}$/;
export type VoiceCutSpan = { sceneId: string; sourceStart: number; sourceEnd: number; outputStart: number };
export type VoiceCut = { sourceFrames: number; outputFrames: number; spans: VoiceCutSpan[]; wire: string };
export function isOriginalVoiceMontage(project: Project, profile: DeliverableProfile): boolean {
  return profile.included_scene_ids.length > 0 && (profile.included_scene_ids.length !== project.scenes.length ||
    profile.included_scene_ids.some((id, i) => id !== project.scenes[i].id));
}
function exactSamples(time: RationalTime): number {
  if (!/^(0|[1-9][0-9]{0,18})$/.test(time.num) || !/^[1-9][0-9]{0,18}$/.test(time.den))
    throw new Error("Voice cut boundaries must be exact 48-kHz samples.");
  const n = BigInt(time.num) * 48000n, d = BigInt(time.den);
  if (n % d || n / d > BigInt(maxSamples)) throw new Error("Voice cut exceeds the exact bounded original source clock.");
  return Number(n / d);
}
/** Pure view of the saved full-source clock; no caller-supplied ranges. */
export function deriveVoiceCut(project: Project, profile: DeliverableProfile): VoiceCut {
  if (!isOriginalVoiceMontage(project, profile) || profile.audio_sample_rate_hz !== 48000 ||
      !project.scenes.length || project.scenes.length > 512)
    throw new Error("Voice cut requires an explicit non-original selected cut at 48 kHz.");
  const track = project.audio.voice_tracks.find(t => t.id === profile.voice_track_id);
  if (!track || track.sample_rate_hz !== 48000 || track.channels !== 2 || !sha.test(track.source_sha256) ||
      !project.assets.some(a => a.id === track.asset_id && a.content_sha256 === track.source_sha256 &&
        ["audio/wav", "audio/x-wav", "audio/wave"].includes(a.media_type)))
    throw new Error("Voice cut requires an explicitly bound exact imported stereo 48-kHz WAV.");
  const sourceFrames = exactSamples(track.measured_duration);
  let fullEnd = 0;
  const sourceSpans = new Map<string, {start: number; end: number}>();
  for (const scene of project.scenes) {
    const start = exactSamples(scene.start), length = exactSamples(scene.duration);
    if (start !== fullEnd || !length || sourceSpans.has(scene.id))
      throw new Error("Original voice must cover a continuous original project clock.");
    fullEnd = start + length;
    sourceSpans.set(scene.id, { start, end: fullEnd });
  }
  if (sourceFrames !== fullEnd || sourceFrames < 48000)
    throw new Error("Original voice must exactly cover the complete original project clock.");
  let outputFrames = 0;
  const seen = new Set<string>();
  const spans = profile.included_scene_ids.map(sceneId => {
    const source = sourceSpans.get(sceneId);
    if (!source || seen.has(sceneId)) throw new Error("Selected voice cut has missing or duplicate scenes.");
    seen.add(sceneId);
    const span = { sceneId, sourceStart: source.start, sourceEnd: source.end, outputStart: outputFrames };
    outputFrames += source.end - source.start;
    return span;
  });
  if (outputFrames < 48000 || outputFrames > maxSamples)
    throw new Error("Selected voice cut must last 1 to 180 seconds.");
  const wire = JSON.stringify(["motionwright.original-voice-cut/1", profile.id.toLowerCase(),
    track.id.toLowerCase(), track.asset_id.toLowerCase(), track.source_sha256, sourceFrames,
    spans.map(s => [s.sceneId.toLowerCase(), s.sourceStart, s.sourceEnd, s.outputStart])]);
  return {sourceFrames, outputFrames, spans, wire};
}
export async function voiceCutDigest(wire: string): Promise<string> {
  if (!crypto.subtle) throw new Error("Local SHA-256 verification is unavailable.");
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(wire))))
    .map(n => n.toString(16).padStart(2, "0")).join("");
}
/** Reject ambiguous cross-boundary timing instead of splitting a reviewed interval.
 * The Native plan independently validates these same windows and source bytes. */
export function validateVoiceCutIntervals(project: Project, profile: DeliverableProfile, cut: VoiceCut): void {
  const windows = project.audio.transcript.filter(segment => segment.voice_track_id === profile.voice_track_id)
    .map(segment => ({start:exactSamples(segment.start),end:exactSamples(segment.end)}));
  let count = 0;
  for (const span of cut.spans) {
    let end = span.sourceStart;
    for (const window of windows.filter(w=>w.start<span.sourceEnd && w.end>span.sourceStart).sort((a,b)=>a.start-b.start)) {
      if (window.start < span.sourceStart || window.end > span.sourceEnd)
        throw new Error("Reviewed voice interval crosses a selected scene boundary; re-align before conforming the cut.");
      if (window.start < end || window.end <= window.start)
        throw new Error("Selected reviewed voice intervals must not overlap.");
      end = window.end;
      count++;
    }
  }
  if (count < 1 || count > 64)
    throw new Error("The selected cut needs 1 to 64 reviewed voice intervals for ducking.");
}
/** The UI and Native independently recompute the binding before dispatch. */
export async function requireCurrentVoiceCut(project: Project, profile: DeliverableProfile, saved?: string): Promise<VoiceCut | null> {
  if (!isOriginalVoiceMontage(project, profile)) {
    if (saved !== undefined) throw new Error("Saved voice cut binding is stale; clear or review it.");
    return null;
  }
  const cut = deriveVoiceCut(project, profile);
  if (!saved || await voiceCutDigest(cut.wire) !== saved)
    throw new Error("Review and save the exact original voice cut binding before playback or mastering.");
  return cut;
}
