# MW05-E15-02 — trusted creative runtime doctor

Motionwright now exposes a **Run creative runtime doctor** action under
Integrations, beside the existing OS-native Semwright connection selector.
The doctor runs a read-only preflight. To copy an existing local package on a
supported host, use [Integrations → Offline runtime profiles](../offline-runtime-profiles.md).

## Installed desktop boundaries

The Tauri command creative_runtime_doctor is scoped to project ID, generation
and exact revision. It checks the generation/revision before and after the
asynchronous canonical Semwright CLI version-only probe. It then builds the
source-pinned Native SDK application operation catalogue through the actual
Motionwright service to validate local contracts, without a native render.

With a compatible CLI and a project-bound connection, the doctor reads the
authenticated Broker registry. It then queries runtime versions for connected
Motion Canvas, MLT, Blender and Manim Community providers. Registration and
version compatibility are reported separately. If a version query fails, its
result stays unobserved and the other providers can still be checked. When the
project, connection or provider generation changes during diagnosis, run a
fresh check to get a report for the current scope.

The result is a preflight. Rendering, asset byte integrity, effect authority
and installation footprint are checked by their own operations.

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

Offline runtime profiles show the package size, destination and available disk
space before an explicit copy. Rollback keeps the installed bytes in a disabled
folder. Check the supported package layouts for your host and configure the
Semwright connection separately to use an authorized provider.
