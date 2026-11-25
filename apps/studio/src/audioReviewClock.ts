import type { Project, RationalTime } from "./types";
import { selectedAudioScenes } from "./audioRecipeContract";
import { voiceAuditionSource } from "./voiceAudition";
import { deriveVoiceCut, isOriginalVoiceMontage } from "./voiceCut";

/** View-only mapping of verified PCM to the saved project. Exact rational
 * boundaries are accumulated before conversion to the browser's media clock. */
export type AudioReviewSource = { kind: "voice" | "mix"; sourceId: string };
export type AudioReviewRequest = AudioReviewSource & { scope: string; label: string; wav: Blob };
export type AudioReviewActions = { open: (request: AudioReviewRequest) => void; invalidate: (source: AudioReviewSource) => void };
type Fraction = { n: bigint; d: bigint };
export type AudioReviewSpan = { outputStart: Fraction; outputEnd: Fraction; projectStart: Fraction; projectEnd: Fraction };
export type AudioReviewClock = { sampleRate: number; sampleCount: number; spans: AudioReviewSpan[] };
const MAX = (1n << 63n) - 1n;
const ZERO: Fraction = { n: 0n, d: 1n };
const fail = (message: string): never => { throw new Error(message); };
function fraction(n: bigint, d: bigint): Fraction {
  if (n < 0n || d <= 0n) return fail("Audio review needs a nonnegative exact saved clock.");
  let a = n, b = d;
  while (b) [a, b] = [b, a % b];
  n /= a || 1n; d /= a || 1n;
  if (n > MAX || d > MAX) return fail("Audio review exceeds the saved rational clock.");
  return { n, d };
}
function read(value: RationalTime): Fraction {
  if (!/^(0|[1-9][0-9]{0,18})$/.test(value.num) || !/^[1-9][0-9]{0,18}$/.test(value.den))
    return fail("Audio review has an invalid saved timestamp.");
  const n = BigInt(value.num), d = BigInt(value.den);
  const result = fraction(n, d);
  if (result.n !== n || result.d !== d) return fail("Audio review needs canonical rational timestamps.");
  return result;
}
const add = (a: Fraction, b: Fraction) => fraction(a.n * b.d + b.n * a.d, a.d * b.d);
const sub = (a: Fraction, b: Fraction) => fraction(a.n * b.d - b.n * a.d, a.d * b.d);
const compare = (a: Fraction, b: Fraction) => a.n * b.d < b.n * a.d ? -1 : a.n * b.d > b.n * a.d ? 1 : 0;
const numeric = (value: Fraction) => Number(value.n) / Number(value.d);
const saved = (value: Fraction): RationalTime => ({ num: String(value.n), den: String(value.d) });
export const audioReviewScope = (project: Project): string =>
  JSON.stringify([project.id, project.generation, project.active_branch, project.revision]);

