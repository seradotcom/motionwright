# Candidate dependency evidence

## Requirement and scope

This capability supports **CR-DELIVER-03** by identifying dependencies observed in
a particular Cargo or frontend build, authenticating the original source and notice
bytes, and retaining the inputs needed to bind those observations to a candidate.
It does not grant redistribution rights or declare a complete platform package.

The implementation has three separate evidence boundaries:

| Boundary | Implementation | What it establishes |
| --- | --- | --- |
| Cargo compilation | `scripts/cargo_build_inventory.py`, `scripts/candidate_dependency_capture.py` | Units actually reported by one explicit Cargo build, graph contexts and possible roles, original capture hashes, source/tree binding, and artifact byte hashes |
| Cargo source and notices | `scripts/cargo_license_sources.py` | Applicable source authentication and original license/copyright bytes; explicit unresolved coverage fails capture |
| Rendered frontend | `apps/studio/tooling/frontend_dependency_notices.mjs`, `scripts/npm_archive_authentication.py`, `scripts/candidate_frontend_authentication.py` | Rendered JavaScript module observations, Git-bound local source, locked NPM archive integrity, original notices, emitted byte hashes, and a detached authentication receipt |

These boundaries intentionally retain separate states for unverified native
libraries, installer contents, non-JavaScript dependency closure, redistribution
rights, and the final platform package. A successful unit suite or fixture build
does not change those states.

## One Cargo capture

`candidate_dependency_capture.py` accepts a real `cargo build`, not a shell
wrapper or a Tauri command. Its required CLI arguments are:

- `--repo`: the exact checkout root.
- `--expected-source`: its full 40-character HEAD SHA.
- `--cargo-home`: the existing isolated Cargo home used for this build.
- `--manifest`, `--root-package`, and `--target`: one package and one Rust target.
- `--state-directory` and `--resources-directory`: different fresh directories
  inside the checkout. Both must be absent before the invocation.
- Optional `--cargo`, `--build-cwd`, repeated `--features`, and
  `--no-default-features`.
- After `--`, the exact executable and arguments for the single Cargo build.

The admitted build must include `--locked`,
`--message-format=json-render-diagnostics`, the matching `--target`, and the
matching `--package` or `-p`. Feature/default-feature selections must match the
metadata request. `--bins`, `--release`, an explicit `--profile`, and positive
job limits are supported within the checked argument contract. Do not infer
product feature flags from these options: use the actual package manifest.

The capture sequence is:

1. Verify HEAD, tree, index, and tracked file bytes. Exact LF-to-CRLF checkout
   conversion is admitted only when the Git attributes/configuration prove that
   conversion; arbitrary text normalization is rejected.
2. Resolve and hash the actual Cargo executable, admit the build arguments, and
   create new capture/resource directories.
3. Retain original target-filtered `cargo metadata --locked` streams.
4. Execute the selected build once and retain its original streams and completion.
5. Revalidate the executable and source, derive observed units, and hash the
   reported artifacts.
6. Authenticate source and notice bytes, require a complete notice set for the
   observed Cargo components, and write the public Cargo notice resources.

Build argument `--offline` is not copied to the metadata subprocess. An offline
gate must impose that network policy explicitly in its isolated environment.
The capture does not claim complete rustc/linker/environment identity; the gate
must record those identities when the acceptance criterion requires them.

Timeout, unclosed inherited pipes, failed process reaping, nonzero completion,
changed source, redirected paths, and incomplete notice coverage are failures.
A directory from a failed or uncertain invocation is evidence to inspect, not
a destination to overwrite or a request to replay.

### Cargo metadata declarations

The inventory admits at most 128 non-development targets per package and 2048
development declarations across the entire metadata document, including packages
outside the observed runtime closure. Every example, test and benchmark is
validated and duplicate target identities are rejected. These declarations do
not count as compiled units or establish emitted package contents.

Only development metadata declarations may contain balanced parent segments in
an absolute source path. Their spelling is preserved, without filesystem
resolution or file-read authority. A parent that crosses the path anchor is
rejected. Observed target paths, manifests, artifact filenames, executables and
build-output directories retain the strict rejection of parent segments.

The global input bounds still apply before target parsing: 1,000,000 JSON nodes,
64 MiB of text, depth 32, 8192 packages and 50,000 build messages. The development
budget does not change the rejection of development artifacts in the build
stream.

### Cargo output contract

The state directory retains, among other records:

- `source-before.json` and `source-after.json`;
- `metadata.stdout`, `metadata.stderr`, `build.stdout`, and `build.stderr`;
- request/completion records for both processes;
- `cargo-components.json`, `cargo-artifacts.json`, and
  `cargo-license-sources.json`;
