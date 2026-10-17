# Creative production: state and runtime ownership

Motionwright is responsible for authored creative projects, revisions, persistence, and editable Studio state. Semwright remains responsible for native runtime capabilities, Broker and Driver Host execution, policy and effect authorization, and source-bound receipts. The SDK dependency is pinned by [SOURCE_LOCK.json](../../SOURCE_LOCK.json).

## Project state and migration

Creative documents use a versioned project format so older writers cannot silently discard newer fields. Legacy source documents are admitted through explicit migration and serialized inside the same transactional project boundary. Unsupported formats are rejected rather than partially rewritten.

Project changes retain stable scene, component and object identities. Mutations are revision-bound and applied through StudioService and the existing SQLite compare-and-swap transactions. Source assets, rights declarations, locks, generation identity and prior user changes remain authoritative.

Exact round-trip serialization is required for persisted component geometry and editorial state. Floating-point round-trip decoding is intentional: source values are compared after database reopen, not weakened by tolerance-based migration claims.

## Rendering and native capabilities

The public integration surfaces include authored production plans, components, scoped creative changes and source-bound native observations. Semwright Native SDK calls preserve explicit resource identity, revision, output bounds, capability and effect-grant constraints. Imported creative capsules are data, not executable plugins.

Creative movement, camera and composition are admitted only where the target renderer can preserve their declared semantics. Existing linear position, bounded entrance motion and native media realization have distinct validation rules. Unsupported motion or missing authority must produce an explicit diagnostic instead of inventing an approximation.

## Verification and evidence

Domain, storage, native and Studio tests exercise serialization, conflict handling, source identity and bounded render contracts. Native outputs and package artifacts require the exact source revision and independently checked bytes. Tests and examples never substitute for original-media rights, installed-device acceptance or human creative review.

The source-linked requirement ledger is [requirements-status.json](requirements-status.json). Technical evidence, implementation scope and independent product acceptance are deliberately distinct. The integration does not create a second scheduler, a parallel persistence authority, automatic third-party model execution, or hidden publication rights.
