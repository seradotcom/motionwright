# Delivery profiles, variants and portable output

Motionwright stores delivery intent inside the versioned creative project. A deliverable profile is not a rendered artifact: it is the exact creative and technical contract that a later Semwright-backed production operation must satisfy.

Each profile versions frame dimensions, frame rate, locale, caption policy, framing strategy, selected voice take, localized canvas text, narrative cut, protected scenes, video/audio codecs, sample rate, color space, container, brand/cut labels and adaptation notes. Derived profiles retain the parent profile ID and source revision so the original remains independently inspectable.

## Aspect-ratio variants

Alternate aspect ratios default to **replan**, not crop. Canonical Film projects the semantic scene into the selected output dimensions and checks the resulting safe area before driver dispatch. Crop is a separate framing strategy and is invalid until the profile records explicit crop approval.

A vertical or square profile therefore does not silently stretch a 16:9 render or hide content off-frame. Unsupported geometry fails closed and requires an authored adjustment.

## Localization and voice timing

A locale derivative is a real variant, not a relabeled export:

- every canvas text object must receive an explicit localized override when the locale differs from its parent;
- a parent-language voice take cannot be reused as the localized voice;
- captions are selected from the profile's bound voice track;
- UNKNOWN transcript timing never becomes caption evidence;
- timing_locked rejects a locale voice whose measured duration differs from the parent voice, rather than speeding it up or stretching the project invisibly;
- native text projection carries the locale and a no-truncation verification constraint. Long text that does not fit must be adapted or re-authored; it is never clipped as a hidden fallback.

Timing can be deliberately unlocked for a derivative that will be re-timed in project state. The profile does not invent the re-timing on its own.

## Narrative cuts

A profile may select an ordered subset of project scenes. Selected scenes must preserve the approved project order. Profiles can additionally mark scenes as protected; a custom cut that omits a protected scene is invalid.

Canonical Film reflows a valid selected cut onto a zero-based output timeline without changing the source project sequence. Caption sidecars use the same cut: transcript segments from omitted scenes are removed and retained cues are rebased to the cut timeline. A transcript segment that crosses a selected scene boundary blocks export until it is re-aligned, rather than being clipped silently. The source scenes remain available in the parent/original project state.

## Captions

The desktop application can write WebVTT and SubRip sidecars from the selected profile voice transcript. Caption export is deliberately fail-closed:

- transcript timing must exist for the selected voice;
- every exported segment must have manual or measured alignment evidence;
- multiple transcript tracks require an explicit voice selection;
- UNKNOWN alignment blocks export instead of becoming invented timecodes;
- rational timestamps are converted to milliseconds without floating-point time arithmetic;
- the destination must be absolute and its parent directory must already exist;
- Motionwright creates a new file and refuses to overwrite an existing file.

A profile can also request caption burn-in. Dependency fingerprints distinguish the two cases: a sidecar-only wording change invalidates caption evidence but does not invalidate source frames; a burn-in wording change invalidates frame inputs.

## Dependency fingerprints

Motionwright computes bounded SHA-256 fingerprints for frame inputs, caption inputs, audio inputs, mastering inputs and the complete profile. This allows downstream caches and receipts to reject stale work without invalidating unrelated stages.

Mastering settings include codec, sample rate, frame rate, color space and container. Changing those settings changes the mastering/profile fingerprints. Native production still fails closed when the pinned driver path does not support the requested combination.

## Portable project delivery

The portable project format is a directory bundle, not an opaque media archive. Its manifest carries the complete project backup/journal and the exact digest, size and path of every referenced immutable blob. Import validates the bundle before creating project authority, rejects symlinked roots/files/path components and enforces bounded blob count/aggregate size in addition to digest checks.

Variant lineage, localization text, cut selection and delivery profiles travel as project semantics. Execution request receipts and owner connection material are not restored with a project.

## Build delivery evidence

CI can generate an exact-source delivery manifest with tooling/release_manifest.py. That manifest binds the tested Motionwright SHA to the Semwright Native SDK pin and hashes the Rust/npm lockfiles, source lock, AGPL license and third-party notices. It is uploaded as evidence by the Security and Delivery workflow; it is not a signing, notarization or publication claim.

The dedicated Candidate Packages workflow builds release-shaped desktop bundles only on disposable GitHub-hosted runners:

- Linux produces an AppImage for user-local use plus an optional Debian package.
- Windows produces an NSIS installer with `currentUser` install mode and downgrade protection enabled.
- macOS produces a DMG candidate without claiming signing or notarization.

`tooling/package_receipt.py` rejects missing, empty, symlinked or unexpected bundle types and records the exact source SHA, pinned Semwright SHA, OS/architecture, file size and SHA-256 for every candidate artifact. Each receipt explicitly leaves publishing, distribution signing, notarization and human install acceptance unclaimed.

Candidate artifacts are CI evidence, not releases. The workflow does not create a tag, GitHub Release, updater feed or automatic publication. Linux AppImage is the default no-root path; the Debian package is an explicit system-package alternative. Windows NSIS is fixed to current-user installation rather than relying on an implicit default. macOS users retain normal platform protections; no Gatekeeper bypass or ad-hoc "fix" is part of the product instructions.

Renderer/runtime prerequisites remain independently owned. Motionwright does not silently install Semwright or renderer runtimes. In the desktop app, Integrations performs a bounded read-only compatibility check against the owner-provisioned Semwright connection: the executable digest is revalidated, the exact binary is queried with `--version`, and the observed version is compared with the `SOURCE_LOCK.json` version. This is compatibility evidence only; it is not signing, notarization or source-revision attestation.

## Media delivery

Configuring a profile never creates a media-success claim. The current direct pinned MLT final-master path is deliberately narrower than the profile model and accepts H.264/AAC, 48 kHz, Rec.709, MP4 only; other combinations remain versioned intent and fail as unsupported until a native production path proves them.

The desktop can now invoke the existing native MLT master over exactly one verified Motion Canvas segment plus a measured, SHA-256-bound 48 kHz stereo WAV voice track from the saved delivery profile, with exact rational timing and no arbitrary input paths. [Desktop native AV mastering](desktop-av-master.md) describes the owner-authorized staging, fail-closed constraints, evidence and still-open limitations. When the canonical MP4 is complete, [verified local MP4 delivery](master-verified-export.md) copies its exact bytes to a new absolute destination, with independent SHA-256 verification and no overwriting. For shorter MP4s (16 MiB or less), [native in-app AV review](native-av-review.md) can play the same SHA-256-verified source through the local WebView decoder without a user-path export, when H.264/AAC is supported by that WebView.
