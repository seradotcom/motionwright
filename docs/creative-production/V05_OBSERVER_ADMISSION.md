# Motionwright v0.5 — independent technical observer admission

**SRS:** `MW05-E13-04 — Observación extensible`. A producer must declare a
method, version, units, coverage and limitations; an independent verifier
must pass a separate owner admission gate before its evidence can be used
technically. Merely installing a renderer never admits it as a certifier.

## Scope of this partial implementation

`tooling/observer-admission/verify_observer.py` is an **offline, data-only
technical admission prototype**, not a new Semwright Driver Host, policy
authority, creative judge, scheduler or executable plugin registry.

For the first exact method `png_dimensions_v1`, it enforces three separate
classes of provenance:

1. **An owner-approved verifier source descriptor** signed with Ed25519.
   It identifies an independently reviewed `.py` file by exact SHA-256,
   the approved measurement method/version/units/coverage, its limitations,
   and its narrow `observe_png_dimensions_only` permission. The gate **does
   not execute** that submitted source code; the supported implementation
   of actual PNG dimension readback is fixed and first-party.
2. **A separate owner-signed admission/revocation policy** specifying the
   admitted and revoked verifier identities at a monotonically updated
   owner generation. The caller also supplies an **owner-controlled trusted
   policy head** *outside* the untrusted evidence directory containing the
   current policy generation and SHA-256 of the exact canonical signed
   policy envelope. The gate rejects replay of a **previously validly signed
   old policy** after its revocation is recorded at that trusted head.
   Revocation prevents *new* accepted technical gate observations while
   retaining the prior signed historical evidence bytes intact.
3. **A distinct independent observer identity** signs one source-bound
   technical result. The admission explicitly binds verifier identity,
   Ed25519 raw public key, version, method, units, coverage, limitations,
   producer ID/source SHA, frame ID and PNG SHA-256. The verifier and owner
   public keys must differ, and the verifier ID cannot equal the renderer
   producer ID. The gate independently rereads and decodes the original
   PNG (not just a PDF/JSON receipt) and checks the exact claimed width,
   height and bytes.

The current fixed technical method accepts only a 4096×4096-or-smaller
original RGB/RGBA PNG, with explicit
`method=png_dimensions_v1`, `units=pixels`,
`coverage=single_full_frame` and bounded source SHA-256. It
**cannot certify** truthfulness, image appeal, typography readability,
animation continuity, rights, sound, or full-film technical conformance.
Adding another verifier or a more ambitious method requires its own
**independent owner admission and gate implementation**, with separate
negative tests. An imported Python module is never an implicit execution
grant.

## Inputs and trust anchor

A user/administrator who actually owns the project supplies:

- `--trusted-owner-key`: raw 32-byte Ed25519 **public** key from a
  trusted owner-controlled path outside the evidence bundle.
- `--trusted-policy-head`: a bounded JSON file, **also outside the
  evidence bundle**, controlled by that same owner/administrator, with
  schema `motionwright.observation-trusted-policy-head/1`, integer
  `owner_generation`, and
  `canonical_policy_envelope_sha256` (SHA-256 of canonical JSON for the
  currently signed `owner-policy.json` envelope). The head is changed by
  an authorized operation as part of an owner policy version update; it is
  not read from a project/producer-supplied file.
- `--admitted-verifier-source`: immutable original source bytes from
  a separately reviewed owner path, SHA-256 matching the signed admission
  descriptor. The validator inspects the bytes and **does not execute them**.
- `--expected-producer-sha256`: the exact source version SHA-256 supplied
  independently by the production coordinator/owner. The untrusted report
  cannot choose its own admitted producer source.
- `--root`: an untrusted bounded evidence directory containing
  `owner-admission.json`, `owner-policy.json`,
  `observed-frame.json` (each an explicitly signed envelope) and the
  referenced original PNG.
- `--output`: a **new** path for a technical-only result. Existing
  results are never overwritten.

Example invocation **after actual owner trusted keys have been separately
provisioned**:

```bash
python3 tooling/observer-admission/verify_observer.py \
  --root /path/to/untrusted/proof \
  --trusted-owner-key /path/to/owner/trusted-owner.pub \
  --trusted-policy-head /path/to/owner/current-head.json \
  --admitted-verifier-source /path/to/owner/reviewed-observer.py \
  --expected-producer-sha256 <64 LOWERCASE HEX DIGITS> \
  --output /path/to/NEW-observation-result.json
```

The trust of the **owner path**, freshness of that owner's policy head,
and independent operator sign-off cannot be established from a chat or from
a self-created document. The proof does not claim that the test's ephemeral
signing keys represent an actual Motionwright human owner. The true platform
gate still has to incorporate an authenticated owner policy source.

## Negative tests and limits

`test_observer.py` covers distinct owner/observer identities, forged
signatures, rejected self-certification, false image dimensions even with a
valid observer signature, modified original PNG bytes, stale/missing owner
head, replay of a previously signed unrevoked policy, unadmitted verifier,
changed source code, changes to method/units/limitations, symlinked source,
and unknown fields attempting to grant installation/publication authority.
Revoked evidence remains inspectable but `gate_eligible=false`.

GitHub Actions runs tests and generates a **synthetic, source-bound 320×180
real PNG** with **temporary, destroyed** Ed25519 owner and observer keys,
demonstrating successful technical admission and subsequent policy
revocation. The artifact explicitly contains **no private keys**,
no real owner authentication and **no production gate acceptance**.

**Still NOT implemented:** owner-authenticated policy head persistence in
the canonical StudioService/Native SDK, independently provisioned production
public keys/rights, semantically richer observation methods,
separate real renderer-host conformance, optional paid/source-code
verifier installation, multi-machine revocation propagation, and human
creative quality approval. This E13-04 addition remains **PARTIAL** until
those critical acceptance gates have demonstrated real results.
