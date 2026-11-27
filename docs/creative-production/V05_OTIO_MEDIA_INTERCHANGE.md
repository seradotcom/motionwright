# Motionwright v0.5 — OTIO with real media (bounded profile)

**SRS:** `MW05-E11-03 — OTIO con medios reales`. Motionwright already owns
`otio_interchange(Project)` in `crates/domain/src/delivery.rs`, which
intentionally exports abstract scenes as `MissingReference.1`. The new
`tooling/otio-media/bridge.py` **extends that exact export**, instead of
rebuilding the timeline or inventing media attached to a scene.

## 1. Explicit, owner-checked portable export

The exporter requires the current original Project JSON, the OTIO produced by
the existing Motionwright domain export at the *same generation/revision*,
and a separate scene-media declaration authorized by the owner. It validates:

- Each Scene ID in the existing OTIO matches the original Project and has a
  contiguous exact-time duration expressible at 24 fps.
- Every owner-specified media asset exists in the original project ledger
  with the same asset ID, MIME identity and original SHA-256.
- The authorized, redistributable source MP4 bytes still hash exactly to
  that SHA. ffprobe independently confirms **real** H.264, constant exact
  24/25/30/60 fps, counted video frames, legal dimensions and enough source
  frames to cover the requested original scene interval.
- The requested frame offset is bounded by the actual source clip; it cannot
  silently create a video frame or substitute a short source.
- Clips **without** an explicitly verified owner binding retain their
  `MissingReference`. Missing bytes on a *declared* source cause an error
  rather than a fallback to synthetic footage.

Output is a new directory containing `timeline.otio`, original
`source-project.json`, `bridge-manifest.json`, a `media/` directory of
original SHA-named MP4 files, and a deterministic `portable-cut.zip`.
OTIO `ExternalReference.1` entries use portable, relative media paths
(with source `available_range`, exact owner start and duration frames).
The original Motionwright timeline, camera, editable object hierarchy,
audio, history, locks and approvals are retained **only** in
`source-project.json` and reported explicitly as non-portable properties,
not flattened into a false OTIO claim. A structured **12-property fidelity
report** is present both in the OTIO metadata and in the independent
`bridge-manifest.json`: scene order/duration, media references/in-points,
unbound sources, native object semantics, camera/effects, audio, captions,
locks/reviews, runtime authority and publication rights. NLE import preview
rejects a cut that silently drops/changes this source loss disclosure.
An importing NLE may require relative-source relinking, which is a separate
compatibility test.

Run after obtaining a true native `export_otio` and a current Project JSON
from the same saved project revision:

```bash
python3 tooling/otio-media/bridge.py export \
  --root /path/to/owner/source \
  --request media-bindings.json \
  --output /path/to/NEW-portable-otio-export
```

`media-bindings.json` is scoped and reviewed. Example placeholders (NOT
valid ready-to-use signatures):

```json
{
  "schema": "motionwright.otio-portable-media-bridge/1",
  "project": "project.json",
  "otio": "conservative-export.otio",
  "bindings": [{
    "scene_id": "<existing scene UUID>",
    "asset_id": "<existing project asset UUID>",
    "file": "media/original-source.mp4",
    "sha256": "<exact lower-case 64-character SHA-256>",
    "source_start_frame": 0,
    "rights": {
      "owner": "Verified original media owner",
      "license": "Explicit first-party source redistribution permission",
      "authorized": true,
      "redistributable": true
    }
  }],
  "owner_review": {
    "reviewer": "Owner-approved source reviewer",
    "synthetic_source_allowed": false
  }
}
```

This is a current, intentionally narrow profile. It does not automatically
relicense a third-party clip, grant NLE execution permission, copy paid
fonts/browser runtimes, fetch URLs, submit a render or publish to a channel.

## 2. Bounded cut import preview

After a video editor has saved a revised `edited.otio` in the exported
directory, use a **fresh Project JSON snapshot** of the project that will
receive the edit:

