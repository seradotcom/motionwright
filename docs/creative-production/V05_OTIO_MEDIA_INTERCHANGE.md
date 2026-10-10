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
not flattened into a false OTIO claim. An importing NLE may require
relative-source relinking, which is a separate compatibility test.

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
The tested OTIO and media are available as a source-bound CI artifact.

This proves file/source identity and OpenTimelineIO-reader compatibility,
**not** that Kdenlive/Shotcut/Resolve or another particular NLE imports and
renders the exact same result. It also does not prove that a reviewed
preview can be automatically committed or all Motionwright creative
semantics round-trip. Those require separate owner-authorized tests and
may incur explicit losses, particularly audio/transcript/camera/effects.

**Acceptance:** E11-03 remains `PARTIAL`, with actual external editor
import/round-trip, edited clip semantic mapping and human visual review
still `NOT_RUN` until demonstrated on the same source revision.
