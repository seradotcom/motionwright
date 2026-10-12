# Motionwright v0.5 — source-bound caption reading preflight

This bounded read-only capability lives in **Deliver → Caption sidecar →
Caption reading review**. It uses the current Project, generation, revision,
unsaved or saved DeliverableProfile, selected voice track, transcript
segments, authorized source scene ordering and existing cut intent. The
button **Review transcript in Audio** goes to the real Audio editor. No new
caption editing backend, media generation or independent renderer is added.

## Source timing and cut handling

- Source times are parsed as exact rational integers and operated on via
  bounded BigInt arithmetic. Non-increasing, negative, malformed or
  oversized clocks are rejected rather than converted to arbitrary float
  milliseconds.
- For explicit scene cuts, the preflight preserves canonical source order,
  refuses missing, repeated or reordered scene references, and rebases
  wholly contained segments to their retained cut time. Captions crossing
  cut boundaries are structural errors and require human realignment.
  Source segments lying completely outside the selected cut are counted
  as omitted, never evaluated as delivered captions.
- For the full uncut source, existing authored source times remain
  unchanged. Segments completely outside the project scenes receive
  a bounded editorial notice; native export validation remains the
  final authority for actual export legality.
- Each source segment must have a selected transcript track, increasing
  time interval and manual or measured alignment. UNKNOWN evidence
  cannot become a success claim. A measured alignment digest must match
  the currently admitted voice source SHA-256 to avoid silently
  accepting a replaced recording.
- Multiple voice transcript takes require explicit selection. A
  non-admitted voice source is reported; the preflight never imports it.

## Editorial guidance, not universal standards

Additional **review** notices identify overlapping caption intervals,
display shorter than 0.75 seconds, display longer than 7 seconds,
more than two explicit lines, lines over 42 Unicode code points, and
more than 20 non-whitespace Unicode code points per second.

These numerical values are intentionally labeled *editorial
heuristics*. They are not standards, subtitles language quality
measurements, automated accessibility certification, speech accuracy
or approvals. Code-point counts do not model grapheme clusters,
display glyph widths, bidirectional layout, visual accommodation,
East Asian reading speed, accessibility needs, or fonts. They are
not hard-blocking delivery errors. Any burn-in request explicitly
requires native pixel review for caption position, occlusion, legibility
and safe-margin interaction.

An empty list or NO_AUTOMATED_FLAGS means only that these checks did
not find issues in a bounded source subset. It does not imply a
successfully created WebVTT/SRT, that actual pixels were reviewed,
a correct native video/audio master, speech-to-text truth, source rights,
viewer comprehension, external publication, or human approval.

## Security, scope and test evidence

- No network, filesystem writes, project mutations, Broker effects,
  raw source serialization, or automatic caption export.
- Technical report is bound to project ID, generation, revision,
  profile ID, and voice identity. Limited in-memory UI output displays
  only a bounded excerpt of text already present in the owner's project.
  CI artifacts never include private transcript excerpts.
- Max 256 source scenes, 768 transcript segments and 8,000 UTF-16 code
  units per segment; report retains at most 90 findings and renders
  only 18 before explicit overflow notice. Input outside the budget
  is reported as not assessed, not evaluated as a pass.
- Unit tests cover source clocks, cut rebasing and exclusions, sources,
  alignment evidence, voice selection, bounds, line density,
  overlaps, draft non-mutation and failure conditions.
- Browser tests verify truthful empty evidence, navigation to Audio,
  unsaved draft changes, and unchanged project revision.
- Pinned Semwright Native SDK, Rust caption-export validation,
  rights admission, native master checks and existing 60 independent
  product acceptance cases are unchanged.

Scope is a bounded **MW05-E14-03** technical/editorial subcapability,
not full profile quality, multilingual accessibility or human creative
acceptance. The original v0.5 release remains unapproved.