```bash
python3 tooling/otio-media/bridge.py preview-import \
  --bundle /path/to/NEW-portable-otio-export \
  --edited edited.otio \
  --current-project /path/to/CURRENT-motionwright-project.json \
  --output /path/to/new-import-preview.json
```

The preview revalidates every packaged media SHA, video length, original
project digest, generation/revision and actual OTIO clip ID. It rejects
foreign clips, deleted/duplicated scenes, arbitrary NLE effects, extra
tracks, missing-media invention, in-point changes that cannot be expressed
by the current native Scene and changed media references. It accepts only
exact source-rate **end trims** and scene reordering that map directly to
existing `MoveScene` and `SetSceneDuration` Change types.

The result is an explicit, ordered list of proposed native changes, together
with an inherited per-property loss report. Nothing is committed: the
current Motionwright project may have locks, protected branches or creative
approvals that require owner review and the canonical StudioService CAS
revision to apply a change. A preview is not permission for an agent to
rewrite approved content.

## 3. Native CI and receiver limitations

The disposable `v05-otio-real-media` CI gate compiles the **existing Rust
OTIO exporter**, generates an original H.264 video with ffmpeg, binds two of
three scenes to its real SHA-verified bytes, then uses the independent
**OpenTimelineIO 0.18.1 Python adapter** to read actual ExternalReference and
MissingReference objects. It verifies native video stream information with
ffprobe, writes and reopens an edited OTIO cut, and validates its bounded
reorder/end-trim import preview. It also demonstrates the negative case:
deleting the original media refuses import rather than inventing a clip.
After successful exact-source CI verification, a **public synthetic-only artifact**
may include `timeline.otio`, exactly one matching content-addressed
`media/<sha256>.mp4`, and a minimal SHA-256/status manifest. The complete
portable folder (including `source-project.json`, owner review, extra input
OTIO, private bundle metadata and edit proposal) stays in the disposable CI
workspace and is **never** uploaded wholesale. If any verification fails or
has an unknown result, only a `NOT_VERIFIED` public manifest is emitted.
Publication neither retries media generation nor authenticates third-party
rights, a human review, or an actual external-NLE round-trip.

This proves file/source identity and OpenTimelineIO-reader compatibility,
**not** that Kdenlive/Shotcut/Resolve or another particular NLE imports and
renders the exact same result. It also does not prove that a reviewed
preview can be automatically committed or all Motionwright creative
semantics round-trip. Those require separate owner-authorized tests and
may incur explicit losses, particularly audio/transcript/camera/effects.

**Acceptance:** E11-03 remains `PARTIAL`, with actual external editor
import/round-trip, edited clip semantic mapping and human visual review
still `NOT_RUN` until demonstrated on the same source revision.

## 4. Desktop export, preview and owner-approved save

Open **Deliver → OTIO cut review** in Desktop. Media verification needs an
administrator-managed Python 3 and ffprobe installation on the same host.

- **Linux** uses the system Python 3 and ffprobe under `/usr/bin`.
- **Windows** uses `Motionwright\OTIO Runtime` in the system Program Files
  directory. Provision the embeddable CPython distribution for the installed
  architecture and a standalone `ffprobe.exe`. Keep the versioned Python DLL,
  standard-library ZIP and `._pth` file together. The active `._pth` entries
  are the versioned ZIP and `.`. The trusted owners are LocalSystem,
  Builtin Administrators and TrustedInstaller. Standard users need read
  and execute access to the installation.
- **macOS** uses `/Library/Application Support/Motionwright/OTIO Runtime`.
  Provision a self-contained Python 3 distribution and ffprobe for the
  installed architecture, with regular executable files at `bin/python3`
  and `bin/ffprobe`. Keep Python's standard library inside this installation.
  The runtime and its parent directories must be root-owned and protected
  against group or world writes. Any extended ACL entries must deny access.