- `capture-complete.json` only after all required capture checks succeed.

The resource directory contains `cargo/index.json` and content-addressed original
texts under `cargo/texts/<sha256>.txt`. Public workspace identities are relative
to the source root. Private original captures may contain absolute host paths
and must not be copied wholesale into a distributable package.

`cargo-artifacts.json` is detached build evidence. The public notice index does
not itself prove that the final installer contains those same executable bytes.
That equality must be checked by the package producer/extractor described below.

A Rust GLib wrapper, a vendored Rust patch, and a native GLib library are distinct
components. Neither a Cargo source replacement nor the wrapper's license
establishes the native library version, identity, or license coverage.

## Frontend observation and authentication

The Vite/Rollup plugin exports `candidateFrontendNotices(options)`. Production
options are `sourceRoot`, `projectRoot`, `sourceSha`, optional
`packageLockPath`, and optional `additionalTrackedFiles` relative to the source
root. Candidate wiring must include the actual Vite configuration in the tracked
inputs. The plugin itself is authenticated from its own `import.meta.url`;
do not replace that identity with a caller-supplied trusted path.

The `fixture` option is reserved for explicitly synthetic tests. Candidate
builds must not enable it. Production execution requires the exact Git source,
tracked package manifest/lock, and tracked local modules.

The plugin observes rendered JavaScript modules and writes:

- `assets/licenses/frontend/index.json`;
- the original license/notice text assets referenced by that index;
- recorded hashes for the original emitted chunks and assets.

The resolved Vite public directory is included when public copying is enabled.
Its physical file roster must equal the tracked HEAD roster before the build;
an absent directory is accepted only when no tracked file is missing. Public
sources enter the tracked-input binding, and their literal copies enter
original outputs as assets, separately from Rollup's bundle entries. The
producer rejects links, source/output overlap, case and file/directory-prefix
collisions, source mutations, and missing or changed physical copies. The public
tree is limited to 4,096 entries and 16 levels; public bytes share the 128 MiB
original-output budget with Rollup. Disabled copying creates no public rows.

It revalidates the physical outputs at the write boundary. The index still marks
CSS/native/other non-chunk dependency closure and full package inventory as
`NOT_VERIFIED`. A virtual module is not authenticated as an NPM or Git source
merely because Rollup generated it.

`candidate_frontend_authentication.py` then independently authenticates the
emitted index and bytes. Its CLI requires `--repo`, `--expected-source`,
`--output-directory`, `--archive-root`, `--archive-map`, and `--receipt`.

The archive map is a JSON object mapping the index's NPM lock keys to relative
paths of original local package archives. Authentication verifies locked SRI,
safe archive members, package identities, observed module bytes, and original
notices. It binds observed local modules to the exact tracked Git bytes and
revalidates the entire emitted file/directory set. The receipt must be outside
the frontend output directory and is created exclusively.

For copied public assets, the producer records the resolved repository-relative
source directory in the source binding. The consumer checks each complete copy
declaration against its tracked source path, byte length, SHA-256 and Git blob,
then compares the declared roster with the actual HEAD roster under that
directory, including when the declared set is empty. Partial claims, chunk
claims, omitted sources and coordinated index/output changes that disagree with
Git fail. The pure verifier requires wrapper validation for this source credit;
the wrapper reports exact tracked raw-source authentication. This proves source
byte equality, not which process performed the copy.

A legacy index without public declarations receives no public-source credit.
The isolated Windows gate additionally requires the independently authenticated
bootstrap from `apps/studio/public/desktop-bootstrap.html`; removing all copy
markers cannot satisfy that gate. Its existing source preflight executes the
focused public-copy consumer cases, and both change filters include their file.

Archive retrieval belongs to the controlled candidate build. These verifiers
do not download archives. A lockfile declaration or an installed
`node_modules` directory by itself is insufficient archive authentication.

The isolated Windows helper retains the exact original index bytes and a
bounded physical file/directory observation before parsing the index or
authenticating archives. Initial and post-bundle phases use distinct create-only
records. The observation contains paths, kinds, sizes and hashes, including
unexpected physical members; it contains no asset or archive bodies. A failed
collection retains its captured index and explicitly incomplete metadata.
Index and serialized metadata are each limited to 16 MiB. The existing artifact
step exports only these named records, including on authentication failure.
This custody begins when the helper is invoked; it cannot recover originals
from a build that failed before invocation or from a prior unretained run.
The authentication receipt must bind the same original index digest.

