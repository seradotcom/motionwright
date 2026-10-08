# Source-verified native PNG preview

Motionwright's Program surface can switch from its clearly labeled semantic design representation to **sampled, actual Motion Canvas PNG frames** after a successfully completed Semwright Native production operation. This is frame readback from the renderer's immutable artifact, not a reconstructed canvas thumbnail, not an encoded video player and not evidence of full realtime playback or audio sync.

## Trust and authority

The pinned Semwright Motion Canvas driver returns a completed native artifact containing an output directory, SHA-256 manifest digest, exact frame count and a `frames/NNNNNN.png` sequence. Its canonical production/verification path is unchanged.

After the authenticated `render_motion_canvas` Tauri command returns actual Semwright evidence, Motionwright checks the manifest digest, its bounded size and all declared frame indices, paths, sizes and SHA-256 values. Only after that validation does the desktop issue short-lived **application-session** preview handles, bound to project ID, generation, revision, delivery profile and native render segment. The WebView never sends or receives absolute or relative filesystem paths for frame readback and cannot request an arbitrary output-root file.

`preview_native_frame` accepts only a server-issued opaque token plus a nonnegative frame index and the exact project stamp. The Rust backend rechecks the current project revision before and after reading, rejects unknown/expired-by-eviction grants, refuses symlink traversal, enforces a 24 MiB per-PNG budget and validates the file bytes again against the pinned per-frame SHA-256 from the renderer manifest. At most two readback requests run concurrently, and grants are bounded in number. The IPC response is binary PNG bytes: no local HTTP server, broad filesystem plugin, asset protocol wildcard, runtime mutation, host command or Network permission is introduced.

Source generation/revision changes invalidate readback. If the renderer's output directory is unavailable or a PNG changed, the UI shows an explicit error and offers a retry rather than quietly substituting a semantic drawing while labeling it native.

## Editorial behavior and limitations

- Users explicitly choose **Show native frames** or **Show design**. Without a current verified session handle, the native readback control is disabled or absent.
- For each selected scene, the Studio follows its segment's ordered scene IDs and versioned source durations, then samples the closest *preceding* renderer frame. The output profile's exact rational FPS defines frame index, independent of the timeline's DF/NDF label policy.
- The UI samples at most four frames per second while the editorial clock advances; it displays a frame index and makes the sampling limit conspicuous. This is a real **frame scrubber**, not a continuous 24/30/60 fps player.
- A readback handle applies only to the selected output profile. Selecting another profile or advancing the project revision returns the view to a truthful design representation, while native production receipts stay marked DIFFERENT PROFILE or STALE.
- The readback handle is in memory only. Restarting the editor does not silently reconstruct permissions from a path, a browser store or a former execution receipt. Persistent, independently authorized artifact playback, encoded MP4 viewing, audio synchronization and mixed-renderer preview remain separate future gates.

## Evidence boundaries

- Rust unit tests exercise manifest validation, SHA-256 readback, stale generation/revision, invalid indexes, owner-resource mismatch, tampered PNG bytes, and path traversal rejection against disposable files.
- TypeScript tests verify scene-to-segment frame calculation for fractional rates, changed profiles, missing grants and stale revisions.
- Playwright covers mode switch, native PNG decode from a clearly synthetic Tauri bridge, seeking, absence of disk paths in requests, and honest error states; it is *not* a fake passing native renderer.
- The dedicated real `native-render-e2e` GitHub Action independently verifies the actual Semwright output manifest and SHA-256 bytes of all 60 PNG frames before retaining first/middle/last frames as artifacts. This exercises the genuine render and its readback contract, while human review and end-user device playback acceptance remain distinct.

No Graph admission, Effect Conformance PASS, synchronized AV master or creative approval is inferred from these checks.