MP4 and MOV originals with their index after the media data require a build of
ffprobe with the seekable `fd` protocol. Check the installed decoder with
`ffprobe -protocols`. The Desktop bridge uses up to 256 MiB of temporary space
for one read-only copy of such an original.

Use runtime packages from a trusted distribution channel. Motionwright checks
the installed paths and permissions before using the one-time local grant. A
missing or unsafe installation leaves the operation available for a manual
attempt after the configuration is corrected.

1. Choose a new export folder, bind saved project scenes to their original
   MP4 assets and source frames, and declare the media owner and license.
   Confirm permission to copy and redistribute the selected media, then use
   **Export original media-bound OTIO bundle**. The desktop exports the actual
   saved Project and verifies original media through its embedded bridge.
2. Save the bounded edited `.otio` inside that exported folder. Use
   **Choose OTIO bundle folder** or enter its absolute path. Enter the edited
   filename, then use **Preview edited OTIO
   with verified media**. This rechecks the source Project, revision, media
   hashes and supported edits. Preview does not modify the project.
3. Review the proposed scene order, end trims and twelve property-loss
   classifications. Declare the reviewer, explain the change and acknowledge
   the losses and any original absolute audio, transcript or marker positions.
   Confirm the reviewed cut to save one atomic `Change::ApplyOtioOwnerCutReview`
   through the existing StudioService and SQLite/CAS.
4. Reopen the saved project to continue editing. Locks, protected reviews,
   stale generations and late invalid trims refuse the complete change.
   Increasing an original scene duration is refused; only end trims and scene
   reordering are supported.

### Returning original Kdenlive OTIO exports

Select **Original and edited Kdenlive OTIO exports** under **How the cut was
exported** when Kdenlive does not retain Motionwright metadata. After importing
the exported `timeline.otio`, export the unchanged cut as `received.otio`, then
make the supported edits and export `edited.otio`. Keep both original files
inside the selected bundle and keep every source media reference inside that
bundle. Preserve `timeline.otio` and the source manifest unchanged.

The existing native cut reader checks both files against the saved Project and
all original media. It requires a unique media SHA, frame rate and original
in-point for every scene. It refuses missing clips, ambiguous source instances,
foreign media, moved in-points, added tracks or unsupported effects. Only exact
scene reordering and end trims become a proposal. The initial cut must still
have the complete original scene order and duration.

The review retains the initial and edited file hashes in the same owner Change
that is saved to the journal. It does not relink assets, authenticate an editor
process, approve visual fidelity or import external permissions. Review every
property loss and explicitly save the proposed change through the ordinary
project revision controls. An inspection selection is attempted once in the
open review workbench; unknown results are not replayed.

### Installed runtime

The DEB declares `python3` and `ffmpeg` as dependencies; ffmpeg supplies ffprobe.
Install the DEB through a dependency-resolving package manager. The AppImage
also requires these system packages on its Linux host. The application embeds
its first-party bridge modules and needs no manual Python script setup or
third-party Python package installation. On Linux it runs the protected system
tools under `/usr/bin`. On Windows and macOS it uses the protected installations
listed above. Missing tools or writable runtime paths refuse export and
inspection before execution.

The Desktop source checks run synthetic H.264 export and import on Windows
and macOS, including originals whose index follows the media. A previously
generated JSON proposal can also be opened by selecting the proposal file or
pasting its contents in **Review an existing bridge proposal**. Reviewing
the declaration alone does not authenticate the media or external editor.

The export is published once into a previously unused folder after verification;
existing directories and dangling symlinks are never replaced. An interrupted
or unconfirmed export is not retried automatically. Inspect the selected
folder before deciding what to do next.

An external NLE import/render, installation qualification and real human review
remain separate acceptance observations. The workflow does not launch an NLE,
relink media, move external audio tracks, grant publishing rights or infer
third-party licenses. See [Owner-reviewed OTIO cut commit](V05_OTIO_OWNER_CUT_COMMIT.md).
