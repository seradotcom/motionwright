# Explicit delivery of a verified native MP4

After Motionwright completes the native Motion Canvas + measured-WAV → Semwright MLT H.264/AAC master path, the desktop offers **Deliver verified MP4**. This copies the exact existing native master to a *new*, user-selected local absolute `.mp4` destination; it does not encode a new file, invent a download, upload user data, publish media, or change the creative project.

## Authority and source provenance

The existing owner-provisioned `ProductionCoordinator::assemble_mlt_av_master` checks the output profile, voice binding, visual segment, FFV1 intermediary, AAC/H.264 mux metadata and SHA-256 output/decoded audio. **Only after that verified operation finishes**, the Tauri backend issues an opaque, in-memory **master export token** and retains the exact native output root, project ID/generation/revision, delivery profile, relative artifact reference and artifact SHA-256 **without sending the source file path or hash-bearing input choices back as writable parameters**.

`export_native_av_master` accepts only the current project stamp, source token, explicit destination path and a separately issued one-time `DeliverLocal` effect grant bound to that *exact destination*. The WebView cannot choose a different source file, alter a digest, pass arbitrary MLT/FFmpeg arguments or reuse a render grant as a file-export grant. A token from a former revision, foreign project or absent session fails closed.

The delivery backend first validates the user destination as an absolute UTF-8 MP4 path with an **existing real directory chain and no symlinks, dot-component traversal or special parent files**. The copy uses create-new semantics; it never overwrites a pre-existing user file. On Unix, new MP4 files are owner-private (`0600`).

The **source** is independently reverified using Motionwright's canonical owner-root-only readback boundary: normalized relative path, no symlink traversal, byte budget of at most 1 GiB and exact recorded SHA-256. The local export then streams bytes in bounded chunks, checks the MP4 `ftyp` signature, enforces the verified source byte count, syncs the output and independently rehashes copied bytes against the same canonical SHA-256. No entire 1 GiB video is buffered in the WebView or the application memory. If a copy fails, only the newly created incomplete destination is discarded; existing files are never touched. A project edit during export leaves any successfully written, verified output intact but marks its source revision as historical.

## Product scope

On explicit opt-in, the same one-time destination-bound delivery operation also reserves a non-overwriting local `.mp4.motionwright-integrity.json` sibling with source revision, native profile, pinned Semwright SHA and the exported MP4 SHA-256; see [portable integrity verification](portable-mp4-integrity.md). No additional arbitrary source path or generic filesystem permission is introduced. The receipt is **unsigned** and cannot prove publisher authenticity or content quality.

The UI shows the destination, byte count, SHA-256 and CURRENT or HISTORICAL source status. The receipt is session-bound for the current desktop application. Export is not a signed release, a network publication, media-quality approval, source-video playback, automated upload or independent product acceptance. Correctness of the *bytes* is distinct from viewer compatibility, audio mix quality and final creative approval.

This closes one critical local-first deliverable action without introducing general read/write filesystem capability to Semwright agents. A truly persistent, portable media artifact catalog and external deliveries require separate explicit trust boundaries.

## Evidence

- Rust desktop tests: source identity and codec/digest registration; source tamper/stale token rejection; absolute safe destination; symlinked parent refusal; create-new/no-overwrite; SHA-256-verified streamed copy; owner-private file mode on Unix.
- Browser tests: a clearly synthetic Tauri bridge checks the user-visible delivery workflow, exact one-time grant intent, request shape without source paths or untrusted hashes, and revision invalidation. These are UI wiring tests only.
- The pre-existing real native AV master workflow exercises the pinned Semwright Broker/Driver Host and H.264/AAC MP4 generation. The delivery-specific tests are separately run in GitHub Actions at the exact PR source SHA.

No accepted product scenario is promoted from a green CI run without independent human verification.
