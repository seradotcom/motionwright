# Verify an existing portable MP4 inside Motionwright Studio

Motionwright desktop now has a **read-only local verification** command for a previously exported MP4 and its unsigned portable JSON receipt. It works after the original session has ended and does not need the producing project to be open. The command does not depend on a running Semwright Broker or the owner-issued export token.

This complements — it does not replace — the source-bound native production and export gates in [Portable SHA-256 verification](portable-mp4-integrity.md).

## Operator flow

1. Produce a real native H.264/AAC master through Motionwright's pinned Semwright Native SDK and Driver Host lane.
2. In **Deliver → Deliver verified MP4**, enable the optional portable integrity receipt and export to a new absolute MP4 path. Existing outputs are never replaced.
3. Keep the sibling `final.mp4.motionwright-integrity.json` next to `final.mp4`, including if the pair is transferred to another workstation.
4. In **Deliver → Verify a delivered MP4**, enter the absolute local JSON receipt path. Optionally enter a 64-character lowercase SHA-256 obtained **independently** of that receipt (for example through a separate trusted publishing channel).
5. Choose **Verify local MP4**. A successful check displays exact file identity, byte count, content SHA-256, the self-declared source revision and whether an independent anchor was supplied and matched.

A previously verified result is immediately cleared if either user input changes or a fresh check starts. A failed check never leaves a stale green result. Browser demo mode cannot run this native command.

## Exact contract

The Rust verifier reads the user-selected local receipt and its sibling MP4. It accepts only the v1 receipt schema produced by Motionwright's native export path:

- Manifest must be a normal absolute path ending with `.motionwright-integrity.json`. Its name must be the media basename followed by that exact suffix.
- The JSON is UTF-8, at most 12 KiB, and contains *only* the expected `schema`, `scope`, `media` and `origin` structures.
- MP4 filename is a plain relative basename ending in `.mp4`; no slashes, backslashes, control characters or parent traversal are accepted.
- All origin UUID strings must be canonical lowercase; frame count, rate and Semwright commit SHA are syntactically checked, not authenticated.
- MP4 is a regular file, with size between 12 bytes and 1 GiB inclusive, and an MP4 `ftyp` signature at bytes 4–7.
- Files and observed parents that are symlinks are rejected; the verifier uses file handles for bounded reads. It makes no claim to be an OS sandbox or a general-purpose race-free filesystem capability.
- Media bytes are streamed through SHA-256 in fixed 128 KiB chunks. Actual byte count must match the manifest, and the optional separate trusted hash must match the manifest digest.
- No file write, project revision change, provider job, external upload, shell execution or export grant occurs. Work is delegated to a blocking Tauri worker rather than occupying the UI event executor.
- `source_revision` is returned as a decimal **string** to avoid silently rounding 64-bit revision IDs in JavaScript.

The result intentionally includes `signed_authenticity=false` and `human_acceptance=false`. Even when it has a trusted anchor, it proves only that the supplied independently trusted *content hash* matches the local media; no signature is checked.

The existing stdlib-only Python verifier remains independently usable without installing Motionwright:

```sh
python3 tooling/verify_delivered_media.py --manifest /absolute/path/final.mp4.motionwright-integrity.json
```

## Threats, evidence and regression gates

An attacker who controls both the MP4 and JSON can replace the pair and recompute the hash. This is why the optional **separate** trusted anchor matters. A passing receipt does not establish the identity of its author, licensing, privacy clearance, decoder compatibility, sync, color rendition, subjective quality or independently reviewed product acceptance.

CI evidence is layered:

- Rust tests in the Linux desktop gate exercise exact match/anchor, modified content, incorrect size, invalid MP4 header, bad source provenance, malformed schema, traversal and symlink denial.
- Chromium synthetic-bridge tests check that only an explicitly invoked verifier is called, invalid anchors are rejected before IPC, failures clear prior results, and project revision does not change.
- The existing standalone Python verifier's adversarial tests stay independent.
- Native Motion Canvas and MLT render E2E tests continue to prove the **producer** path, separately from portable content verification.
- None of these automated tests marks any of the 60 independently reviewed product acceptance cases PASS.

If verification fails, keep the original files intact. Compare their names and lengths; obtain fresh copies from the actual producer or verify against a trusted independently published SHA-256. Motionwright will not repair, overwrite or delete either file.
