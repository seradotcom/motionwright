# Scoped change review and draft recovery

Scoped changes let an author stage text and transform edits for one scene,
inspect their effect, and apply the reviewed proposal as one project revision.
The workbench preserves unfinished input while the author changes objects,
inspects history, or returns to the same editing session.

The failure this flow addresses is a mismatch between what was reviewed and
what was eventually sent: a late preview could belong to an older proposal,
while mutable form state or a newer project revision had already changed.
Another failure is losing a draft when a commit callback finishes even though
the expected change has not been observed in the project. The workbench keeps
the draft, reviewed snapshot, and committed project record distinct.

## Authoring and review

Open **Production → Scoped changes**, then select the working scene and target
object. Enter replacement text or a transform, choose **Add to proposal**, and
write the reason for the change. Typing, selecting an object, and preparing a
preview do not commit a project change.

The current workbench exposes **Text only** and **Transform only**. A transform
contains position, size, rotation, and opacity. Adding the same object and edit
kind again replaces that queued entry; a text edit and a transform edit for the
same object can coexist in the proposal.

| State | What it contains | What preserves it |
| --- | --- | --- |
| Editor inputs | Typed text and transform values, including values not yet queued | Current component state and a bounded recovery copy in the current tab |
| Proposed edits | The entries explicitly added to the queue, their source values, and rationale | The same tab recovery copy |
| Prepared review | A cloned change, its exact source identity, and before/after nodes | Memory for the current review only; recovery never restores a prepared preview |
| Committed change | The resulting project revision, creative patch record, and journal event | The existing service and SQLite storage |

**Preview scoped changes** compares the queued proposal with the source
project. The comparison reports the selected scene's invalidation interval and
shows before/after node state. It is an editorial comparison, not a decoded
renderer frame or an assessment of creative quality.

### Apply the exact reviewed snapshot

The prepared review is bound to the project ID, generation, active branch,
revision, selected scene, proposal, repair source when present, and selected
output profile. The workbench clones the `Change` when review completes and
sends that stored change when **Apply as one revision** is selected. It does
not reconstruct the submitted change from the current form fields.

Editing the proposal or rationale, changing the review context, restoring a
draft, or observing a different project revision requires a new preview.
Asynchronous preview results are accepted only if their request epoch and
review identity still match the current view. A late result cannot reactivate
Apply for a replaced or removed proposal. The submission guard also prevents
overlapping apply attempts from the same workbench.

The desktop submission path remains `api.ts → issue_effect_grant → apply_change
→ StudioService`. The service validates the change against the expected
generation and revision using transactional compare-and-swap, and checks the
domain's locks and invariants again. Recovering a draft or preparing a local
preview supplies no grant and bypasses none of those checks.

## Draft identity and unqueued input

A recovery key contains the following identity:

```text
[project ID, generation, active branch ID, scene ID]
```

The storage prefix is `motionwright.scoped-change-draft.v1:`. Revision belongs
to the saved draft's source metadata rather than the key. This lets a draft
remain available after the project advances while still marking its old base
as stale. Different projects, restored generations, branches, and scenes do
not inherit one another's form state or pending preview. Returning to a scope
can recover that scope's own draft from the same tab.

Changing the edit kind preserves both the text and transform inputs. Changing
the target object parks unfinished input for the previous object; selecting
that object again restores those inputs. The queue remains explicit. A field
that has been typed but not added to the queue is not silently included in a
commit, even when another field for that object has been queued.

When a matching committed patch is observed, the workbench clears the reviewed
queue and retains inputs that were not part of it. Those remaining inputs stay
available against the updated project. A successful queued edit therefore does
not erase an unrelated sentence or transform that the author was still
considering.

### Limits of tab recovery

The recovery mechanism uses `sessionStorage`, with a versioned schema and a
512 KiB per-draft encoding budget. It preserves editing inputs; it is not a
project backup, an operation receipt, a persisted preview, or an authorization
token. Restored content must pass the local shape checks and still requires a
fresh preview and the normal service commit.

Reloading within the same surviving tab is covered by this mechanism. It does
not guarantee recovery after closing the tab or desktop window, terminating
the browser or operating system, changing devices, clearing site data, or
losing disk data. Storage can also be unavailable or exceed its quota. No
filesystem durability or cross-tab synchronization is implied by
`sessionStorage`.

If a recovery write fails, the workbench reports that the draft remains open
and asks the author to keep the view open until the work is committed or
discarded. If saved data cannot be decoded, the existing recovery bytes are
left untouched rather than overwritten with an empty draft. **Discard saved
copy and current draft** is an explicit choice. Applied project changes use the
service's separate persistence path.

## New revisions, conflicts, and explicit rebase

When another edit advances the project revision, the existing proposal stays
visible, but its old preview cannot be applied. **Review on current revision**
is an explicit rebase step: it updates the draft's source only after checking
that its affected values still match the values from which the draft began.
A rebase never submits a change and always requires another preview.

