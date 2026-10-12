/** Scheduling and bounded byte caching for EXISTING, source-verified native PNGs.
 * No renderer, image synthesis, frame interpolation, or Broker authorization.
 */
export const SCRUB_SAMPLE_INTERVAL_MS = 250;
export const SETTLE_EXACT_DELAY_MS = 120;
export const MAX_PREVIEW_CACHE_BYTES = 32 * 1024 * 1024;
export const MAX_PREVIEW_CACHE_FRAMES = 3;
export const MAX_NATIVE_PNG_BYTES = 32 * 1024 * 1024;

export type PreviewTier = "sampled_scrub" | "settled_exact";
export type ReadDecision = "cache" | "fetch" | "hold";
export type PreviewReadPolicy = {
  desiredFrame: number;
  showingFrame: number | null;
  playing: boolean;
  inFlight: boolean;
  failedFrame: number | null;
  cacheHit: boolean;
  nowMs: number;
  targetChangedAtMs: number;
  lastRequestStartedAtMs: number;
};

export function previewTier(playing: boolean): PreviewTier {
  return playing ? "sampled_scrub" : "settled_exact";
}
export function planNativePreviewRead(policy: PreviewReadPolicy): ReadDecision {
  const {
    desiredFrame, showingFrame, playing, inFlight, failedFrame, cacheHit,
    nowMs, targetChangedAtMs, lastRequestStartedAtMs,
  } = policy;
  if (!Number.isSafeInteger(desiredFrame) || desiredFrame < 0 ||
      !Number.isFinite(nowMs) || !Number.isFinite(targetChangedAtMs) ||
      targetChangedAtMs > nowMs) return "hold";
  if (showingFrame === desiredFrame) return "hold";
  if (cacheHit) return "cache";
  if (inFlight || failedFrame === desiredFrame) return "hold";
  if (playing) {
    return nowMs - lastRequestStartedAtMs >= SCRUB_SAMPLE_INTERVAL_MS
      ? "fetch" : "hold";
  }
  return nowMs - targetChangedAtMs >= SETTLE_EXACT_DELAY_MS
    ? "fetch" : "hold";
}

/** Scope this class to one project/generation/revision/grant-token lifetime.
 * Holds PNG bytes only; caller owns/revokes each generated object URL.
 */
export class NativePreviewByteCache {
  private entries = new Map<number, Uint8Array>();
  private total = 0;

  get(index: number): Uint8Array | null {
    const bytes = this.entries.get(index);
    if (!bytes) return null;
    this.entries.delete(index);
    this.entries.set(index, bytes);
    return bytes;
  }
  has(index: number): boolean {
    return this.entries.has(index);
  }
  put(index: number, bytes: Uint8Array): boolean {
    if (!Number.isSafeInteger(index) || index < 0 ||
        bytes.length < 8 || bytes.length > MAX_PREVIEW_CACHE_BYTES) return false;
    // Always own the bytes, never permit the caller to mutate cached evidence.
    const copy = new Uint8Array(bytes);
    const prior = this.entries.get(index);
    if (prior) {
      this.total -= prior.byteLength;
      this.entries.delete(index);
    }
    while (this.entries.size >= MAX_PREVIEW_CACHE_FRAMES ||
           this.total + copy.byteLength > MAX_PREVIEW_CACHE_BYTES) {
      const oldest = this.entries.keys().next().value;
      if (oldest === undefined) break;
      this.total -= this.entries.get(oldest)!.byteLength;
      this.entries.delete(oldest);
    }
    this.entries.set(index, copy);
    this.total += copy.byteLength;
    return true;
  }
  delete(index: number): void {
    const bytes=this.entries.get(index);
    if(bytes){this.total-=bytes.byteLength;this.entries.delete(index);}
  }
  clear(): void {
    this.entries.clear();
    this.total = 0;
  }
  get size(): number { return this.entries.size; }
  get bytes(): number { return this.total; }
}

/** PNG already passes a backend source/revision/grant check.
 * The WebView also refuses unbounded or malformed payloads before Blob decode.
 */
export function requireBoundedNativePng(bytes: Uint8Array): void {
  const signature = [137,80,78,71,13,10,26,10];
  if(bytes.length < 32 || bytes.length > MAX_NATIVE_PNG_BYTES ||
    signature.some((value,index)=>bytes[index]!==value)) {
    throw new Error("Native preview returned invalid or unbounded PNG data.");
  }
}
