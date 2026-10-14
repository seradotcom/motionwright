/** Review the existing immutable-source waveform proxy; never invent PCM, LUFS or true peak. */
import type { Change, Project, RationalTime, VoiceTrack, WaveformPage } from "./types";

export const WAVEFORM_REVIEW_METHOD = "motionwright.sample-peak-page-review/1";
const NEAR_FULL_SCALE = 0.999;
const QUIET_PROXY_PEAK = 0.001;
const MIN_QUIET_MS = 150;
const MAX_CANDIDATES = 16;
const digest = /^[a-f0-9]{64}$/i;
const decimal = /^(0|[1-9][0-9]{0,79})$/;

function exact(numerator: bigint, denominator: bigint): RationalTime {
  let a = numerator, b = denominator;
  while (b !== 0n) { const r = a % b; a = b; b = r; }
  const gcd = a || 1n;
  return { num: String(numerator / gcd), den: String(denominator / gcd) };
}
function timeParts(time: RationalTime): [bigint, bigint] {
  if (!time || !decimal.test(time.num) || !decimal.test(time.den) || time.den === "0") {
    throw new Error("Invalid measured voice-track duration.");
  }
  return [BigInt(time.num), BigInt(time.den)];
}
function integer(value: number, low = 0, high = Number.MAX_SAFE_INTEGER): boolean {
  return Number.isSafeInteger(value) && value >= low && value <= high;
}

export function waveformReviewKey(project: Project, track: VoiceTrack | null, pageIndex: number, pageSize: number): string {
  return track ? JSON.stringify([project.id, project.generation, project.revision, track.id,
    track.asset_id, track.source_sha256, track.sample_rate_hz, track.channels,
    track.measured_duration, pageIndex, pageSize]) : "unavailable";
}

/** Verify the returned page against independent active-track metadata and the request. */
export function validateWaveformReviewPage(
  project: Project, track: VoiceTrack, page: WaveformPage, pageIndex: number, pageSize: number,
): void {
  const current = project.audio.voice_tracks.find(item => item.id === track.id);
  if (project.audio.active_voice_track_id !== track.id || !current ||
      waveformReviewKey(project, current, pageIndex, pageSize) !== waveformReviewKey(project, track, pageIndex, pageSize)) {
    throw new Error("Waveform source is not the current active voice track.");
  }
  if (!page || page.schema !== "motionwright.waveform-page.v1" || page.algorithm !== "sample-peak-max-abs-v1" ||
      !digest.test(track.source_sha256) || !digest.test(page.source_sha256) || page.source_sha256.toLowerCase() !== track.source_sha256.toLowerCase() ||
      page.sample_rate_hz !== track.sample_rate_hz || page.channels !== track.channels ||
      !integer(page.sample_rate_hz, 1, 0xffffffff) || !integer(page.channels, 1, 0xffff) ||
      !integer(page.frames_per_peak, 1, 0xffffffff) || !integer(page.total_frames) || !integer(page.peak_count) ||
      !integer(pageIndex) || !integer(pageSize, 1, 2048) || page.page_index !== pageIndex || page.page_size !== pageSize ||
      !integer(page.start_peak) || !Array.isArray(page.peaks) || page.peaks.length > pageSize ||
      page.peaks.some(peak => !Number.isFinite(peak) || peak < 0 || peak > 1)) {
    throw new Error("Waveform page identity, values or bounded pagination is invalid.");
  }
  const count = (BigInt(page.total_frames) + BigInt(page.frames_per_peak) - 1n) / BigInt(page.frames_per_peak);
  const start = BigInt(pageIndex) * BigInt(pageSize);
  const [num, den] = timeParts(track.measured_duration);
  if (count !== BigInt(page.peak_count) || start !== BigInt(page.start_peak) ||
      (start >= count && pageIndex !== 0) || start > count ||
      num * BigInt(page.sample_rate_hz) !== BigInt(page.total_frames) * den) {
    throw new Error("Waveform extent does not match the measured source or requested page.");
  }
  const available = count - start;
  const expectedLength = Number(available < BigInt(pageSize) ? available : BigInt(pageSize));
  if (page.peaks.length !== expectedLength || page.has_previous !== (pageIndex > 0) ||
      page.has_next !== (start + BigInt(page.peaks.length) < count)) {
    throw new Error("Waveform page is incomplete or has contradictory coverage flags.");
  }
}

export interface WaveformCandidate {
  key: string; code: "near_full_scale_proxy" | "expected_sound_quiet_proxy";
  startPeak: number; endPeak: number; start: RationalTime; end: RationalTime;
  clippedToPage: boolean; observation: string; consequence: string; proposedRepair: string;
}
export interface WaveformAnalysis {
  method: typeof WAVEFORM_REVIEW_METHOD;
  expectedSound: boolean; inspectedPeaks: number; totalPeaks: number;
  candidates: WaveformCandidate[]; omittedCandidates: number;
  loudness: "not_measured"; truePeak: "not_measured"; creativeApproval: "not_assessed";
}

