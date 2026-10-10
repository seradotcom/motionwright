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
is admitted as a Semwright Native SDK provider. The scoped branch-push/manual-dispatch job in
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

Durations are measured per phase with monotonic time on the **same runner**.
All media is generated from the *original upstream example*; no owner product
screens, licensed fonts, private recordings, previous browser or unreviewed
marketing sources are distributed.

## Explicitly incomplete criteria

This is an **experiment, not a migration/adoption**. The first hello-world
fixture has **no narration/music**, so real audio mixing/loudness remains
`NOT_RUN_NO_AUDIO_IN_HELLO_FIXTURE`. The interactive GPU preview requires
a display/GPU not available in normal headless CI; its result remains
`NOT_RUN_HEADLESS_CI`. The source code is not projected to Motionwright
Project/Native SDK source; no editing round trip or Semwright Driver Host
execution is tested. We must use comparable 16:9, 9:16 and 1:1 projects,
one identical brief and media set, a true professional HyperFrames direct
baseline, audio sources, and both warm/cold render timing before any
performance or fidelity comparison is credible.

**Important:** Not even a fast fframes result would justify replacing the
existing Motionwright service, render scheduler or native authoring
contracts. A negative result is valid evidence for *not* adopting fframes.
Independent human creative review is required before release, and this
experiment does not select an engine automatically.

Run manually from GitHub Actions by dispatching the workflow against its
exact feature SHA. A failing job must upload its `result.json` with
`EXPERIMENTAL_UPSTREAM_SOURCE_BLOCKED` rather than turning missing artifacts
into green gates.

## Release status

E04-03 remains **PARTIAL / NOT_CLOSED** until broader representative CPU/GPU,
cold/warm compile, preview, audio and same-brief renderer parity evidence is
collected. This branch does not expose `fframes` as a supported provider.
