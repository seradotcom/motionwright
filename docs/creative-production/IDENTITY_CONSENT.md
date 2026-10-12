# MW05-E12-04 · Identity origin and consent boundaries

Status: **partial implementation**, not production-ready identity generation,
human consent authentication, or a complete v0.5 feature.

## Scope and executable authority

An immutable motionwright.identity-origin/1 declaration can be registered
through the existing project SQLite journal, the Semwright Native SDK and
Studio's **Production → Identity & consent** workstation. It distinguishes
two kinds of sources:

- **Entirely original fictional characters**. The declaration references an
  imported content-addressed JSON provenance asset, an originating owner
  assertion, approved modalities, a limited purpose and a bounded UTC expiry.
  A fictional original must carry the visible output disclosure
  SYNTHETIC ORIGINAL CHARACTER / NOT A REAL PERSON.
- **A likeness of an actual human**. The declaration requires a second,
  distinct content-addressed JSON consent-evidence asset, a named subject,
  a specific recorded consent scope and the disclosure
  SYNTHETIC HUMAN LIKENESS / CONSENT REQUIRES INDEPENDENT REVIEW.

Neither declaration authenticates a person's identity, consent signature,
legal rights or a provider's actual output. They are **author assertions and
file identities**. A consent file with a valid SHA-256 is not independently
verified permission. An operator must inspect and authenticate consent
externally, and the source may not be used for likeness cloning automatically.

The only modalities admitted are voice, face and presenter. The only purposes
are product demonstration, marketing, education and internal review. Every
source declares nonempty, nonduplicated modalities/purposes, an origin,
canonical UTC issue and expiry instants, a maximum 366-day validity interval,
and original author/rights text. Empty consent, an omitted disclosure, unknown
configuration fields, a missing/forged JSON source, a duplicate source digest
or an unauthorized project generation is rejected.

**No identity-generation capability or paid provider API has been added.**
Registration is not authorization to synthesize a face, clone a voice, or
publish a presenter. The advisory preflight can only return blocked states
(revoked, expired, outside scope) or a requirement for owner approval /
independent human consent verification. It never returns an execution grant.

## Immutable evidence and revocation

The project's versioned ProductionDesign holds up to 32 original identity
declarations and 32 append-only revocation events. A registration cannot
replace an existing identity or reuse its original JSON source or consent
digest under a different ID. Revocation is a new CAS-protected event with
a canonical UTC timestamp and nonempty reason. It immediately prevents any
subsequent positive usage review, but preserves the source assets and
historical project record. Removing a referenced origin/consent asset fails.

The historical registration generation is preserved during an authorized
portable project import that rotates the *current* project generation;
any new registration must use the new current generation. SQLite durability
and request-ID replay retain canonical revision authority. Neither Studio nor
a separate agent can clear revocation by editing a prompt.

## Test coverage and remaining work

- Rust domain: source/consent association, time and scope constraints,
  required disclosure, exact consent/identity categories, duplicate/reused
  sources, malformed serialization, revocation and project round-trip.
- SQLite: actual imported JSON source blob metadata, project CAS, idempotent
  request replay, stale/foreign requests, historical provenance, blocked
  source deletion and retained revocation.
- Studio: independent TypeScript validation and preflight parity, revision
  guard, explicit imported JSON selectors and no generated media route.
- Browser: empty-source and unverified-human fail-closed controls.

The exact GitHub commit must pass its Rust, Studio, browser and platform
checks. Tests of source identity and declaration are not a human consent
review or a media-rendering demonstration.

**Still open:** trusted consent-signature and scope verification, source-
document semantic ingestion, authenticated owner approval, privacy rules for
the actual likeness asset, provider-safe admission, synthetic labeling on
every rendered frame/audio output, expiry/revocation enforcement by future
paid providers, and real-human pilot testing. Until those are independently
implemented, MW05-E12-04 remains NOT_COMPLETED.
