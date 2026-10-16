# Captioned native MP4 — owner-side review without premature export

**Status:** bounded v0.5 Studio/Desktop review capability, not a production
caption acceptance or human accessibility certificate.

Motionwright PR #239 (merged at 609c4e305da554e5d92edcfc95ea67b0716beab0)
proved in Linux GitHub Actions that original Semwright Motion Canvas captions
survive the pinned Native SDK MLT FFV1 to H.264/AAC process for a synthetic,
source-pinned 33-frame fixture. Those original CI pixels cannot certify a
different installed user's generated master.

## User operation

The existing saved Burn-in profile, measured 48kHz stereo WAV, canonical
Film source digest, project CAS revision, opaque preview token and
one-shot RenderLocal grant remain unchanged. The desktop producer still
produces its actual master via Semwright, validates SHA-256 and verifies
the native AV source. **For captioned masters** the desktop mints an
ephemeral **owner review-only** token:

- Native MP4 review reads only the exact SHA-matching original owner artifact,
  with a 16 MiB maximum browser IPC payload and no user-chosen source paths.
- Studio marks the media REVIEW ONLY · CAPTION PIXELS NOT VERIFIED LOCALLY.
  It does not show captioned MP4 as delivered/approved.
- Export verified MP4 remains disabled. The Tauri backend independently
  rejects export BEFORE consuming DeliverLocal grant; both direct export
  and optional unsigned-integrity-manifest export fail before creating any
  destination or sidecar bytes. This restriction cannot be bypassed by a
  different WebView, agent or a forged request.
- Malformed or missing caption count, mismatched master scope, foreign project
  generation/revision, oversized source, changed original SHA, denied grants
  or altered owner paths do not create delivery authority.
- **Noncaptioned masters remain export eligible** under existing owner
  source SHA-256 checks, explicit user destination and one-time grant.

The exact pinned Native SDK does not yet offer an independently attested
read-only decoded caption glyph comparison as a desktop output contract
for arbitrary installed multi-segment H.264 media. This feature **does not
silently substitute system FFmpeg, an alternate renderer or the synthetic CI
artifact** to manufacture that approval. Longer captioned masters exceeding
16 MiB require a future bounded local native media review/observation path;
there is no fallback export.

## Tests and release bounds

Rust tests reject forged/mixed scope and caption counts, and prove
read-only review plus refusal of both media copy and integrity sidecar.
Studio Chromium tests separately exercise ordinary original export and
captioned review-only action. Changes never modify native provider
permission policy, CAS, StudioService project, or pinned Semwright SHA.

Real installed glyph readback, multilingual contrast/legibility, authentic
speech timing, media rights and user creative approval are separate
NOT_VERIFIED/NOT_RUN gates. No commercial release is implied.
