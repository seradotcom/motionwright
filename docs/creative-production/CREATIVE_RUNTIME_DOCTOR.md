# MW05-E15-02 — trusted creative runtime doctor

Motionwright now exposes a **Run creative runtime doctor** action under
Integrations, beside the existing OS-native Semwright connection selector.
This is a read-only preflight, not the future installer. It cannot download,
modify or remove packages, create effect grants or dispatch a Broker action.

## Installed desktop boundaries

The Tauri command creative_runtime_doctor is scoped to project ID, generation
and exact revision. It checks the generation/revision before and after the
asynchronous canonical Semwright CLI version-only probe. It then builds the
source-pinned Native SDK application operation catalogue through the actual
Motionwright service to validate local contracts, without a native render.

The report separates source/project validity, Native SDK contracts and
digest-checked CLI version compatibility from the following NOT_TESTED gates:
Broker session and effect grants, Driver Host renderer discovery, content-
addressed asset byte integrity, installation footprint and rollback,
source media rendering and independent creative acceptance.

Even when the CLI version matches, the overall result is PRE-FLIGHT ONLY.
The backend never reports ready-for-native-production from this diagnosis.

## Project-specific facts and limitations

The source registry covers Motion Canvas, MLT, Blender and Manim Community.
Optional Remotion and ManimGL descriptors can be present and owner-enabled
with cleared rights. The doctor never interprets a descriptor as installed
software, verified rights or a working native provider.

For real output profiles, the doctor reports per-frame raw RGBA bytes
computed as width multiplied by height multiplied by four. This is not a
download size, package footprint, storage reservation, RAM benchmark or
export file size.

The deterministic report includes project ID, generation, revision, the
Semwright SDK source pin, bounded status codes and renderer counts. It never
exports owner session data, private connection paths, CLI diagnostics,
Broker socket names or grant tokens.

Browser demo provides only project-authored metadata, with BROWSER ONLY and
all native evidence checks NOT_TESTED. The demo Native SDK pin is kept equal
to SOURCE_LOCK.json and not mistaken for a live connection.

## Validation and remaining v0.5 scope

Rust tests cover stale revision/generation, invalid projects, known and
unknown CLI status, declared renderer routes and opt-in admission, immutable
source state and redacted serialization. Browser tests cover renderer
counts, unknown status and actual UI interaction. GitHub Actions verifies
the native crate and desktop command integration on Windows.

A future real installer requires explicit owner-selected profile,
destination and exact package size, signature verification, available disk
space and transactional rollback. None of this is performed here.
MW05-E15-02 remains partial; human acceptance remains NOT_RUN.