export function analyzeWaveformPage(
  project: Project, track: VoiceTrack, page: WaveformPage,
  pageIndex: number, pageSize: number, expectedSound = false,
): WaveformAnalysis {
  if (typeof expectedSound !== "boolean") throw new Error("A waveform expectation must be an explicit boolean.");
  validateWaveformReviewPage(project, track, page, pageIndex, pageSize);
  const candidates: WaveformCandidate[] = [];
  const at = (offset: number) => {
    const frame = (BigInt(page.start_peak) + BigInt(offset)) * BigInt(page.frames_per_peak);
    return frame < BigInt(page.total_frames) ? frame : BigInt(page.total_frames);
  };
  const scan = (code: WaveformCandidate["code"], predicate: (peak: number) => boolean) => {
    let first: number | null = null;
    for (let index = 0; index <= page.peaks.length; index++) {
      if (index < page.peaks.length && predicate(page.peaks[index])) {
        if (first === null) first = index;
      } else if (first !== null) {
        const startFrame = at(first), endFrame = at(index);
        const longEnough = code !== "expected_sound_quiet_proxy" ||
          (endFrame - startFrame) * 1000n >= BigInt(MIN_QUIET_MS) * BigInt(page.sample_rate_hz);
        if (endFrame > startFrame && longEnough) {
          const quiet = code === "expected_sound_quiet_proxy";
          candidates.push({ key: `${code}:${page.start_peak + first}:${page.start_peak + index}`, code,
            startPeak: page.start_peak + first, endPeak: page.start_peak + index,
            start: exact(startFrame, BigInt(page.sample_rate_hz)), end: exact(endFrame, BigInt(page.sample_rate_hz)),
            clippedToPage: (first === 0 && page.has_previous) || (index === page.peaks.length && page.has_next),
            observation: quiet ? "A page-local proxy span is quiet where the user declared sound was expected." :
              "One or more quantized sample-peak bins are near full scale.",
            consequence: quiet ? "Silence may be intentional. Only this page's quantized max-absolute proxy was inspected; listening is still required." :
              "This is a peak-bin candidate, not proof of clipping, reconstructed true peak or loudness compliance.",
            proposedRepair: quiet ? "Listen to this take-relative interval and review its intended cue before proposing a change." :
              "Listen to the source at this take-relative interval and inspect gain staging before proposing a mix change." });
        }
        first = null;
      }
    }
  };
  scan("near_full_scale_proxy", value => value >= NEAR_FULL_SCALE);
  if (expectedSound) scan("expected_sound_quiet_proxy", value => value <= QUIET_PROXY_PEAK);
  return { method: WAVEFORM_REVIEW_METHOD, expectedSound, inspectedPeaks: page.peaks.length,
    totalPeaks: page.peak_count, candidates: candidates.slice(0, MAX_CANDIDATES),
    omittedCandidates: Math.max(0, candidates.length - MAX_CANDIDATES),
    loudness: "not_measured", truePeak: "not_measured", creativeApproval: "not_assessed" };
}

/** Take-relative intervals remain in the body; never fabricate a project-timeline mapping. */
export function waveformReviewChange(
  project: Project, track: VoiceTrack, page: WaveformPage,
  pageIndex: number, pageSize: number, expectedSound: boolean, candidateKey: string,
  observedRequestKey: string,
): Change {
  if (observedRequestKey !== waveformReviewKey(project, track, pageIndex, pageSize)) {
    throw new Error("Waveform review source changed after this page was read.");
  }
  const analysis = analyzeWaveformPage(project, track, page, pageIndex, pageSize, expectedSound);
  const candidate = analysis.candidates.find(item => item.key === candidateKey);
  if (!candidate) throw new Error("Waveform candidate is absent from the current bounded page.");
  const body = JSON.stringify({ schema: "motionwright.waveform-review-candidate/1", method: WAVEFORM_REVIEW_METHOD,
    source: { project_id: project.id, generation: project.generation, revision: project.revision,
      voice_track_id: track.id, asset_id: track.asset_id, source_sha256: page.source_sha256,
      sample_rate_hz: page.sample_rate_hz, channels: page.channels, total_frames: page.total_frames },
    coverage: { kind: "single_waveform_page", page_index: pageIndex, page_size: pageSize,
      first_peak: page.start_peak, inspected_peaks: page.peaks.length, total_peaks: page.peak_count,
      frames_per_peak: page.frames_per_peak, coordinate_space: "voice_take_relative",
      method: page.algorithm, units: "quantized_u16_normalized_sample_peak", omitted_candidates: analysis.omittedCandidates },
    thresholds: { near_full_scale: NEAR_FULL_SCALE, quiet_proxy_peak: QUIET_PROXY_PEAK, minimum_quiet_ms: MIN_QUIET_MS },
    expected_sound_declared_by_user: expectedSound, candidate,
    interpretation: "automated_proxy_candidate_recorded_for_human_review", confidence: "uncertain",
    loudness: "not_measured", true_peak: "not_measured", creative_approval: "not_assessed",
    effects_verdict: "not_issued", observer_admission: "not_claimed", repair_applied: false,
  });
  if (new TextEncoder().encode(body).length > 8_000) throw new Error("Waveform candidate exceeds the existing review body budget.");
  return { type: "add_review", kind: "technical", resource: "project:" + project.id,
    body, start: null, end: null, locale: null, profile_id: null };
}
