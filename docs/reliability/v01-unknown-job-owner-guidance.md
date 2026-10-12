# v0.1 — Owner-native UNKNOWN job recovery guidance

**Scope: UI-only, read-only safety information for a previously started native job.**
This does **not** fix the pinned Semwright provider generation disappearing
(issue #49), cause native reconciliation, authorize retry, or satisfy human acceptance.

The Jobs workspace reads locally persisted Motionwright production receipts.
When a currently applicable Motion Canvas or MLT job is projected as
`outcome_unknown`, an accessible warning explains that the original operation
has not been confirmed. When a cancellation is requested but no definitive
terminal response exists, it explicitly says cancellation is unconfirmed.
The warning neither retries a render nor treats a local file or incomplete
receipt as a successful native output.

## Operator boundary

1. Find the original job in the installed app's Jobs table and privately
   preserve its source project generation/revision, opaque job identity and
   original owner receipt. Do **not** copy session tickets to public issues.
2. Reconcile that **same** job only through the authorized original Semwright
   Broker / Driver Host. A different job, provider generation, project
   revision or existing local PNG cannot prove the original outcome.
3. For an existing acknowledged Motion Canvas job, the optional [owner-initiated original status read](v01-owner-native-status-read.md) checks the same job and provider generation through one read-only Semwright Broker operation after owner action. This is a status observation, not media verification or a mutation. The local persisted Jobs ledger remains unchanged.
4. If the provider generation is unavailable or the terminal result cannot
   be verified, leave the original state UNKNOWN and avoid automatic mutation.
   An independently approved new production request must be a deliberately
   new operation and not a covert retry of the prior mutation.
4. `Refresh receipts` and `Full history` only query Motionwright's local
   store. They do not reach Semwright, restart providers or consume grants.
   Historical receipt pages may be incomplete until the source-bound snapshot
   is fully enumerated.

This warning uses locally projected state; even an empty table does **not**
prove that no upstream native job exists. Full provider reliability and actual
installed-owner recovery remain external release blockers under issue #126.

## Regression evidence

`apps/studio/src/nativeRecovery.test.ts` tests current/historical boundaries,
terminal and nonterminal cancellation, unknown priority and no retry authority.
`apps/studio/tests/v01-native-recovery.spec.ts` validates visible behavior
and absence of native command requests in the browser bridge. Both run in CI.

The owner UI is not a source of Broker authority and does not claim that
Issue #49, any v0.1 requirement, or the 60 independent cases have passed.