Conflict inspection includes queued changes and unfinished input. It compares
the original, current, and proposed values for each affected text or transform
entry. A missing object, a changed object kind, or a changed transform parent
also prevents silent reuse of the old source. A transform is reviewed as one
value containing all six transform fields.

For an overlapping entry, **Keep current …** removes only that proposed edit
kind and accepts the current value for that input. Other queued changes and
unfinished inputs remain available. After resolving the overlaps, the author
can rebase the remaining proposal and inspect it again. A deleted object is
not recreated as a side effect of resolving a stale proposal.

A repair originating from a native-frame observation also keeps its original
source revision, frame digest, target, and output profile. Ordinary rebase
does not certify that old frame against a different source. This also matters
after committing part of a repair: remaining unqueued inputs survive, but the
original frame observation still belongs to its original revision.

To keep the frame-bound workflow, obtain a current matching observation. A new
frame repair offered while another draft exists presents an explicit
replace/keep choice. Alternatively, **Continue as a manual proposal** preserves
the current inputs, queue, and base revision while explicitly ending the old
frame binding. The rationale retains the old observation's identifying
metadata as historical context, with a statement that it does not verify the
current proposal. This action does not claim that the old frame became current,
apply a change, or create an effect grant.

Manual continuation invalidates the previous review and requires a new
preview. If the draft's base revision differs from the current project,
conflicts must still be resolved and **Review on current revision** selected
before previewing. The author can therefore continue retained input without
silently replacing its source or discarding the rest of the draft.

## Undo and confirmation of persistence

**Preview undo** inspects an existing creative patch record without replacing
the independent proposal in the editor. **Back to proposal** dismisses the
undo inspection and keeps that proposal; applying it still requires a new
preview. Applying undo creates another revision and links its patch record to
the record it reverts. Redo uses the inverse record through the same review
and mutation path.

An existing unfinished proposal also survives a committed undo. Its source may
then be stale, so the author must inspect conflicts and explicitly rebase it
before applying it. Undo is not permission to erase later incompatible author
decisions: the service rejects conflicting, missing, or locked targets
atomically, and compatible unrelated changes remain in place.

The workbench does not clear a submitted proposal merely because the commit
callback resolves. It first observes a matching creative patch record in the
updated project. Matching requires the same project, generation and branch,
the expected next revision, the reviewed scene and source revision, and the
reviewed before/after nodes. An applied proposal must also match its rationale;
an undo must identify the intended reverted record. An error, unrelated revision,
or unconfirmed response leaves the draft available for further inspection.

## Focused verification

The Cargo package is **`motionwright-service`**. The Studio npm package is
**`@motionwright/studio`**, located at `apps/studio`. Use the Rust toolchain
selected by `rust-toolchain.toml`, currently `1.98.1`, and retain `Cargo.lock`
and the pinned dependencies. The commands below are for a prepared isolated
checkout or disposable test runner; they do not launch native renderers.

| Test surface | Evidence it can provide |
| --- | --- |
| `apps/studio/src/scopedChangeDraft.test.ts` | Pure editor-state checks for scope identity, queued and unqueued input, explicit rebase, receipt matching, and bounded recovery decoding |
| `crates/service/tests/scoped_change_preservation.rs` | Desktop-shaped JSON, real SQLite/CAS, one-revision patch application, independent service instances, conflict rejection, structural preservation, undo/redo and reopen |
| `crates/service/tests/creative_undo.rs` and `creative_production.rs` | Existing undo, idempotence, later author decisions, bounded history, regeneration, branch and reopen regressions |
| `apps/studio/tests/scoped-change-preservation.spec.ts` | Browser input/recovery behavior and deliberate UI fault injection, including delayed editorial previews; this suite uses synthetic browser fixtures |
| `apps/studio/tests/scoped-change-service.spec.ts` | Browser-to-process integration through the real `StudioService` test transport and persisted state |

The JSONL executable is `studio_review_session`. It uses the existing service,
SQLite journal and content-addressed asset store. Its synthetic project is
created through actual service changes with generated identities; responses
are not precomputed project fixtures. The Playwright helper in
`tests/support/scopedChangeService.ts` correlates responses by request ID and
reports process exit, stderr, protocol errors, and timeouts. It does not retry
a timed-out mutation automatically.

`page.exposeBinding` and `window.__TAURI_INTERNALS__.invoke` connect the browser's
existing invoke path to this test process. Every forwarded command receives a
real process response; unsupported commands, including render commands, fail.
The bootstrap declares `test_transport: "service-test-jsonl"` and synthetic
fixture metadata. Its existing `native_sdk.mode` field remains `browser-demo`.
These fields must not be treated as evidence that the Tauri runtime is active.

The TypeScript editorial preview remains a local calculation in `api.ts`.
The test executable's `preview_creative_patch` command exposes the Rust domain
preview for comparison; it does not add that command to the production
desktop IPC. The delay injected by the browser fault-injection suite tests
out-of-order completion, without establishing service or renderer behavior.

