# Bounded native audiovisual review in Deliver

Motionwright can preview a **real, already-mastered H.264/AAC MP4** inside its local Tauri desktop editor, directly from the finished Semwright-owned output artifact. This is an explicitly initiated, **read-only media review**; it does not launch a render, create a file on the user's desktop, open a new network service or change a project revision.

## Canonical source and bounded delivery

After a single-segment native MLT master successfully completes, Motionwright's backend stores only a session-scoped export/readback token that is bound to the exact project ID, generation, revision and saved deliverable profile. The `review_native_av_master` command accepts only this token plus the exact project stamp. It rejects foreign, stale or expired sessions and checks the creative revision both **before and after** reading bytes.

The backend reads its own known master artifact through the existing Semwright-owner-root helper. It revalidates path normalization, rejects symlink traversal, enforces an exact SHA-256 digest over the returned bytes and rejects files larger than **16 MiB** *before* copying them into IPC. The MP4 header must also contain an `ftyp` box at the expected offset. The WebView never chooses a source file, receives an owner absolute path, changes a SHA digest, mints a grant or invokes generic filesystem APIs.

For a small eligible MP4, Studio wraps those exact verified bytes in a local `video/mp4` Blob, plays them via the WebView's native HTML media decoder and revokes the Blob URL on unmount, revision/profile change or retry. A user must explicitly click **Review native MP4** before the file is read. The controls allow video playback **with its encoded audio** when the local decoder supports H.264 and AAC.

## Limits and quality claims

This initial review is deliberately **not an unbounded AV streaming protocol**: it buffers at most 16 MiB of a verified media file (plus the temporary in-WebView copy). Large videos must use **Export verified MP4** for external playback; the export path continues to stream up to the separate validated maximum without buffering a giant movie into the UI.

The native review UI is independent of Motionwright's semantic timeline playhead; it does **not** yet promise frame-exact synchronized scrubbing, waveform correlation, subtitle burn-in, codec support across all WebViews, or proof of audio/visual creative quality. If the current WebView decoder cannot play the verified MP4, the UI reports the unsupported playback state and directs the user to the exact verified export, rather than rebranding a semantic representation as a real video.

No Graph admission, Effect Conformance PASS, external publication, signing or independent human acceptance is inferred from rendering a playable Blob.

## Exact-SHA tests

Rust desktop tests ensure a source-bound token returns actual bytes when the file is small and unmodified, and refuses a changed SHA-256, invalid MP4 header, foreign/stale project and source files above the 16 MiB cap. Browser tests use a clearly **synthetic** Tauri bridge to exercise the read-only request shape, no source paths/hashes/effect grants, explicit UI opt-in and object-URL cleanup. Synthetic media is not decoder-quality evidence; the separate native AV master E2E suite produces genuine Semwright H.264/AAC output in GitHub Actions, and installed-device playback/human approval remain open product acceptance work.
