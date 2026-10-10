# Motionwright v0.5 — Paid generation ledger (E12)

**Scope:** SOURCE/DOMAIN implementation of the four E12 delta requirements:
`MW05-E12-01` capability and generation contract, `E12-02`
sample/pilot before batch and exact reserved cap, `E12-03` unknown outcome
and provider task reconciliation, and `E12-04` declared identity/consent.

**Status:** implementation candidate and CI contracts only. No paid API call,
provider authentication, actual spend, identity verification or user
consent authentication has been executed in this branch. No provider SDK,
billing, scheduler or worker was created. The existing
`Project → ProductionDesign → PaidGenerationLedger` is serialized and
committed through the canonical `Change::EditPaidGeneration` and
`StudioService.apply`/SQLite revision CAS, preserving history/undo/backup.

## Paid request and event contract

An original `PaidGenerationSpec` binds the exact project+campaign+scene,
one capability (`image`, `video`, `tts`, `presenter`), original
prompt SHA-256, input asset IDs and digests, usage rights, provider/model
and version, and exact capability/rights policy declaration SHA-256.
Provider declarations are classified **unverified** until independently
authenticated by a real adapter. The immutable logical request key
does **not** include a newly generated job UUID. An attempt cannot be
charged twice just by changing that UUID. Each intentional batch
variant has a distinct explicit index; only variant 0 is the pilot.

Prices use **integer USD micro-units**, not floating-point estimates.
A request can reserve its exact maximum once, against a bounded global
reservation budget. Attempts are append-only events with a unique event ID
and expected previous history SHA-256, in addition to the existing
StudioService project revision CAS.

The bounded state transitions are:

```text
DRAFT
  → RESERVED                      (one exact maximum charge)
  → UNKNOWN_OUTCOME               (submission MAY have happened)
  → UNKNOWN_OUTCOME               (query-existing-task result still pending)
  → PROVIDER_REPORTED_SUCCESS     (reported provider result, UNVERIFIED)
      → OUTPUT_ADMITTED           (separate exact local media import/review)
          → PILOT_APPROVED        (recorded owner sample review; not identity-authenticated)
  → PROVIDER_REPORTED_FAILURE     (declared outcome; not authenticated provider proof)
```

The output receipt's exact bytes have to be ingested into the original
project as an `Asset` with matching SHA-256 and suitable media type
before the `AdmitOutput` event. Neither a provider's declared success
nor a pleasant preview automatically inserts an asset.

A `Batch` request may exist as a draft, but its first reservation fails
until the referenced `Pilot` is both **admitted** and **owner-recorded
approved** with the exact same campaign, provider/model/version, kind and
accepted source SHA-256. Every later batch unit must still use a different
variant index and logical key. A timeout cannot be treated as final failure,
and the `reconcile_query` output instructs a future effect-authorized
adapter to **query the original task ID or logical idempotency key**, never
to create a second paid task. Repeating a poll receipt or changing the
original provider task ID is rejected. Unknown budgets remain explicitly
reserved rather than silently refunded/rebilled.

### Rights, human identity and source fidelity

Original synthetic content requires a visible synthetic disclosure. For a
person's real face/voice/presenter, the source must be imported as an
approved asset, with exact media SHA, source-rights note,
purpose-specific consent receipt SHA and affirmative owner declaration.
That declaration **is not cryptographic consent authentication**.
A model-authored value is not evidence of the person's real consent;
the real provider adapter/owner must verify and enforce this independently.

A pure `preview_paid_provider_route` compares declared provider
capabilities against kind, exact model/version, accepted input mime,
rights policy, budget cap and declared identity support. The result
always sets `owner_execution_grant=false` and
`provider_capability_signature_verified=false` regardless of matches.
No generated source can opt into paid execution via JSON fields.

## Evidence and limits

`crates/domain/tests/paid_generation_ledger.rs` covers unknown outcomes,
deduplication by semantic key, stale event histories, strict identity
rights/usage, pilot-before-batch, sampled media import, cost bounds,
unverified provider routing, and forged storage/event states.
`crates/service/tests/paid_generation_persistence.rs` reopens SQLite
with the same unknown paid task and reserved cost; attempts to write with
a stale project stamp fail without changing the journal. The v2
schema cannot silently carry these new paid records to an old writer.

**Not done:** real image/video/TTS/presenter provider SDK/API or capability
attestation; safe task dispatch and cancellation under Semwright grants;
authentication of provider reconciliation replies and exact billing;
signed owner consent or pilot review; actual asset generation and ingestion;
a human-tested batch preview workflow. These are mandatory gates to close
E12 and advertise paid generations as available.

This feature is **not** a payment method, subscription, provider recommendation
or automatic task retry. Reserve/declared spends are editorial bookkeeping,
not invoices, charges, or an authorization to spend customer money.


## Desktop-only paid job intent panel

The Production workspace includes a `Paid generation` tab. It uses
`native_paid_generation_preflight` to validate an owner-authored,
source-digest-bound synthetic pilot request and produce a normalized
`Change::EditPaidGeneration`, with `committed:false` and
`provider_called:false`. A separate **explicit Save** action uses the
existing StudioService revision CAS to persist that draft.

For an existing Draft, `native_paid_generation_reserve_preflight` returns
one expected-history-bound `Reserve` event. Committing it records
**bookkeeping only**; it neither executes the provider nor charges any
payment account. The direct journal view lists declared provider and
model/version, immutable attempt SHA, reporting state, exact 6-decimal USD
micro-unit budgets, unknown outcome and the query-existing-task-only hint.
It deliberately cannot submit provider calls, approve a pilot, impersonate
a person, or pretend the browser demo has real balances.

The Tauri command is read-only until the user commits the normalized
domain change. It refuses stale project revision and generation. The
browser-only Vitest guard rejects every paid-generation observation,
preflight and reservation call in the absence of the local native service.
Recorded provider capability receipts, policy digests and task IDs remain
**unverified declarations**, not verified provider integrations or
ownership/consent authentication.

A later feature must provide a trusted provider adapter with end-to-end
exact idempotent dispatch, cancellation, authenticated provider-state
reconciliation, explicit owner spending/consent policy, asset download
and quarantine/admission before any paid generation can be enabled.