Neither browser suite certifies an installed Tauri application, installer
relaunch, Semwright renderer execution, pixel fidelity, source authenticity,
or human creative approval. Record the exact tested source and retain the
actual test output; a listed command or a skipped suite is not a passing result.

### Bash

Run from the repository root. This example uses an explicit target directory
so the service binary path matches the build even when other tests use a
different Cargo target directory.

```bash
set -euo pipefail
export CARGO_TARGET_DIR="$PWD/target/scoped-change-review"
cargo build --locked -p motionwright-service --example studio_review_session -j 2
cargo test --locked -p motionwright-service \
  --test scoped_change_preservation \
  --test creative_undo \
  --test creative_production -j 2

export MOTIONWRIGHT_SERVICE_TEST_BIN="$CARGO_TARGET_DIR/debug/examples/studio_review_session"
export MOTIONWRIGHT_SERVICE_TEST_TMPDIR="$PWD/apps/studio/test-results/service-tmp"
# Optional when another isolated checkout uses the default Studio test port:
export MOTIONWRIGHT_PLAYWRIGHT_PORT=1431

cd apps/studio
npm ci
npx vitest run src/scopedChangeDraft.test.ts
npx playwright install chromium
npm run test:e2e -- \
  tests/scoped-change-preservation.spec.ts \
  tests/scoped-change-service.spec.ts --workers=1
```

The manual JSONL process can also be started from the repository root:

```bash
"$MOTIONWRIGHT_SERVICE_TEST_BIN" \
  --database "$CARGO_TARGET_DIR/manual-session.sqlite3" \
  --synthetic-fixture
```

Then send one request per stdin line, for example:

```json
{"id":1,"command":"bootstrap","args":{}}
```

The fixture flag seeds an empty database; it does not replace an existing
project. Supply a new isolated database when a test needs a fresh fixture.

### PowerShell on Windows

Run in a prepared Windows test environment with the repository at the path
below. Each external command is checked before the next stage starts.

```powershell
Set-Location 'E:\Projects\motionwright'
$motionwrightRepo = (Get-Location).Path
$env:CARGO_TARGET_DIR = Join-Path $motionwrightRepo 'target\scoped-change-review'

cargo build --locked -p motionwright-service --example studio_review_session -j 2
if ($LASTEXITCODE -ne 0) { throw 'StudioService test executable build failed.' }

cargo test --locked -p motionwright-service `
  --test scoped_change_preservation `
  --test creative_undo `
  --test creative_production -j 2
if ($LASTEXITCODE -ne 0) { throw 'Focused service regressions failed.' }

$env:MOTIONWRIGHT_SERVICE_TEST_BIN = Join-Path $env:CARGO_TARGET_DIR 'debug\examples\studio_review_session.exe'
$env:MOTIONWRIGHT_SERVICE_TEST_TMPDIR = Join-Path $motionwrightRepo 'apps\studio\test-results\service-tmp'
$env:MOTIONWRIGHT_PLAYWRIGHT_PORT = '1431'

Push-Location (Join-Path $motionwrightRepo 'apps\studio')
try {
  npm ci
  if ($LASTEXITCODE -ne 0) { throw 'Studio dependency installation failed.' }
  npx vitest run src/scopedChangeDraft.test.ts
  if ($LASTEXITCODE -ne 0) { throw 'Scoped draft editor-state regressions failed.' }
  npx playwright install chromium
  if ($LASTEXITCODE -ne 0) { throw 'Chromium test installation failed.' }
  npm run test:e2e -- `
    tests/scoped-change-preservation.spec.ts `
    tests/scoped-change-service.spec.ts --workers=1
  if ($LASTEXITCODE -ne 0) { throw 'Scoped change browser regressions failed.' }
} finally {
  Pop-Location
}
```

For evidence retention, pass a database path under the test's output directory
to `ScopedChangeService.create`. `close()` stops the entire process and keeps
SQLite/CAS available for a subsequent `start()` or inspection. `peer()` opens
an independent process on the same database, with its own grant registry.
`reopen(projectId)` replaces the connection inside the existing process and
discards its grants. `dispose()` removes only a temporary directory created by
the helper itself; it does not remove an explicitly supplied database path.

## Implementation references

- [Scoped workbench](../apps/studio/src/CreativePatchWorkbench.tsx)
- [Draft identity, recovery, conflict and receipt checks](../apps/studio/src/scopedChangeDraft.ts)
- [Studio API and editorial preview](../apps/studio/src/api.ts)
- [Creative undo domain](../crates/domain/src/creative_revisions.rs)
- [Service JSONL test executable](../crates/service/examples/studio_review_session.rs)
- [Playwright service helper](../apps/studio/tests/support/scopedChangeService.ts)
- [Testing strategy and evidence boundaries](testing.md)