export function buildAudioReviewClock(project: Project, source: AudioReviewSource): AudioReviewClock {
  let sampleRate: number, spans: AudioReviewSpan[], duration: Fraction;
  if (source.kind === "voice") {
    if (source.sourceId !== project.audio.active_voice_track_id)
      return fail("An alternate take can be heard independently. Select it explicitly before linking it to the project timeline.");
    voiceAuditionSource(project, source.sourceId);
    const voice = project.audio.voice_tracks.find(track => track.id === source.sourceId)!;
    sampleRate = voice.sample_rate_hz; duration = read(voice.measured_duration);
    const projectEnd = project.scenes.reduce((end, scene) => {
      const next = add(read(scene.start), read(scene.duration));
      return compare(next, end) > 0 ? next : end;
    }, ZERO);
    if (compare(duration, projectEnd) > 0)
      return fail("The original take extends beyond the saved project timeline. Continue independent listening or edit the timeline explicitly.");
    spans = [{ outputStart: ZERO, outputEnd: duration, projectStart: ZERO, projectEnd: duration }];
  } else {
    const profile = project.deliverables.find(value => value.id === source.sourceId);
    if (!profile || profile.audio_sample_rate_hz !== 48000)
      return fail("The original mix needs its saved 48-kHz delivery profile.");
    if (!profile.voice_track_id)
      return fail("The saved mix must bind an explicit measured delivery voice.");
    const voice = voiceAuditionSource(project, profile.voice_track_id).track;
    if (voice.sample_rate_hz !== 48000 || voice.channels !== 2)
      return fail("The saved original mix requires the existing stereo 48-kHz voice source.");
    const recipe = project.production_design?.delivery_recipes?.[profile.id];
    const binding = recipe?.original_mix?.voice_cut_sha256 ?? recipe?.voice_only_cut_sha256;
    if (isOriginalVoiceMontage(project, profile)) {
      if (!binding || !/^[a-f0-9]{64}$/.test(binding))
        return fail("The complete original cut is the default. Review and save an explicit voice cut binding for the montage.");
      const cut = deriveVoiceCut(project, profile);
      // Media is admitted only by the separately source-verified Native API.
      // The exact saved scope and native output length are checked by playback.
      const at = (samples: number) => fraction(BigInt(samples), 48000n);
      return {sampleRate: 48000, sampleCount: cut.outputFrames, spans: cut.spans.map(span => ({
        outputStart: at(span.outputStart), outputEnd: at(span.outputStart + span.sourceEnd - span.sourceStart),
        projectStart: at(span.sourceStart), projectEnd: at(span.sourceEnd),
      }))};
    }
    if (binding !== undefined) return fail("Saved voice cut binding is stale; clear or review it.");
    sampleRate = 48000; duration = ZERO; spans = [];
    const scenes = selectedAudioScenes(project, profile.id);
    if (!scenes.length || scenes.length > 512) return fail("Audio review needs a bounded nonempty saved cut.");
    for (const scene of scenes) {
      const start = read(scene.start), length = read(scene.duration), end = add(start, length);
      if (!length.n) return fail("An audio review scene has no duration.");
      if (compare(start, duration) !== 0)
        return fail("The original mix cannot silently remove a gap or overlap in the saved project clock.");
      if (spans.some(span => compare(start, span.projectEnd) < 0 && compare(end, span.projectStart) > 0))
        return fail("Overlapping source scenes do not have one unambiguous review clock.");
      const next = add(duration, length);
      spans.push({ outputStart: duration, outputEnd: next, projectStart: start, projectEnd: end });
      duration = next;
    }
    if(compare(duration, read(voice.measured_duration)) !== 0)
      return fail("The decoded original mix and the saved project must have the same measured duration.");
  }
  if (!Number.isSafeInteger(sampleRate) || sampleRate < 8000 || sampleRate > 96000)
    return fail("Audio review needs its measured sample rate.");
  const scaled = duration.n * BigInt(sampleRate);
  if (scaled % duration.d) return fail("The complete audition does not end on an exact sample. No timing was rounded.");
  const samples = scaled / duration.d;
  if (samples <= 0n || samples > BigInt(sampleRate) * 180n)
    return fail("Audio review supports the existing bounded original source duration.");
  return { sampleRate, sampleCount: Number(samples), spans };
}

export function audioSampleToProject(clock: AudioReviewClock, sample: number): RationalTime | null {
  if (!Number.isSafeInteger(sample) || sample < 0 || sample > clock.sampleCount) return null;
  if (sample === clock.sampleCount) return saved(clock.spans[clock.spans.length - 1].projectEnd);
  const at = fraction(BigInt(sample), BigInt(clock.sampleRate));
  const span = clock.spans.find(item => compare(at, item.outputStart) >= 0 && compare(at, item.outputEnd) < 0);
  return span ? saved(add(span.projectStart, sub(at, span.outputStart))) : null;
}
/** Quantize the observed whole media clock once, never each scene duration. */
export function audioMediaToProject(clock: AudioReviewClock, seconds: number): number | null {
  if (!Number.isFinite(seconds) || seconds < 0 || seconds > clock.sampleCount / clock.sampleRate + 1 / clock.sampleRate) return null;
  const sample = Math.min(clock.sampleCount, Math.floor(seconds * clock.sampleRate + 1e-7));
  const at = audioSampleToProject(clock, sample);
  return at ? Number(at.num) / Number(at.den) : null;
}
/** Source gaps or excluded scenes are explicit, never snapped to a nearby cut. */
export function projectToAudioMedia(clock: AudioReviewClock, seconds: number): number | null {
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  const span = clock.spans.find(item => seconds >= numeric(item.projectStart) && seconds < numeric(item.projectEnd));
  if (span) return numeric(span.outputStart) + seconds - numeric(span.projectStart);
  const last = clock.spans[clock.spans.length - 1];
  return seconds === numeric(last.projectEnd) ? clock.sampleCount / clock.sampleRate : null;
}
