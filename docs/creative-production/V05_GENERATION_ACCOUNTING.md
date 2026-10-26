# V0.5 E12: generation intent and source-owned budget journal

This slice restores and adapts the **data-only** accounting contracts from the
superseded experimental PR #100 into the *current* Motionwright project model.
The current Semwright Native SDK pin is recorded in
[`SOURCE_LOCK.json`](../../SOURCE_LOCK.json). There is no parallel
backend, no external provider connection, no billed generation, and no
new identity/consent authority. **Release acceptance remains false.**

## What is implemented

The canonical `Project.paid_generation` journal exists outside creative
branch snapshots so an alternative scene or branch cannot reset an
unresolved reservation. The field is optional/empty for legacy format-2
documents, and nonempty data is refused in format-1 documents.

A source-bound **pilot** records its scene, campaign, kind (original image,
video, TTS or presenter study), prompt digest, declared model and capability,
usage policy digest, immutable imported source media identities, fixed
maximum cost in **integer USD micro-units**, and a strict semantic
idempotency key. The key remains identical across a regenerated request
UUID, budget cap changes, revised owner notes, disclosure text, policy
metadata updates, and ordering of the *same* source input set. Distinct
campaigns, scenes, media, models, prompts, inputs, and batch variants
remain distinct.

All writes are existing `Change::EditPaidGeneration` mutations performed
through **StudioService SQLite CAS** and the existing
`driver.motionwright.paid-generation.edit` operation of the public Semwright
Native SDK. Project and scene content locks remain authoritative. There is no
browser-demo fallback that can mutate paid accounting.

A single reservation can be recorded after explicit preflight and a separate
UI confirmation. The reservation is **not a payment** and does not authorize
execution. Unknown provider outcomes and task IDs survive SQLite reopen,
portable reimport and creative branch changes. They must be reconciled by
querying only the original known task or semantic idempotency key; the app
cannot retry or launch a new paid operation automatically. External receipt
identities, payment and provider truth remain unverified.

A batch reservation is blocked until a matching original pilot is reported,
its exact media asset is imported and an owner-declared pilot review is
recorded. Such a record is not a cryptographically authenticated approval.
The provider route preview compares declarations only, never installs or
executes a model. Neither user-declared consent nor a digest is a trustworthy
authorization to clone a real person's face/voice. **Human likeness and
consented-person work is refused by this ledger**, pending a separate
authenticated integration with the already-existing identity registry.

## Desktop experience

Production → Generation journal shows the saved accounting history,
declared budget ceilings, uncertain provider tasks and a bounded original
synthetic pilot form. Previewing never commits; saving a pilot and reserving
a cap are two distinct project mutations. A pending response is tied to the
exact project ID/generation/revision/scene, and abandoned if navigation or
revision changes while the preview is running.

The desktop preflight performs current project reads **before and after**
domain validation. The TypeScript boundary rejects stale project identity,
unrecognized fields, missing/ambiguous success flags, unauthorized
provider/payment claims, changed normalized specifications, inconsistent
amounts (including totals recomputed from the same canonical project revision)
and a fabricated task reconciliation status. This is defense in
depth, not cryptographic authentication of a Tauri process. The canonical
SQLite service is still the source of truth.

The browser demo is explicitly read-only. No provider credentials, payment
methods, original prompts, identity sources, remote task receipts or
private project journal records are sent to GitHub artifacts.

## Adversarial proof

- Domain tests: duplicate attempts despite changed cap/notes/order, source
  and campaign distinctions, budget overflow, pilot-before-batch, forged
  states/events, unknown task drift, malformed media/identity.
- Service tests: repeated reopen, stale writes, **two independent SQLite
  clients racing to reserve** the same source revision, and portable reimport
  preserving unknown outcomes and obtaining a fresh CAS generation.
- Native SDK tests: descriptor remains target-bound, CAS, durable request-key,
  application-local context cannot become a privileged host invocation,
  strict no-execution edit schema.
- Studio unit/browser tests: strict receipt parsing, manipulated provider
  claims, two distinct confirmation actions, change-scene race, absence
  of automatic paid operations.
- CI runs all heavy Rust/Studio/Chromium suites on disposable GitHub runners.
  Success proves *these code contracts*, not paid provider correctness.

## Still outside scope

An authorized real provider adapter, verified source rights, authenticated
consent and ownership, authentic billed-spend observations, real output
quarantine/admission, human approval of a pilot, commercial credits, batch
execution, full owner-local two-client acceptance and real user testing
remain unimplemented or unverified.

This is **partial** coverage of E12-01/02/03, not completion of v0.5.
The 60 independent human acceptance scenarios remain `NOT_RUN`.
