# Bounded original audiovisual review in Deliver

Motionwright plays an already completed H.264/AAC MP4 inside its local Tauri editor. Review is explicitly requested, reads the original Semwright-owned artifact and leaves the creative revision unchanged.

## Current-session playback

After native mastering completes, the backend retains a session token bound to the exact project, generation, revision and saved output profile. **Review native MP4** prepares a private `motionwright-media` URL through the existing `review_native_av_master` command. The WebView receives an opaque URL and media metadata; it never receives an owner filesystem path or chooses the source file.

Preparation validates the owner path and original SHA-256, retains the opened file and records hashes for its 1 MiB blocks. The local protocol accepts one bounded byte range at a time, returns at most **4 MiB** per response and verifies the covered original blocks again before returning bytes. The original MP4 may be up to **1 GiB**, matching the existing verified delivery limit. Length changes, altered blocks, stale project stamps and expired tokens are rejected. At most four readers are admitted concurrently; overlapping reads of one file are serialized within that bound.

Every protocol request checks the actual registered Tauri owner window, its private resource identity, trusted entry URL and live project scope before and after reading. Only the media CSP admits this local scheme. No filesystem scope, network listener or general fetch authority is exposed.

A legacy caller may still request the original binary IPC response for files up to 16 MiB. Studio uses bounded ranges for new playback and does not copy an entire long master into a Blob. Browser decoders may prefetch or revisit ranges; bounded responses do not imply a fixed total transfer count.

## Reopen an original master after restarting

In the saved audio-QC history, **Review original MP4** selects the original decoded-measurement receipt for the current saved profile. The receipt UUID and expected digest are lookup hints. Native reconstructs the exact original source, measurement and completed master chain from SQLite and verifies current owner connection, receipt custody and original media before creating a new reader.

This path reuses an existing original. It does not render, measure, append receipts, retry an UNKNOWN operation or restore an export permission. Recovered handles are always review-only. Missing historical selector metadata, changed source/media, another connection or an unresolved original remains unavailable. This recovery currently follows the supported Linux native audio-measurement chain; ordinary current-session playback remains available on supported desktop decoders.

Changing the project revision, profile or selected original closes playback. Leaving and reopening the history view requires fresh discovery and readback; a late reply from a previous source cannot replace the current selection.

## Decoder support and product limits

The video element plays the encoded sound and supports ordinary playback and seeking. The [Program monitor](program-av-monitor.md) can follow a current-session original against the editorial playhead. Sample-exact or frame-exact seeking, caption glyph quality and independent human acceptance remain separate checks.

Linux Debian candidates declare the GStreamer Good, Bad and libav plugin packages for MP4/H.264/AAC playback. AppImage configuration includes Tauri's media framework, and both Linux packaging paths install those plugins before bundling. The system-plugin check decodes a small synthetic H.264/AAC clip. The installed AppImage/WebView still requires its own candidate check; configuration or a synthetic decoder result alone does not establish installed playback.

If the WebView cannot decode the original, the UI reports the playback error. A separately authorized, current-session verified export remains available where its source qualifies for delivery. Review itself does not grant export or publication authority.

## Focused verification

Desktop source tests cover closed request selectors, owner lifetime, exact source scope, single-range parsing, bounded concurrent reads, altered originals and the compatibility binary limit. Native recovery tests close and reopen SQLite and reject foreign, changed or unresolved original selectors without dispatching Native work.

Chromium tests exercise an actual synthetic H.264/AAC clip, a valid MP4 larger than 16 MiB, playback and seeking, source changes, history remount and late-result rejection. These fixtures establish the local playback/recovery contract. They do not replace original Native rendering, installed-device decoder verification or human product review.
