# Brief editing and recovery

The Brief workspace edits the project title, objective, audience, constraints
and exclusions through the existing project service. **Save title** submits
only `rename_project`; **Commit brief** submits only `set_brief`. Claims,
production plans, scenes and media keep their existing identities and content.

The workspace keeps each field's unfinished text separately from the saved
project. Saving a title preserves the pending objective and other brief fields;
saving the brief preserves a pending title. Navigation and project updates do
not reset the whole form. Clean fields follow the observed project, while
unfinished fields keep their original value and revision for comparison.

## Save, observe and continue editing

A save captures an immutable snapshot of the selected group. Subsequent typing
is not added to that request. A resolved callback alone does not clear inputs:
the workspace requires the same project, generation and branch, the exact next
revision, and all submitted values in the observed project. The notice reports
that canonical match, without treating it as a unique request receipt.

When the submitted values are observed, inputs that still match the submitted
snapshot become clean. Newer typing and fields outside the group remain local.
Errors, denied grants and unconfirmed responses preserve the draft. Validation
still happens in the existing service and domain; there is no alternate local
commit path.

The editor retains an explicit input marker on fields being changed by an
outstanding submission. This matters when an author sends `Sent`, leaves the
panel, returns and types the original value `Old` before the response arrives.
`Old` remains a deliberate input even though it equals the earlier base. If the
saved document later contains `Sent`, the editor compares the two values and
requires a decision before another save.

Project content locks disable saving while allowing the author to keep drafting.
Space activates the focused Brief button or disclosure using native browser
behavior, without also starting the application's transport. Resolving a field
returns focus to that field.

## Overlapping revisions

The conflict panel shows the field's starting value, its current saved value and
the author's pending input. Only overlapping fields require a decision.

- **Keep saved** accepts the current value for that field.
- **Keep my draft** retains the input against the currently observed revision.

Neither action commits a change. Other fields remain available. A conflict in a
brief field blocks **Commit brief**, while an independent title can still be
saved. A recovered field from a later revision also requires explicit review.
The service continues to enforce generation, revision, authorization, locks and
domain invariants when the author saves.

## Local recovery

Recovery stores editor buffers under a versioned `motionwright.brief-draft.v1:`
namespace in local storage. Its identity includes the project ID, generation
and active branch. Each document uses a separate writer ID; another tab's draft
is not overwritten. Session storage holds this tab's recovery pointer, and an
in-memory buffer preserves workspace navigation when storage is unavailable.

A recovered copy contains raw text, source values and source revisions. It does
not contain a Change, effect grant, service receipt or approval. Restoring a copy
does not replay a request or modify the project. A pending current draft must
first be backed up successfully before a different recovery copy replaces it.

Recovery is bounded to 1 MiB per copy and 64 copies / 4 MiB across the namespace.
Unreadable copies and copies with pending input are not evicted automatically.
Redundant clean checkpoints may be removed, while the latest clean marker is
retained to avoid silently reviving an older completed draft. Clean keys remain
immutable; new work receives a new writer ID. The recovery panel offers explicit
restore and discard actions for earlier copies, including unreadable identities.

Storage failures show an actionable warning and keep current inputs in memory.
The latest edit may not survive closing or reloading when its backup failed.
Local recovery is not a portable project backup and does not promise filesystem
durability, survival after cleared site data, or recovery on another device.
Committed project state uses the service's separate SQLite and asset storage.

## Creative continuity

The existing production-plan editor uses the saved objective and audience when
preparing its initial plan. Merely opening that editor does not save a plan.
After an author saves a production plan, later Brief changes preserve that plan,
its shots, claims and clocks. This increment does not automatically rewrite an
authored plan or claim that a Brief save has produced a native render.

## Verification

Unit coverage in `src/briefDraft.test.ts` checks per-field reconciliation,
submission recognition, overlaps, pending-input markers, scope isolation,
recovery validation and bounded checkpoint behavior.

The interaction suites `tests/brief-service.spec.ts` and
`tests/brief-preservation.spec.ts` use the `studio_review_session` example to
exercise the real `StudioService`, authorization, SQLite journal and asset
storage. Build that example with the repository's pinned Rust toolchain and
configure its executable explicitly:

```sh
cargo build --locked -p motionwright-service --example studio_review_session
cd apps/studio
MOTIONWRIGHT_SERVICE_TEST_BIN=/absolute/path/to/studio_review_session \
  npx playwright test tests/brief-service.spec.ts tests/brief-preservation.spec.ts
```

The suites cover independent title/brief saves, keyboard activation, delayed
real replies, newer typing, reload without replay, service validation refusal,
two-client stale edits with both conflict resolutions, revoked grants, six
revisions and process reopen. They compare authored plans, source bytes and
unrelated project data, and exercise the initial plan's use of the saved brief.
The example is a test transport, not the production desktop backend. A held
reply controls delivery timing after a real commit; it does not replace the
service with a synthetic project. These suites require the configured binary
and otherwise skip. Browser interaction results do not establish installer,
native-renderer or human creative acceptance.
