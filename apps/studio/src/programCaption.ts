import { captionTextRuns, type CaptionTextRun } from "./transcriptEmphasis";
import { seconds, type DeliverableProfile, type Project, type Scene, type TranscriptSegment } from "./types";

export interface ProgramCaptionSample { cue: TranscriptSegment | null; runs: CaptionTextRun[]; issue: string | null }
/** Read-only source-clock caption study, outside the scene camera. This neither
 * claims native glyph placement nor invents per-word timing or a voice choice. */
export function sampleProgramCaption(project: Project, scene: Scene, playhead: number, profile: DeliverableProfile | null): ProgramCaptionSample | null {
  if (!profile?.captions || (profile.included_scene_ids.length && !profile.included_scene_ids.includes(scene.id))) return null;
  const blocked = (issue: string): ProgramCaptionSample => ({ cue: null, runs: [], issue });
  const empty: ProgramCaptionSample = { cue: null, runs: [], issue: null };
  const sceneStart = seconds(scene.start), sceneEnd = sceneStart + seconds(scene.duration);
  if (![playhead, sceneStart, sceneEnd].every(Number.isFinite)) return blocked("The caption source clock is invalid.");
  if (playhead < sceneStart || playhead >= sceneEnd) return empty;
  const tracks = new Set(project.audio.transcript.map(cue => cue.voice_track_id));
  if (!profile.voice_track_id && (tracks.size > 1 || profile.burn_in_captions))
    return blocked("Select the output voice take to inspect its captions.");
  const voiceId = profile.voice_track_id ?? [...tracks][0];
  if (!voiceId) return empty;
  const voice = project.audio.voice_tracks.find(track => track.id === voiceId);
  if (!voice) return blocked("The caption voice source is no longer available.");
  const selected = project.audio.transcript.filter(cue => cue.voice_track_id === voiceId);
  if (selected.length > 768) return blocked("The caption source exceeds the bounded review budget.");
  if (selected.some(cue => !Number.isFinite(seconds(cue.start)) || !Number.isFinite(seconds(cue.end))
    || seconds(cue.start) < 0 || seconds(cue.end) <= seconds(cue.start))) return blocked("Correct the caption source intervals before playback.");
  const active = selected.filter(cue => seconds(cue.start) <= playhead && playhead < seconds(cue.end));
  if (active.length > 1) return blocked("Caption cues overlap at this source time. Review their timing in Audio.");
  const cue = active[0];
  if (!cue) return empty;
  if (cue.alignment.kind === "unknown") return blocked("This cue has no manual or measured timing.");
  if (cue.alignment.kind === "measured" && cue.alignment.source_sha256 !== voice.source_sha256)
    return blocked("The caption alignment refers to a different voice source.");
  if (seconds(cue.end) > seconds(voice.measured_duration)) return blocked("The caption ends after the measured voice source.");
  if (profile.burn_in_captions && (seconds(cue.start) < sceneStart || seconds(cue.end) > sceneEnd))
    return blocked("This burn-in cue crosses a native scene boundary.");
  if (typeof cue.text !== "string" || Array.from(cue.text).length > 8000 || !cue.text.trim())
    return blocked("Correct the caption text before playback.");
  try { return { cue, runs: captionTextRuns(cue.text, cue.emphasis), issue: null }; }
  catch (error) { return blocked(error instanceof Error ? error.message : String(error)); }
}
