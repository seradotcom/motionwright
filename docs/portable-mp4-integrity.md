# Portable SHA-256 verification for delivered MP4s

Motionwright can optionally place a small, unsigned integrity descriptor beside a locally exported, **already verified native H.264/AAC MP4**. The result is suitable for carrying alongside an MP4 to another computer so its bytes can be compared to the original export without running Semwright, a decoder or Motionwright Studio.

This is **content-integrity evidence, not a digital signature, authenticated provenance, codec compliance report, distribution approval or human creative acceptance**. Anyone who can replace both files can produce a new matching hash. Where publisher authenticity matters, separately sign the release or compare the MP4 hash against an independently trusted channel.

## Export

In the desktop's **Deliver → Deliver verified MP4** section, choose an existing absolute destination directory with a new `.mp4` filename. Enable **Write portable SHA-256 verification receipt (.json) next to the MP4** (off by default), then click **Export verified MP4**.

Motionwright uses the exact existing, one-time `DeliverLocal` grant bound to the selected MP4 destination. The optional sibling filename is deterministic:

```text
final-master.mp4
final-master.mp4.motionwright-integrity.json
```

The backend resolves the genuine MLT master only from its current, revision-bound owner-issued session token. It verifies the canonical source and the output MP4 bytes against their original SHA-256 before creating the completed proof. No source file, digest, native receipt, output profile or origin data comes from the WebView.

The JSON contains only a relative MP4 basename (no absolute source or destination paths), the exact verified MP4 byte count and SHA-256, the project UUID/generation/revision, delivery profile UUID, native H.264/AAC profile, frame count/rational FPS, and the pinned Semwright Native SDK revision embedded in the desktop binary. No private producer transcript, assets, source audio, credential, host path, user identity or raw driver payload is embedded.

The sidecar is created with **create-new semantics**, never overwriting an existing file or manifest, and uses mode `0600` on Unix. Motionwright reserves the sidecar filename before creating the new video, so an existing sidecar blocks the operation without exporting an unpaired MP4. If video export fails, the sidecar reservation is discarded. If video export succeeds but writing the sidecar fails, Motionwright retains the already-verified MP4 and explicitly reports that the optional proof was not completed rather than deleting real user media.

Moving a file does not mutate the project. The proof retains its **source revision**, even if the creative project advances later, and does not claim that older content is currently approved.

## Verify on another machine

Copy the two files into the **same directory** along with the independent verifier `tooling/verify_delivered_media.py`. Run:

```bash
python3 tooling/verify_delivered_media.py \
  --manifest /path/to/final-master.mp4.motionwright-integrity.json
```

Optionally compare against a previously obtained hash from a trusted separate channel:

```bash
python3 tooling/verify_delivered_media.py \
  --manifest /path/to/final-master.mp4.motionwright-integrity.json \
  --trusted-sha256 YOUR_PREVIOUSLY_TRUSTED_64_CHARACTER_SHA256
```

The verifier requires the exact v1 schema, canonical UUIDs and source rate, a bounded plain MP4 basename, a genuine regular local MP4 file, no symlinks in either file or its parent directories, strict frame-count and byte limits, an MP4 `ftyp` header and **byte-for-byte streaming SHA-256 equality**. MP4 bytes are processed in 1 MiB chunks, never buffered wholesale. It returns nonzero on corruption, missing files, symlinks, mismatched hashes/size or invalid evidence fields. It is standard-library-only Python; no cloud service, network access or renderer is invoked.

A successful check prints `"status": "sha256-content-verified"`, `"signed_authenticity": false` and `"human_acceptance": false`. If an independent trusted hash was supplied, `"trusted_anchor_matched": true` also appears.

## Verify directly in Motionwright desktop

The **Deliver → Verify a delivered MP4** panel can check an existing local MP4/JSON pair without the original project or an active producer session. It uses a read-only native Rust streaming verifier and can optionally check an independently trusted SHA-256. This is distinct from the CLI verifier above, which remains the portable dependency-free option. See [in-app verification contract](local-mp4-integrity-review.md).

## Security and product boundaries

- A locally verified checksum does not prove H.264/AAC decoding, visual frame fidelity, proper audio mix, SRT/subtitle synchronization, accessibility or that the native renderer truly made the content. These require the separate pinned-runtime and human acceptance gates.
- An unsigned JSON descriptor is **self-declared metadata**. Checking source UUIDs or the pinned SDK SHA is a syntactic/provenance-consistency check, **not** a cryptographic publisher identity check.
- Export still introduces no general filesystem plugin, generic write endpoint, network upload, shell command, arbitrary **master source** selection or bypass of `DeliverLocal`. The new separate read-only verifier accepts only a user-selected local receipt and its explicitly paired MP4; it cannot change the project or authorize delivery.
- If the manifest is disabled, the original MP4-only create-new export behavior and `MasterExportReceipt` remain available, with its existing SHA-256 and project revision.
- Desktop Rust regressions test actual source-bound output bytes, manifest fields without private paths, no overwrite, sidecar collision, integrity mismatch and Unix private permissions. Lightweight Python tests reject tampered/swapped media, directory traversal, malformed schemas, symlinks, bad UUID/rate/counts, and unverifiable content. Browser tests verify that the new flag is optional and the UI does not supply an arbitrary receipt or hash. The tests **do not** mark any of the 60 independent product acceptance cases PASS.