The Vite configuration loader must preserve the physical plugin identity.
An actual build with the selected Vite version must demonstrate that
`tracked_inputs` includes the tracked plugin and configuration rather than a
temporary bundled configuration path. A unit mock of Rollup hooks is insufficient
for this integration check.

## Package integration and acceptance boundary

The existing candidate producer, resource staging, extractor, and receipt
consumer must compose these outputs. Do not create a second authority for package
identity or weaken existing source, profile, installed-root, or receipt checks.

The integration must establish all of the following for one exact candidate:

1. The expected full source SHA and tree are bound to the actual checkout, not
   merely well-formed strings with a matching version/pin.
2. Cargo metadata and compilation use the same selected package, target,
   feature set, and source. Capture the compilation that produces the packaged
   executable. A subsequent unobserved rebuild invalidates that association.
3. Frontend observation and archive authentication use the same source and the
   exact frontend bytes consumed by the native package.
4. The original project/third-party/GLib notices and generated Cargo/frontend
   notices are staged into declared package resources without overwriting one
   another. Capture output directories must be separate; the Cargo destination
   is required to be fresh.
5. The final archive/package has a detached hash and a closed member inventory.
   The extracted executable hash equals the selected captured build artifact,
   and the extracted frontend/notices equal the authenticated resource bytes.
6. Extra or missing package members, packaged caches/secrets, unauthorized
   assets, changed notices, and a source SHA mismatch fail the consumer.
7. Native shared/static libraries, runtime payloads, installer components,
   license obligations, and platform signing have their own actual identities
   and evidence. Do not inherit these from a Cargo/npm index.

The Studio Vite configuration enables frontend inventory only for an explicit
candidate build bound to a full source SHA. It loads the notices plugin through
its physical module URL and retains the normal development configuration. The
desktop bundle declares the four original notice resources. The package
consumer checks any declared Cargo build binding against the captured index and
the extracted executable bytes; a missing binding remains `NOT_VERIFIED`. These
connections do not establish an actual package/extraction join by themselves.

The pinned Tauri packaging mechanism must be verified before selecting the gate.
Do not assume that a normal Tauri build packages an earlier captured binary
without compiling again, or invent a no-build flag. Either observe the actual
single compilation used by the packaging command or demonstrate a supported
packaging-only path and verify the resulting executable equality.

The declared original-notices repair and the generated inventory complement
one another. Four static notices do not constitute a complete dependency
inventory; generated notices do not replace those original resource mappings.

## Receipt and packaging integration

### Producer and consumer binding

The existing `tooling/package_receipt.py` now accepts `--cargo-index`,
`--cargo-binary`, and `--require-cargo-binding`. The first two are a pair;
requiring a binding without both inputs fails before publishing a receipt.
Both inputs must remain inside the actual checkout. The producer verifies the
whole tracked source against HEAD, the actual Git tree, the platform target,
the desktop package identity and version, the selected binary target, and its
captured bytes before and after hashing the package.

For example, after producing a new unsigned Windows candidate:

```text
python tooling/package_receipt.py --git-sha <full-source-sha> --platform windows --arch x86_64 --artifact <new-setup.exe> --cargo-index <resources/cargo/index.json> --cargo-binary <target/x86_64-pc-windows-msvc/release/motionwright-desktop.exe> --require-cargo-binding --output <new-candidate-receipt.json>
```

Each artifact receives exactly these five `cargo_build` fields:
`index_sha256`, `source_tree`, `target`, `root_package`, and
`binary_target`. Linux's two package formats share the same captured build
binding while retaining their independent package hashes. Legacy calls without
Cargo inputs keep the existing receipt shape. A declared but invalid binding
never falls back to legacy inspection.

The producer rechecks source and binding after exclusive receipt publication.
If that final check fails, the existing uncertainty handling preserves the
new receipt and reports `manifest_output_commit_uncertain`. Inspect the retained
evidence; do not overwrite the receipt or repeat a possibly completed mutation.

Run the existing consumer on the executable actually extracted by the controlled
runner, with the exact expected source SHA:

```text
python tooling/candidate_smoke.py --candidate-receipt <candidate-receipt.json> --expected-source-sha <full-source-sha> --artifact <new-setup.exe> --installed-binary <extracted/motionwright-desktop.exe> --kind nsis --method nsis_silent_current_user --output <new-inspection.json>
```

