# Motionwright v0.5 — original-frame pixel contrast probe

**Scope:** MW05-E09-02 bounded visual feedback, not automatic
perceptual/creative evaluation. This augments the existing native inspection
and hand-authored scoped repair workflow; it does **not** copy or modify
the separate multimodal-analysis PR #211.

## What is measured

**Production → Pixel contrast** uses exactly the same
`planNativeInspection`/native `preview_native_frame` read-only
capability that the current Motion Canvas native review consumes.
The current scene, output profile, project ID, execution generation,
revision, source token and frame index remain the source authority.

- A user explicitly chooses **one frame** from the current native
  sample plan (maximum eight candidates). The desktop reads PNG
  bytes sequentially from the existing Semwright-granted source.
- Input is capped at **16 MiB PNG / 16,777,216 decoded pixels**.
  The parser checks signature, IHDR and declared dimensions before
  the browser decodes anything. SHA-256 is computed over an owned copy
  of the exact returned PNG.
- A downsampled thumbnail permits picking two points; keyboard users
  may instead enter foreground and background coordinates as bounded
  percentages. The original bytes remain only in memory and are never
  written to the creative project or browser storage.
- Measurement decodes the same SHA-checked original at its original
  width and height, reads just **two separate 3×3 RGBA patches** and
  computes the WCAG-defined *sRGB relative luminance contrast ratio*.
  A 3×3 neighborhood varying by more than 32 channel levels (edge,
  anti-aliasing or gradient), or any nonopaque pixel, produces an
  explicit **refusal**, not an aesthetic classification.
- Results show exact source PNG digest, coordinates, two measured RGB
  colors and the numeric ratio. **3:1 and 4.5:1 are reference numbers,
  not WCAG pass badges or text readability claims.** Neither font
  metrics nor actual glyph/background segmentation are inferred.

A new project revision, generation, scene, profile, selected frame or
expired grant discards the source bytes, URL, points and result. The
Tauri/backend boundary is still read-only; no `Change`, effect grant
for mutation, additional provider or renderer is introduced. Every
read belongs to the current `preview_native_frame` capability, not
to a fabricated DOM screenshot.

## Tests and limits

- `nativePixelContrast.test.ts`: canonical 21:1 black/white
  fixture, insufficient contrast, region instability, transparent
  pixels, invalid coordinates, 3×3-only reads and PNG header/dimension
  corruption.
- `native-pixel-contrast.spec.ts`: no-grant fail-closed UI,
  synthetic Tauri native-read source at output dimensions,
  source-bound 21:1 read/measure flow with no project mutation,
  profile change invalidation and tampered PNG refusal.
- Dedicated GitHub Actions on disposable runners runs these exact
  tests, Studio production build and Chromium; existing native
  project CI remains the source of render authority.

This is **partial** E09-02: objective local measurements are not
automated image critic judgments, aesthetic scores, optical text
recognition, actual font-size WCAG conformance, semantic contrast
segmentation, multi-scene human preference, signed provider provenance,
video playback or full-master color validation. Native WebView
decode/color conversion may differ from encoded ICC/profile semantics;
the ratio is a browser-decoded sRGB reference from the original
PNG bytes, not a calibrated display measurement.

The 60 private independent creative acceptance scenarios and installed
human review remain NOT_RUN; release authorization remains false.
