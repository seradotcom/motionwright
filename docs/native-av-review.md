# Bounded original audiovisual review in Deliver

Motionwright plays an already completed H.264/AAC MP4 inside its local Tauri editor. Review is explicitly requested, reads the original Semwright-owned artifact and leaves the creative revision unchanged.

## Capture-to-delivery continuity

Desktop creates a fresh Coordinator for each IPC invocation. Its backend-owned custody now retains the original Motion Canvas accounting root across the capture, direct MLT, multisegment MLT and optional audio-measurement calls. The retained root must match the exact live Store session, complete owner connection identity, source revision, Film options and original rendered-evidence digest. SQLite remains authoritative for the unchanged limits and cumulative debits; no new root or frame render is created to continue delivery.

The private handle is never serialized. A new Store session or caller-supplied render JSON cannot implicitly attach that budget. Closed Store identities and completed captures for superseded source revisions can leave the bounded memory registry; their durable receipts and spending remain intact. Explicit capture recovery below is required after losing that original live custody.

## Reopen a complete Motion Canvas capture

In Deliver, **Find saved capture** reads the original full-capture history for the current saved project and profile. **Reopen saved capture** verifies every original frame, restores the source-bound preview handles and makes the existing MP4 controls usable without rendering again. Pending profile/recipe edits and replies for superseded views cannot replace the current source.

The complete producer adds closed private recovery context to its existing verification intent and result, without another receipt row or store. Recovery replans the exact committed Film and matches the original plan, apply, start, result and verification request digests, owner identity and typed Native provider provenance. Every selected atomic operation must retain its original intent and completion; missing, ambiguous, changed or UNKNOWN history is refused. The provider's original manifest digest and every indexed frame digest/length are checked with bounded reads.

Only then does the backend reattach the existing SQLite accounting root to its new Store session. The original limits are independently rederived by the current planner, and all spent capacity and later delivery UNKNOWNs remain unchanged. Reopening is a read; future delivery still consumes its normal current effect grant and the remaining original budget. The worker limit and owner/source checks apply before and after reading. No root, debit, receipt or permission is recreated.

This route covers one unambiguous complete capture of up to 64 segments and 36,000 frames using the same original owner connection and output files. It does not admit partial-window or experimental hybrid captures. Legacy receipts without the new context are never backfilled; those originals remain unavailable to this new cold-recovery path. Completed MP4s have the separate review route below.

## Current-session playback

After native mastering completes, the backend retains a session token bound to the exact project, generation, revision and saved output profile. **Review native MP4** prepares a private `motionwright-media` URL through the existing `review_native_av_master` command. The WebView receives an opaque URL and media metadata; it never receives an owner filesystem path or chooses the source file.

Preparation validates the owner path and original SHA-256, retains the opened file and records hashes for its 1 MiB blocks. The local protocol accepts one bounded byte range at a time, returns at most **4 MiB** per response and verifies the covered original blocks again before returning bytes. The original MP4 may be up to **1 GiB**, matching the existing verified delivery limit. Length changes, altered blocks, stale project stamps and expired tokens are rejected. At most four range readers are admitted concurrently; overlapping reads of one file are serialized within that bound. Original preparation, receipt recovery and legacy binary reads have a separate two-worker limit acquired before queueing. Dropping the IPC wait keeps that capacity occupied until the actual worker finishes.

Every protocol request checks the actual registered Tauri owner window, its private resource identity, trusted entry URL and live project scope before and after reading. Only the media CSP admits this local scheme. No filesystem scope, network listener or general fetch authority is exposed.

A legacy caller may still request the original binary IPC response for files up to 16 MiB. Studio uses bounded ranges for new playback and does not copy an entire long master into a Blob. Browser decoders may prefetch or revisit ranges; bounded responses do not imply a fixed total transfer count.

## Reopen an original master after restarting

**Saved original MP4s → Find saved MP4s** lists completed muxes for the current source and output profile. **Review saved original** verifies the chosen original and opens it through the same bounded local reader. An audio measurement is optional for this route.

The two original mux receipt writes now retain a closed private context: the original mux arguments, project digest, output profile and owner connection identity. Reopening compares that context with the current saved project and original dispatch/result, checks actual Native provider provenance, verifies the original file SHA-256 and size, and rejects any UNKNOWN in the same atomic request history. Discovery exposes only bounded receipt/hash/clock hints. It does not expose the original paths or arguments.

There is no new receipt store, render, measurement, budget restoration or export capability. Old mux receipts without this metadata are not rewritten or inferred; they remain eligible only for an independently available older recovery path. The current-project guard and independent reader limit also apply to discovery and reopening.

The older saved audio-QC path remains available. In that history, **Review original MP4** selects the original decoded-measurement receipt for the current saved profile. The receipt UUID and expected digest are lookup hints. Native reconstructs the exact original source, measurement and completed master chain from SQLite and verifies current owner connection, receipt custody and original media before creating a new reader.

This path reuses an existing original. It does not render, measure, append receipts, retry an UNKNOWN operation or restore an export permission. Recovered handles are always review-only. Missing historical selector metadata, changed source/media, another connection or an unresolved original remains unavailable. The audio-QC recovery route follows the supported Linux native measurement chain. General saved-mux recovery has no meter dependency; playback still requires a compatible desktop decoder.

Changing the project revision, profile or selected original closes playback. Leaving and reopening the history view requires fresh discovery and readback; a late reply from a previous source cannot replace the current selection.

## Decoder support and product limits

The video element plays the encoded sound and supports ordinary playback and seeking. The [Program monitor](program-av-monitor.md) can follow a current-session original against the editorial playhead. Sample-exact or frame-exact seeking, caption glyph quality and independent human acceptance remain separate checks.

Linux Debian candidates declare the GStreamer Good, Bad and libav plugin packages for MP4/H.264/AAC playback. AppImage configuration includes Tauri's media framework, and both Linux packaging paths install those plugins before bundling. The system-plugin check decodes a small synthetic H.264/AAC clip. The installed AppImage/WebView still requires its own candidate check; configuration or a synthetic decoder result alone does not establish installed playback.

If the WebView cannot decode the original, the UI reports the playback error. A separately authorized, current-session verified export remains available where its source qualifies for delivery. Review itself does not grant export or publication authority.

## Focused verification

Desktop source tests cover closed request selectors, owner lifetime, exact source scope, single-range parsing, bounded concurrent reads, altered originals and the compatibility binary limit. Native recovery tests close and reopen SQLite and reject foreign, changed or unresolved original selectors without dispatching Native work.

Chromium tests exercise an actual synthetic H.264/AAC clip, a valid MP4 larger than 16 MiB, playback and seeking, source changes, history remount and late-result rejection. These fixtures establish the local playback/recovery contract. They do not replace original Native rendering, installed-device decoder verification or human product review.
