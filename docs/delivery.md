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

## Media delivery

Codec, sample-rate, brand and cut selections are versioned intent consumed by native production. Final video/audio export remains tied to Semwright-backed production receipts; the editor must not present a configured profile as if an MP4 or other master already exists.
