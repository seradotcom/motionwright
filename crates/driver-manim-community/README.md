# Motionwright Manim Community Driver

This crate is the bounded Semwright Application Driver for Motionwright's Manim Community renderer.

The driver accepts only typed Motionwright scene plans. It never accepts Python source, shell fragments, executable paths, arbitrary command-line arguments, or network authority from a model or project document. The fixed-vocabulary compiler is owned by `motionwright-manim-profile`. Driver Host launches only the owner-pinned `motionwright-manim-runner`, which receives exact Host-materialized Python and FFmpeg dependencies and a read-only Manim runtime bundle.

## Runtime contract

- Semwright Driver Protocol: v8
- Runtime: Manim Community 0.21.0
- Renderer: Cairo
- Network: disabled
- Random seed: 0
- Output: MP4 under the owner-granted `manim-output` root
- Working source/cache: generated under the owner-granted `manim-work` root
- Python packages: read-only `manim-runtime/site-packages`, with a version receipt checked by the runner
- Python and FFmpeg: separate SHA-pinned Host dependencies
- Fontconfig: read-only owner grant, explicitly selected by the runner
- Retained jobs: 32; the driver fails closed when the budget is full rather than deleting prior evidence

The runtime runner never searches `PATH` for Python or FFmpeg. Driver Protocol typed `ToolPath` arguments supply both exact dependencies. The runner clears the inherited environment, fixes `PYTHONPATH` to the delegated runtime bundle, disables user-site imports and bytecode writes, and probes `manim.__version__` before rendering. Its Python `-m manim` invocation and all Manim CLI options are runner-owned constants or bounded render-profile values; project/model data cannot select code or an executable.

## Owner manifest

`driver.manifest.example.json` is documentation, not an install manifest. Operators must:

1. build the driver and runtime runner;
2. provide exact absolute owner-approved paths;
3. pin the driver, runner, Python and FFmpeg SHA-256 values;
4. provide a read-only runtime bundle whose `runtime.json` declares Manim Community 0.21.0 and whose `site-packages` are provisioned independently of the driver;
5. map `manim-work` and `manim-output` as writable owner roots;
6. validate and conformance-test the manifest with the exact Semwright revision in repository `SOURCE_LOCK.json`.

No production configuration may depend on an unversioned PATH lookup, a user Python environment, model-authored source or network installation at render time.

## Capabilities

- `driver.manim-community.doctor`
- `driver.manim-community.render.start`
- `driver.manim-community.render.status`
- `driver.manim-community.render.cancel`
- `driver.manim-community.render.result`

Render jobs are owned by Driver Host. Motionwright's application coordinator remains responsible for project revision binding, receipts, Broker provenance checks, plan/artifact digest verification and final delivery semantics.
