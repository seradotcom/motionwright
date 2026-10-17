# Reproducible reference renderer protocol (not a superiority claim)

The independent Remotion fixture is a deterministic 1920x1080, 30/1 fps, 60-frame composition with two ordinary shapes and no fonts, assets, audio or remote calls. The package is exactly pinned to Remotion 4.0.536, @remotion/cli 4.0.536, React 19.2.0 and React DOM 19.2.0, with the actual npm lock and SHA512 integrity captured by npm ci.

Compile/read back the source and independently verify 60 decoded output frames, their timestamps and the x pixel progression (256 to 1280 in fixed steps); no target output may be substituted after measurement. Use two new process runs for cold/warm with the same browser/renderer CPU and output encoding settings; retain both full logs and resource usage, package/binary hashes, browser version, preset, threads, frame PTS and source SHA. Do not reset/remove untrusted caches to improve results.

Motionwright must render an equivalent registered Film and pass the same pixel/clock/audio obligations before any comparison. This fixture by itself is **not** a Motionwright vs Remotion benchmark. An optional Remotion package merely existing does not supply a production-ready alternative renderer. No claim of faster render, cheaper generation, editable source parity, quality or customer acceptance is justified until the same creative inputs, pre-registered tolerances and original measured renders are compared.

For 'revision ten' record the exact ten semantic source edits and the equivalent ten persisted Motionwright Project changes. Source rebuild/editing and output validation should be measured as separate phases; don't compare 10 Motionwright SQLite commits to 10 Remotion full video renders as if the operations were equivalent. Human creative quality and installed-system usability remain NOT_RUN.


## Execution protocol

Use a fresh disposable runner with the exact source checkout and package lock.
Node 22 or newer and the pinned dependencies are required; install with:

    npm ci --ignore-scripts --no-audit --no-fund

Capture the full npm/Node, browser, FFmpeg, CPU and RAM identities; the
lockfile hash; the source SHA; and output/timing observations. The composition
entry point is src/index.jsx and its id is StaticTwoLayerBaseline.

    ./node_modules/.bin/remotion render src/index.jsx StaticTwoLayerBaseline remotion-baseline.mp4 --codec h264 --concurrency 1

If the runner needs Chromium, install it within runner-local scratch and
record its exact identity. Preserve original run exit codes; do not silently
replay an uncertain render. Independently read every decoded output frame,
PTS, its pixel content, file bytes and audio absence. Source-only or
aesthetic claims without physical output must remain NOT_RUN.

This is a competitor-controlled reference, not the required Motionwright
12-scene/300-object S profile and not a matched quality comparison.
Claim no rendering-speed, cost or audiovisual superiority until a genuinely
equivalent Film/output, hardware, codec, revisions and human evaluation
are objectively reproduced with declared tolerances.
