# Localized voice selection, atomic updates and historical recovery

A derived delivery in another language must select its own voice when the source
profile resolves to voice or transcript content. Translated canvas text and an
unselected voice field do not establish localized caption timing.

## Source selection

The source comparison considers the parent's explicit voice, the project's
active voice, and a unique transcript source when no voice is selected. A
localized profile cannot select that same source voice. An unselected localized
voice is rejected even when timing is unlocked: unlocking timing does not turn
inherited source captions into localized captions.

If an implicit parent's active audio and unique transcript belong to different
tracks, select the parent voice explicitly. Neither source silently overrides
the other. Importing a new measured voice activates it at project scope; bind
the original parent's voice before importing a different locale's voice when
the original delivery must retain its source.

A profile in the same language can retain the existing implicit selection.
A silent project with no selected voice or transcript can also create a
translated silent profile. Narration is not required in every project.

## Timing and rejected changes

With `timing_locked: true`, the selected localized voice must have the same
measured duration as the resolved parent voice. A difference returns an error;
it does not shorten, accelerate, or replace either source. Select and measure an
appropriate localized take, or explicitly unlock and re-time the derived delivery.

When no active or explicit parent voice identifies the source and several
transcript tracks remain, locked timing requires an explicit parent selection.
An independently selected localized voice with unlocked timing can use its own
transcript. Caption exports include only that voice's known timing evidence.

A rejected profile create or update preserves the prior document, its revision,
and accepted profile values. The public domain API validates a candidate before
replacing the caller's project. Through `StudioService`, the SQLite document and
journal remain unchanged and reopening preserves the same state. Original media
bytes remain unchanged in the content-addressed store.

## Read compatibility and incremental recovery

`Project::validate` retains its existing structural read contract. Historical
documents with the previously accepted implicit locale combination remain
readable. They are not silently relabeled, deleted, or assigned a different voice.

`UpsertDeliverable` checks the changed candidate's voice authority. This lets a
caller repair one historical profile while another remains pending. Captions,
transcript selection, and variant fingerprints check the current target's
authority before returning delivery data; an incompatible target returns a
descriptive error. Other structurally valid profiles remain available.

Native delivery consumers must call
`validate_deliverable_voice_authority(&project, profile_id)` on the exact current
snapshot before dispatch or artifact reuse, including captionless/full-cut
paths. Changing a parent or active source can invalidate a previously acceptable
child. Reading a draft or retaining an earlier artifact is not delivery authority.
Native entry-point integration and installed production verification are separate
gates from the domain/service regressions described here.

## Verification scope

The focused regressions use the public domain API and a real `StudioService`,
SQLite databases, measured synthetic PCM files, and manual transcript provenance.
They cover rejection, explicit source selection, divergent source authorities,
timing, reopening, unchanged original media, and incremental recovery of two
historical profiles. Run them with:

```sh
cargo test -p motionwright-domain delivery::tests --all-targets
cargo test -p motionwright-service --test localized_voice_authority
```

Voice identity and measured duration establish source and timing relationships.
They do not automatically verify the language spoken in audio, translation
quality, native caption burn-in, or human acceptance. Those properties require
the corresponding media and production checks.