A required Cargo/package gate must demand
`automated_inspection.cargo_build_binding.status ==
CAPTURED_ROOT_BINARY_BYTE_EXACT`. The inspection's index and artifact-roster
hashes must match the retained `capture-complete.json` record. A legacy
`NOT_VERIFIED` binding, or the overall limited inspection `PASS` alone, does
not satisfy this gate. The two reads of executable identity must also agree;
a change between header inspection and Cargo binding fails.

These APIs authenticate supplied package and executable bytes. The controlled
extraction process, its terminal result, original streams and custody must
establish that those executable bytes came from the supplied package.
The producer explicitly reports `package_contents_verification:
SEPARATE_EXTRACTION_GATE`. Its receipt does not grant installation acceptance,
platform signing, complete package inventory, or publication.

### Cargo notices in the inspected package

The consumer also verifies the resource subtree
`licenses/cargo/{index.json,texts/<sha256>.txt}` against the externally sealed
index. Its expected text set is the union of
`components[].notices[].sha256`. It derives resource names from those digests;
source paths inside component metadata never select package files.

Every referenced text must be a stable regular file with the declared SHA-256.
Missing or changed texts, extra files, extra directories (including empty
directories), and redirected paths fail inspection. Shared texts are checked
once. The namespace and index are rechecked after hashing. The index remains
bounded to 16 MiB; the installed text inspection has a separate 512 MiB
aggregate budget. These checks require an isolated extraction and do not
promise an atomic filesystem snapshot against an adversarial concurrent writer.

A required resource gate additionally demands:

- `cargo_build_binding.notice_resources.status ==
  DECLARED_CARGO_TEXTS_PRESENT_BYTE_EXACT`;
- `cargo_build_binding.notice_resources.subtree_closed == true`;
- `cargo_build_binding.notice_resources.scope == CARGO_NOTICE_SUBTREE_ONLY`.

The closure applies to this Cargo resource subtree. It does not establish a
complete package inventory, native C dependency inventory, legal approval, or
installed acceptance. Files outside that subtree remain subject to their own
checks, including the four existing static notices.

### Attestation-input compatibility

`tooling/candidate_attestation_input.py` accepts two closed receipt forms:
the original unsigned claims with four artifact fields, or those same claims
plus `package_contents_verification: SEPARATE_EXTRACTION_GATE` with a complete
five-field `cargo_build` object on every artifact in that platform receipt.
Mixed or partial forms, unknown fields and upgraded acceptance/signing claims
are rejected. The two Linux package formats must have the same Cargo binding.

The verifier compares the binding with the tree of the already verified source
commit, the expected platform/architecture target, and the desktop package and
binary names. It also continues to verify the downloaded package hashes.
It validates the index digest's form and consistency; it does not read the
extracted index or executable. Those remain inputs to the separate package
inspection gate above. The resulting attestation subject report is unchanged
for the same four packages. Allowing an optional binding does not claim global
Cargo coverage across platforms or add platform signing or install acceptance.

The owner-side provenance CLI accepts the same two closed receipt forms and uses
the same Cargo binding validator. It checks the source tree, platform target,
package/binary names and shared Linux binding before calling GitHub signature
verification. Its output keeps the existing provenance-only claims; it does not
inspect the extracted Cargo index, confer distribution admission or prove an
installed build.

Native-content review entries emitted by capture use the same public component
identity mapping as the component roster. Local Cargo package identifiers are
converted to repository-relative identities, and unresolved review references
fail capture. This avoids leaking a developer's absolute checkout location and
keeps a native-content review attached to its actual component; it does not
resolve the native review itself.

### Pinned Tauri packaging boundary

