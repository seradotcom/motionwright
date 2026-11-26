# Studio project and creative continuity

Studio's desktop library opens stored projects and creates an empty project with
its own identity. Use **Open project library** in the command bar, enter a title
and choose **Create project**, or open an existing row. The browser demo remains
explicitly identified and does not provide desktop persistence.

## Start with the editable example

Choose **Create editable motion example** in the project library. The optional
title field names your copy; an empty field uses **Motion study**. The bundled
example contains two scenes and six seconds of editable rectangle motion, a
640 x 360 output at 30 fps, and saved narrative intent. Canvas provides a local
semantic preview. Portable project export preserves the editable source.
Native rendering uses the explicit runtime setup in Integrations.

The versioned selection is part of the one-time creation grant and retained
request. The complete example and its original receipt commit in one SQLite
transaction. A lost response uses **Check creation result** to recover that
same copy and open its current saved revision. Recovery never rebuilds the
example over later edits. Existing blank-project requests remain compatible.

## Create, save and reopen

The remembered startup selection is a convenience; the saved Project and its
journal are the source of truth. Opening a row checks that the stored document
actually belongs to that row. An unreadable or mismatched document produces an
error without substituting another project. The library stays usable so that a
different, healthy project can be opened.

Each accepted creative mutation updates the revision shown in the command bar.
Unsubmitted text is distinct from a saved revision. Reopening a project restores
its scenes, profiles, audio and other saved decisions from the store, including
media retained in its content-addressed storage.

Creation retains the original request identity before dispatch. If the response
is lost, use the library's recovery entry to inspect that request's outcome.
An unconfirmed creation does not authorize another automatic creation. A local
copy, a submitted request and a confirmed saved project are separate states.

## Inspect original imported media

Choose **Preview original** beside an MP4 or WAV in Assets. The player opens the
immutable source retained with the project, so the original import path can be
unavailable after saving and reopening. Playback, seeking and audio controls
operate on that source. Files up to 1 GiB are read in bounded ranges.

Closing the player releases its reader. A change to the current project or
revision closes the preview; open it again to inspect the newly selected state.
Changed source bytes or an unavailable codec produce a visible error. The media
preview does not change the timeline or create a deliverable.

## Canvas object drafts

The object inspector retains raw text and the six base transform fields: X, Y,
width, height, rotation and opacity. Incomplete input such as `1e` remains visible
and recoverable, while **Commit transform** stays unavailable until the value is
valid. Arrow keys adjust a focused numeric field; Space on a control keeps its
normal action without also starting timeline playback.

Drafts are scoped to the project, generation, branch, scene and object. Editing a
second object or visiting another workspace preserves the first object's input.
Reopening the editor reconciles that input with the saved revision. Disjoint
saved changes remain available; an overlapping field shows its original, saved
and proposed values. Choose the saved value or the draft, then commit explicitly.
A change to an object's kind, parent or coordinate space requires review before
retained transform input can be applied in its new context. Saved locks continue
to protect the corresponding fields.

Saving retains the submitted snapshot before calling the service. Typing after
that snapshot remains a separate decision, including changing a value away and
back while the response is pending. A matching saved revision confirms the
snapshot; a resolved callback alone does not. An unconfirmed submission is kept
for review and is never replayed automatically.

Local recovery copies from other windows are available through an explicit
restore action. An unreadable or changed copy is kept for inspection or download.
If a copy changed since it was read, the editor refuses to replace it and offers
**Keep Canvas input in a new recovery copy**. Quota and storage failures are
visible; the editor keeps the current input in memory and offers a download.
Memory alone cannot survive a reload, so address that warning before leaving.

This draft recovery applies to text and base transform fields. Motion and camera
controls retain their existing saved keyframe contracts. A semantic Canvas
preview does not establish native renderer fidelity.

## Captions and output variants

Import and measure a voice source in **Audio**, then author transcript segments
with explicit timing. Manual timing is recorded as manual; synthetic measured
PCM is not evidence of speech alignment. In **Deliver**, select the voice take,
locale, exact frame rate and caption format, then save the profile. Profile input
remains editable while its save is pending, and a returned rational frame rate
is compared by its named fields rather than JSON object-key order.

Export a WebVTT or SRT sidecar to an absolute path with the corresponding
extension. The export derives its bytes from one checked project revision and
uses the existing timed-caption serializer. Unknown timing, obsolete source
authority and a destination that already exists are rejected. The export does
not mutate the project or overwrite an existing file.

The response identifies the source project, generation, revision, profile and
SHA-256 of the sidecar. A late response keeps identifying its original project
and profile even after the user changes selection. The source-current flag is
an observation after writing; it is not durable approval. If writing or
finalization fails, a file may exist at the destination and must be inspected
before another export.

**Derive selected** creates a child output profile with a source revision. Saving
the variant keeps the original profile's decisions intact. Dimensions, locale
and caption settings are editable intent; native layout, burn-in, audio fidelity
and audiovisual review require the corresponding renderer evidence.

## Portable project boundary

The existing desktop portable-bundle flow carries saved project data and CAS
media into a separate store and creates fresh generation authority on import.
Reopening and editing that import must continue from the retained identities and
decisions. The technical continuity tests export SRT again from the imported
store and compare its exact bytes with the original.

The current consumer still uses the legacy path-based bundle commands. The
required empty-library import and confirmation tied to an inspected fingerprint
are pending integration with the revision-bound portability service APIs. A
round trip without concurrent package mutation does not certify that missing
inspection-to-commit boundary. Portable restore does not restore native session
tokens or establish current render authority.

## Reproducible verification

Build `studio_review_session` from the same source revision and pass its absolute
path as `MOTIONWRIGHT_SERVICE_TEST_BIN` to Studio's Playwright tests. The project
lifecycle, scoped-change capture, Canvas draft and caption continuity suites use
the real service, SQLite, grants and CAS through an explicit JSONL transport.
They do not substitute a simulated persistence service when the binary is absent.

The capture oracle requires every journal event from revision one through the
current revision. Each suite retains complete closed-store evidence, including
media hashes and process outcomes. Deliberate damaged-row, missing-journal,
stale-authority, concurrent-edit and existing-file cases remain distinguishable
from successful interaction evidence.

These automated revisions establish technical regression coverage. They do not
constitute an installed-desktop run, a native audiovisual realization, or a
human creative review. Those evaluations must be recorded against their actual
source and artifacts separately.


## Import directly from the library

The installed desktop library can inspect an exported portable project folder
and import it into an empty Store without creating a placeholder project.
Inspection retains the exact manifest fingerprint; import still consumes the
existing one-time local import grant, rechecks original media and refuses an
occupied project identity. The imported generation is fresh.

Studio saves a bounded local continuity copy before requesting the grant.
If a reply is lost, **Check original import** reads the existing SQLite/CAS
commit marker and opens the current saved project, including subsequent edits.
It neither reads the source folder again nor repeats import. No matching
marker remains unconfirmed. Multiple matching commits, changed Store/CAS
identity, a foreign generation, and a malformed local copy are not accepted
as a successful recovery. Explicit removal affects only the local copy.

Recovery reuses the original transactional cas_import_commits table. It adds
no migration, imported operational receipt or native authorization.
