# Delivery profiles and caption sidecars

Motionwright stores delivery intent as part of the versioned creative project. A profile specifies frame dimensions, locale, caption policy, video/audio codecs, sample rate, an optional brand profile and an optional narrative-cut label.

Changing a profile is an application mutation. It uses the same revision/generation compare-and-swap boundary as the rest of the project and therefore participates in branches, history and review anchoring. A profile is not a rendered artifact and does not imply that a renderer supports or has produced the requested combination.

## Captions

The desktop application can write WebVTT and SubRip sidecars from the project transcript. Caption export is deliberately fail-closed:

- transcript timing must exist;
- every exported segment must have manual or measured alignment evidence;
- UNKNOWN alignment blocks export instead of becoming invented timecodes;
- rational timestamps are converted to milliseconds without floating-point time arithmetic;
- the destination must be absolute and its parent directory must already exist;
- Motionwright creates a new file and refuses to overwrite an existing file.

Caption content is application-owned deliverable material. A sidecar export is not Effect Conformance, render evidence or publication approval.

## Portable project delivery

The portable project format is a directory bundle, not an opaque archive. Its manifest carries the complete project backup/journal and the exact digest/size/path of every referenced immutable blob. Import validates the bundle before creating project authority, rejects symlinked roots/files/path components and enforces bounded blob count/aggregate size in addition to digest checks. Execution request receipts and owner connection material are not restored with a project.

## Build delivery evidence

CI can generate an exact-source delivery manifest with `tooling/release_manifest.py`. That manifest binds the tested Motionwright SHA to the Semwright Native SDK pin and hashes the Rust/npm lockfiles, source lock, AGPL license and third-party notices. It is uploaded as evidence by the Security and Delivery workflow; it is not a signing, notarization or publication claim.

## Media delivery

Codec, sample-rate, brand and cut selections are versioned intent consumed by native production. Final video/audio export remains tied to Semwright-backed production receipts; the editor must not present a configured profile as if an MP4 or other master already exists.
