# Motionwright v0.5 — atomic, source-bound brand campaign rollout

**Scope: E14-01, technical partial.** This capability is deliberately
bounded to a selected set of existing original, attached **Hero** components
inside one saved `Project`. It does not distribute brand policy across
organizations, channels, remote workspaces or mixed third-party renderers.

## Use inside the editor

1. Save an original, versioned `BrandProfile` under
   **Production → Brand & taste**. It must contain an allowed accent
   palette and/or required wordmark; `TasteProfile` remains only advisory.
2. In **Propagate brand to selected scenes**, explicitly choose 1–8
   existing attached Hero scenes, one exact palette color and/or the
   *current mandatory* wordmark. Specify the campaign, reviewer declaration
   and reason for the proposed change.
3. A read-only source preview displays every selected scene's previous
   and proposed colors and wordmark. It includes the exact project identity,
   generation, current revision and SHA-256 digest of the **saved** brand
   policy, never a newly invented provider manifest or unverified brand
   account.
4. The owner separately acknowledges semantic component rebuilding,
   compatible manual overrides, the exact rows and project CAS scope.
   Only the desktop can submit the operation.
5. `Change::ApplyBrandCampaign` rechecks the saved profile, digest,
   selected scenes, review/lock status and current revision *inside Rust*.
   It applies the existing `upsert_product_hero` three-way merge to
   a **private candidate Project**. If any selected scene, authored node,
   style lock, revision, policy exception or capacity rejects the
   update, no earlier scene's changes are published.
6. The candidate is validated before replacing the old project.
   StudioService persists **one revision** under its existing SQLite
   optimistic compare-and-swap request key. Source-bound decision records
   document each changed scene and the owner-declared campaign and policy
   digest; they do not authenticate source ownership. The public
   `driver.motionwright.brand-campaign.owner-rollout` Native SDK
   capability uses the **existing** target/grant/CAS path, without
   implementing a new broker, effect manager, scheduler or renderer.

## Security and creative quality boundaries

- No arbitrary assets, external source, executable code, network service,
  publishing account or model provider can be injected into the rollout.
- The policy can authorize **only** `allowed_accents` and
  `required_wordmark`. Forbidden phrases remain enforced by the
  regular project validator and cannot be waived by propagation.
- Existing brand exceptions on a selected affected rule must be
  independently resolved first; this operation cannot erase or widen them.
- Existing scene approval, unresolved review, project and scene locks,
  manually overridden generated properties and native semantic disclosure
  survive via the original component update/lock validators. Any conflict
  fails closed, rather than flattening the authored composition.
- Owner identity is a *self-declared string*. The source policy digest is
  not a signature or an organizational IAM grant.
- The browser demo may preview its current project but cannot mutate
  brand campaigns. After desktop submission the UI says **requested**,
  not **committed**, until Studio's canonical revision/decision state
  actually changes. A resolved UI callback does not prove SQLite CAS.
- No physical cross-aspect/native video has been reviewed. Human
  typography, legibility, tone, design quality, correctness of
  commercial brand rights and downstream launch acceptance are not
  inferred from numeric or source-policy checks.

## Adversarial test inventory

- Rust domain: multi-scene existing Hero identity preservation,
  forbidden/missing policy sources, duplicate/foreign scene refs,
  tampered generation/digest/owner acknowledgement, project/scene
  locks, protected approvals and late second-scene atomic rollback.
- SQLite: exact one-revision commit with two decision records,
  persistent reopen, durable same-request replay, stale re-request
  rejection, two independent owner clients racing for one revision,
  portable import fresh generation refusing a stale proposal.
- Native SDK: observable, mutating, target-bound CAS descriptor and
  refusal of application-local context or untyped executable fields.
- TypeScript: mirror policy/digest/scene checks, preview-only mode
  and no-op prevention.
- Chromium: explicit preflight → acknowledgment → one canonical CAS
  with synthetic Tauri transport, and a failed late CAS that does not
  produce an invented success claim.

**E14-01 remains PARTIAL.** Cross-project campaign consistency,
multi-renderer media equivalence, remote brand distribution, signed
owner IAM, international copy/locale/font enforcement, native viewport
readback and independent human creative approval remain NOT_RUN.
The 60 independent creative acceptance scenarios and v0.5 release
authorization remain false.
