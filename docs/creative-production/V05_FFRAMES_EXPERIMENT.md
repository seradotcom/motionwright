# Motionwright v0.5 — fframes upstream renderer experiment (E04-03)

The official upstream [fframes](https://github.com/dmtrKovalenko/fframes)
is an MIT-licensed Rust/SVG/FFmpeg video framework. Its `Video` trait uses
frame-indexed SVG trees; the CPU renderer uses tiny-skia and the optional
GPU backend uses Skia/Vulkan/Metal. It includes a native CLI for frame
inspection, video rendering and audio analysis. These are **upstream
properties, not integrated Motionwright capabilities**.

## Isolated reproducible experiment

We pin the upstream exact Git SHA
`e2b552892c0373a5c8dc79de9280a3289670f27b`.
No floating `main` dependency, no user machine build and no foreign source
is admitted as a Semwright Native SDK provider. The manual-dispatch job in
`.github/workflows/v05-fframes-experimental.yml` uses a disposable
GitHub Actions Ubuntu 24.04 runner, read-only checkout,
normal `cargo build --locked` and explicit timeout/byte budgets.

The small first round exercises the upstream `hello-world-example` directly:

- source clone/checkout and current Rust/FFmpeg tool versions;
- **cold release build** of the actual upstream example, then **warm build**
  with the exact same Cargo lock and compiler;
- native command startup via `--help`;
- actual `frame 1s --scale 0.25` on first and subsequent invocations,
  recording SHA, size, raster dimensions and whether pixels repeat;
- actual one-second draft MP4 using `render 0s..1s --draft`, and independent
  FFprobe codec, duration, dimensions and SHA-256.

A separate audio fixture generates two stereo WAV inputs at 48 kHz with
distinct left and right signals. The native audio command renders two seconds
of floating-point PCM. Readback checks the sample rate, channel count, clip
offsets and gains against all 96,000 sample frames of the expected mix.

The runner measures each phase with a monotonic clock. The inputs are the
pinned upstream example and the generated stereo fixtures.

## Running and reviewing the experiment

Dispatch the workflow manually from the reviewed source ref with the admitted
public recipient and its retained SHA-256 fingerprint. Pull-request runs stop
at the custody check before building or rendering.

Use `audio-only` to recover a synthetic mix after a confirmed pre-render audio
failure. That run measures only the audio probe build and mix and records its
scope separately from any retained preview and video originals.

The publisher validates the original outputs, seals selected originals for the
designated recipient and uploads the encrypted capsule with a bounded
`public-evidence.json` receipt. The receipt includes timings and media digests
after both output validation and sealing succeed. A failed or uncertain run
reports `NOT_VERIFIED` and omits timings.

This experiment covers the headless CPU renderer and synthetic stereo mix.
Interactive GPU preview, editable-source round trips and independent creative
review require separate evidence. A comparison against HyperFrames needs
the same brief and media, 16:9, 9:16 and 1:1 projects, audio sources, and
cold and warm render timings under comparable conditions. A negative
result can support leaving FFrames outside the release.

## Release status

E04-03 remains **PARTIAL / NOT_CLOSED** until broader representative CPU/GPU,
cold/warm compile, preview, audio and same-brief renderer parity evidence is
collected. This branch does not expose `fframes` as a supported provider.