The pinned CLI is 2.12.1. Its upstream source at
[`30da1fd6e17de6107ecc850c95dfb16b5729f2dd`](https://github.com/tauri-apps/tauri/tree/30da1fd6e17de6107ecc850c95dfb16b5729f2dd)
provides a separate [`bundle` command](https://github.com/tauri-apps/tauri/blob/30da1fd6e17de6107ecc850c95dfb16b5729f2dd/crates/tauri-cli/src/bundle.rs).
That command does not call the compilation path or `beforeBuildCommand`.
It initializes the Rust interface, consults Cargo metadata, runs any effective
`beforeBundleCommand`, and invokes the bundler. This is a source inspection of
the pinned mechanism; actual packaging and extraction remain separate gates.

A direct captured Cargo build followed by `tauri bundle` requires these
conditions:

- Prepare and authenticate the production frontend before Cargo. Set
  `MOTIONWRIGHT_CANDIDATE_BUILD=1` and
  `MOTIONWRIGHT_CANDIDATE_SOURCE_SHA=<full-source-sha>` for the production
  Vite build. Bundle does not perform the build command's frontend preparation
  or validation.
- Pass the same explicit target and feature set to capture, Cargo and bundle.
  The [build setup](https://github.com/tauri-apps/tauri/blob/30da1fd6e17de6107ecc850c95dfb16b5729f2dd/crates/tauri-cli/src/build.rs)
  normally adds `tauri/custom-protocol` and `--bins`; direct Cargo must include
  them explicitly. Preserve default features for this packaging path. If
  `build.features` is configured, include that union explicitly as well.
- Share the same isolated absolute `CARGO_TARGET_DIR`. Bundle obtains its
  output directory from Cargo metadata and accepts release or `--debug`;
  it has no `--profile`, `--target-dir`, `--no-default-features`, or trailing
  Cargo-argument channel. A custom profile needs a separately validated path.
- Resolve platform configuration before compiling, including the macOS
  deployment target. Keep configuration that affects the application identical
  across the two phases. After capture, an overlay may add generated resource
  entries; it must preserve the four original notices and map the generated
  index to `licenses/cargo/index.json`, with its texts at the corresponding
  `licenses/cargo/texts/` destinations.
- Review the effective `beforeBundleCommand`, including platform configuration
  and overlays. It must not rebuild or modify the captured executable.
- Check source again after bundling. The [Rust initialization](https://github.com/tauri-apps/tauri/blob/30da1fd6e17de6107ecc850c95dfb16b5729f2dd/crates/tauri-cli/src/interface/rust.rs)
  calls [manifest reconciliation](https://github.com/tauri-apps/tauri/blob/30da1fd6e17de6107ecc850c95dfb16b5729f2dd/crates/tauri-cli/src/interface/rust/manifest.rs),
  which writes only when its resulting representation differs. This call is
  not proof that Motionwright's manifest changes. Internal metadata commands
  also lack `--locked`; any actual manifest, lock or tracked-source change
  invalidates the captured association.
- Check the captured root binary name. With the current package and absent
  `mainBinaryName`, it is `motionwright-desktop` (`.exe` on Windows).
  Inspect any newly added binary targets; `productName` does not rename it.

For an unsigned NSIS candidate, the corresponding bundle invocation is:

```text
tauri bundle --target x86_64-pc-windows-msvc --features tauri/custom-protocol --bundles nsis --config <resources-only-overlay.json> --no-sign --no-binary-patching --ci
```

Both flags exist in the pinned source. `--no-sign` alone does not disable the
bundler's executable patching. Omitting binary patching also omits the embedded
package-type information used for updater format selection; this candidate
path is not updater acceptance. Platform tools can still transform a copied
executable, so the extracted-byte comparison remains decisive. A signed or
otherwise transformed distribution needs an explicit, verified transformation
chain; do not weaken byte equality to pass it.

## Isolated Windows capture integration

The existing `windows_installed` dispatch composes these mechanisms on the
canonical integration branch. This is internal verification in a disposable
runner; it does not authorize redistribution, signing, human acceptance, Native
execution, or release publication. Legacy source receipts retain their original
scope and are not upgraded by a later run.

The job builds the production frontend with the source-bound Vite plugin, retains
the original archives for the actually emitted NPM components, and authenticates
them against the locked SRI and observed source/notice bytes. Archive intake is
limited to exact HTTPS `registry.npmjs.org` tarball URLs without credentials,
redirects, query strings or fragments. Each archive is bounded to 32 MiB and the
intake to 128 MiB. The verifier remains the existing
`candidate_frontend_authentication.py`; no receipt is reconstructed from
`node_modules` alone.

Cargo uses a fresh home and target directory, the explicit
`x86_64-pc-windows-msvc` target, `tauri/custom-protocol`, default features,
`--bins`, `--locked` and the original JSON diagnostic streams. The one captured
binary is then bundled separately with binary patching and signing disabled.
An overlay preserves the four static notice mappings and adds the exact Cargo
index/text roster. Frontend authentication is repeated after bundling against
the same original archives and output set, without rebuilding.

### Generated build metadata

The shared `--tauri-generated-schemas` producer and independent consumer have
closed profiles for `x86_64-pc-windows-msvc`, `x86_64-unknown-linux-gnu` and
`aarch64-apple-darwin`. Other targets are refused. Before captured Cargo metadata or compilation, the
home and target must be empty and `apps/desktop/src-tauri/gen` must be absent.
Cargo Git checkout root `.cargo-ok` files are observed after metadata and must
remain byte-identical through build and notice collection. They are untrusted
Cargo metadata, never Git-authenticated source or license text.

Each profile admits only `acl-manifests.json`, `capabilities.json`,
`desktop-schema.json` and one target schema under
`apps/desktop/src-tauri/gen/schemas`. Windows requires `windows-schema.json`;
Linux requires `linux-schema.json`; macOS requires the case-exact
`macOS-schema.json`. Both Unix profiles require byte equality between their
target schema and `desktop-schema.json`. The locked `tauri-build 2.7.1` and
`tauri-utils 2.10.1` generators define these exact names and desktop copies.
Each file must be a regular, bounded, strict JSON object; the namespace is
closed. Limits remain 4 KiB per Cargo marker and 4 MiB per schema, with exactly
four schemas. Source/target, Git checkout HEADs, sizes and hashes are
revalidated. No Git-ignore rule admits additional source files, and default
capture without this profile remains strict.

The Linux and macOS profiles also require
`--tauri-generated-command-permissions`. Their closed
`motionwright.linux-cargo-generated-metadata/1` and
`motionwright.macos-cargo-generated-metadata/1` records bind the literal tracked
build script, both exact generator packages and every derived permission byte.
Existing Windows `/1` and `/2` records retain their contracts. Profile support
alone does not establish a platform compilation or package join; that requires
the selected physical build, extraction and readback.

Original generated bytes are retained create-only beside the original
metadata/build streams. Their references and phase-record hashes are bound by
`tauri-generated-schemas.json`, whose digest enters the Cargo index and
`capture-complete.json`. The observation attributes appearance to build
phases; it explicitly does not assert writer-PID observation.

On Windows, the license reader joins path and descriptor observations by
comparable identity fields. It keeps complete timestamp/mode/link stamps within
each domain before and after reading, including ctime. This accommodates
Windows' distinct ctime domains without claiming writer exclusion or an atomic
filesystem snapshot.

### Installed readback

The candidate receipt requires the actual Cargo index and captured binary.
Installed inspection must report `CAPTURED_ROOT_BINARY_BYTE_EXACT`, the same
index and artifact-roster digests, and a closed, byte-exact Cargo notice subtree.
`ci_windows_dependency_capture.py verify` checks every field returned by the
canonical binary verifier and writes the bounded
`windows-cargo-binding.json` result. A legacy `NOT_VERIFIED` result cannot
satisfy this route. Cold launch and project reopening remain subsequent checks.

Before the single installation, the Windows helper derives an application-file
expectation from the exact source notices, authenticated Cargo binary/index/texts,
reconstructed resource overlay and original candidate receipt/setup. It writes
that expectation create-only; the workflow retains its SHA-256 before starting
NSIS. The record also binds the selected destination locator and the physical
identity of its existing parent. Installed readback revalidates those inputs and
uses the original installation destination, not a recursively discovered binary's
parent. A copied tree under another parent cannot substitute for that destination.

The installed check closes both file and directory names before and after bounded
hashing. Ancestor directories come from the expected paths; extra DLLs, assets,
caches, empty directories, missing members, changed bytes, hardlinks and redirected
members are refused. The inspection retains the existing 512 MiB aggregate bound,
a 4,096-entry bound and a 16-component path bound. It is a bounded observation of
the quiescent installed tree, not a filesystem-wide atomic snapshot.

Exactly one generated member, `uninstall.exe`, is declared separately. Its installed
size and digest are observed, but its reference remains
`NOT_OBSERVED_BEFORE_PUBLICATION`. That digest never becomes its own expected
identity. The result can establish exact application files and a closed inspected
member set; `all_member_bytes_authenticated` remains false and
`full_package_inventory` remains `NOT_VERIFIED`. The uninstaller's original runtime
observation, installer internals and static/native library identities remain
separate required evidence.

The source-original artifact enumerates only build observations, original
streams, generated metadata bytes, the preinstall application expectation and its
resource overlay. Payloads, installers, distribution admission records and target
caches remain inside the internal runner.
Complete package/native-library inventory, legal distribution clearance, and
human/renderer acceptance remain separate obligations.

## Isolated captured Linux packages

The existing `linux_bundle` dispatch on the canonical integration branch uses a
fresh Cargo home and target directory, without restoring or saving a Rust cache.
`tooling/ci_linux_dependency_capture.py` admits only that exact repository,
branch, event and first run attempt. It reuses the source, frontend, Cargo binary
and notice validators with explicit Linux paths; the Windows scope remains
unchanged.

The workflow authenticates the original frontend, captures one actual Cargo
build and the closed Linux generated-metadata profile, then bundles that exact
build with `tauri bundle --no-binary-patching --no-sign`. Both package rows bind
the same external Cargo index. DEB and AppImage extraction must each prove
`CAPTURED_ROOT_BINARY_BYTE_EXACT` and the closed, byte-exact Cargo notice subtree.
AppImage inspection precedes any GUI launch; the original capture comparison is
not relaxed to accept an ELF modified by the packager.

The pinned Tauri bundler invokes linuxdeploy, which sets the executable RUNPATH
to `$ORIGIN/../lib`. This capture builds that relative RUNPATH directly with
`--enable-new-dtags`, sets `NO_STRIP=1`, and selects `/usr/bin/patchelf`. Before
bundling, the helper retains the real patchelf version and command streams and
applies the same RUNPATH to a disposable copy. It requires both that copy and
the original Cargo output to remain byte-exact. Actual extracted package bytes
remain the final check; the probe alone grants no package credit.

This choice follows the pinned upstream implementations:
[Tauri CLI 2.12.1 AppImage bundler](https://github.com/tauri-apps/tauri/blob/30da1fd6e17de6107ecc850c95dfb16b5729f2dd/crates/tauri-bundler/src/bundle/linux/appimage/linuxdeploy.rs),
[linuxdeploy 07333c6 ELF operations](https://github.com/linuxdeploy/linuxdeploy/blob/07333c6e942c7d71782b66be924c5d867f9dfdfc/src/core/appdir.cpp),
and [patchelf 0.18.0 RUNPATH comparison](https://github.com/NixOS/patchelf/blob/99c24238981b7b1084313aca8f5c493bb46f302c/src/patchelf.cc).
The runner's actual executable hash, version and no-change observation are
retained separately; the cited source is not substituted for those observations.

The source-original upload enumerates bounded metadata, original streams,
generated schemas and permissions, Cargo notice resources, frontend indices and
the link-layout probe. It excludes Cargo home, NPM archives, the disposable
binary copy and target caches. Failed captures retain their originals without a
successful package claim. Native libraries, AppImage tools and the complete
package member inventory still need separate closure:
`full_package_inventory=NOT_VERIFIED`, redistribution permission is not inferred,
and Graph6/R74, human installation, signing and stable publication are unchanged.

## Isolated captured macOS package

The existing `macos_bundle` dispatch admits the canonical repository and branch,
first attempt and actual ARM64 macOS runner. It uses fresh Cargo home and target
directories and skips Rust cache restoration and saving. Both Unix capture
routes resolve `rustup which cargo` and pass that same real toolchain executable
to the metadata driver and build command. A rustup launcher alias cannot replace
the executable whose original bytes the capture authenticates.

`tooling/ci_macos_dependency_capture.py` authenticates the frontend, captures one
`aarch64-apple-darwin` Cargo build with the closed macOS profile, and stages the
exact Cargo notice overlay. It bundles that output separately with
`--no-binary-patching --no-sign --ci`. The pinned
[Tauri macOS bundler](https://github.com/tauri-apps/tauri/blob/30da1fd6e17de6107ecc850c95dfb16b5729f2dd/crates/tauri-bundler/src/bundle/macos/app.rs)
copies the application binary and skips signing when requested; actual DMG
readback must still prove the captured Mach-O bytes without relying on that
source observation alone.

The original DMG is mounted read-only and copied for inspection. The candidate
receipt binds the same external Cargo index and binary, and the helper rebuilds
that receipt from the actual archive. The inspected ARM64 Mach-O must report
`CAPTURED_ROOT_BINARY_BYTE_EXACT`; the Cargo notice subtree must be closed and
byte-exact. Source, generated profile, overlay and frontend are rechecked after
bundling. No application launch or Gatekeeper override is part of this scope.

The common candidate upload retains its existing DMG and two standard receipts.
Original streams, schemas, permissions, Cargo notices, frontend indices and the
capture binding report use a separate, explicit source-original allowlist.
Cargo home, NPM archives and target caches are excluded. This route does not
establish the complete native-library or package inventory, human installation,
renderer acceptance, signing, notarization, redistribution clearance or release
publication. Those statuses remain explicit in the resulting report.

## Regression suites

Run Python suites from `scripts/`, with temporary/cache paths confined to the
chosen test workspace:

```text
python -B -m unittest -v test_cargo_build_inventory
python -B -m unittest -v test_cargo_license_sources
python -B -m unittest -v test_candidate_dependency_capture
python -B -m unittest -v test_npm_archive_authentication
python -B -m unittest -v test_candidate_frontend_authentication
```

Run the frontend suite from the repository root:

```text
node --test --test-reporter=tap apps/studio/tooling/frontend_dependency_notices.test.mjs
```

| Suite | Expected cases | Principal regression boundary |
| --- | ---: | --- |
| Cargo inventory | 55 | Observed units, graph contexts, features, aliases, source replacements, retained real Cargo captures |
| Cargo original notices | 47 | Git/registry source authentication, native metadata paths, checkout transformations, original bytes, redirection and coverage failures |
| Cargo capture | 41 | Exact source/build scope, captured root executable identity, all-bin selection, bounded streams, subprocess completion and no replay |
| NPM archive authentication | 17 | Locked archive integrity, safe members and exact package/source/notice identities |
| Frontend authentication | 18 | Git-bound local modules and public copies, original package archives, exact output files/directories and detached receipt |
| Frontend plugin | 75 including 37 nested cases | Rendered module closure, original notices, tracked public copies, output/source mutation and production binding |

The five inventory Python suites total 178 cases. The five receipt and package
suites below add 77 cases, for 255 Python cases in this selection. A
capability-dependent skip remains a skip. In
particular, Windows accounts without symbolic-link creation permission do not
prove physical symlink rejection; the relevant cases must execute on a capable
Linux runner. Record the compiled/discovered case roster, counts, source SHA,
environment, original output hashes, and every skip reason for each execution.

Run the package-consumer suites from the repository root:

```text
python -B -m unittest -v tooling.tests.test_candidate_cargo_binding
python -B -m unittest -v tooling.tests.test_candidate_cargo_producer
python -B -m unittest -v tooling.tests.test_candidate_attestation_input
python -B -m unittest -v tooling.tests.test_candidate_license_notices
python -B -m unittest -v tooling.tests.test_candidate_smoke
```

| Suite | Expected cases | Principal regression boundary |
| --- | ---: | --- |
| Candidate Cargo binding | 12 | Exact captured executable/index identity, changes between inspections and closed Cargo notice resources |
| Candidate Cargo producer | 12 | Actual receipt producer admission, source/package changes, platform bindings and producer-to-consumer join |
| Candidate attestation input | 23 | Closed legacy/bound forms, original claims, exact Git/target/package binding and unchanged package subject hashes |
| Candidate original notices | 21 | Original resource bytes, platform layouts and link rejection |
| Candidate smoke | 9 | Inert binary-format fixtures, package metadata, launch-evidence boundaries and desktop entry checks |

These suites inspect controlled fixtures; they do not execute an installer or
prove an installed application. A Linux run must execute the link cases before
that platform selection can be credited as complete.

Retained predecessor RED results demonstrate the causes fixed by these sources.
They are not GREEN evidence for a later commit. Re-run applicable suites on the
actual composed source; a test listing without execution is not a pass.

## Provenance of the retained Cargo fixture

`scripts/tests/fixtures/cargo_inventory/` contains a portable derivation of an
actual small Cargo fixture capture, not a Motionwright application build. It has
one metadata document and eight build messages: six compiler artifacts, one
build-script result, and one successful build-finished record.

Only two original fixture-root prefixes were normalized: the Windows absolute
root to `Z:\\cargo-inventory-fixture`, and its `path+file:///` URI to
`path+file:///Z:/cargo-inventory-fixture`. There were 46 Windows-path string
replacements and 39 URI-string replacements. Object keys and all non-path
values were preserved; inverse substitution was compared structurally with the
original capture before materialization.

| Capture | Original SHA-256 | Portable fixture SHA-256 |
| --- | --- | --- |
| metadata.stdout | `e8c81c2dd4f7861c6a4bd0488fb8129b8619db92d68bdb38d0098075655ef2a6` | `35f7f316a562093d99076f919a6e66921578119e52a54ebd28981a0f576ee102` |
| build.stdout | `9d3fd56e4d18a12204b17f08ae8a9eb5be57e2475821a1abc4ca29df87dc8d54` | `f14d1ab1543b3ba4347b1cb5a33b36bc6cb9388fc800323e45dd191fe0c8349c` |

Original byte lengths were 13,570 and 7,510; portable lengths are 9,064 and 4,855.
The raw captures remain immutable in private evidence custody. The portable
files have different bytes and hashes; path-derived unit identifiers can also
differ. Their provenance must not be described as byte identity or as a fresh
Cargo execution on the current application source.

## Remaining evidence

Acceptance still needs actual selected Linux/macOS/Windows execution where
applicable, the production Vite-loader integration, the complete candidate
package/extraction join, native-library identities, and required installed
behavior. This document describes a source capability and its checks; it
does not claim those external gates, signing, legal approval, or a stable release.
