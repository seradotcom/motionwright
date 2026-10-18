# Saved delivery recipes

Each delivery profile can retain its own Film scene intent and original audio mix recipe. These are editable project data: an ordinary project revision stores them in SQLite, the project journal includes the change, and a portable project bundle carries them into a new store. A recipe does not approve a render or establish the rights to its source media.

## Authoring and reviewing

In **Deliver**, choose a delivery profile, then edit the scene roles and archetypes in its Film controls. Choose **Save scene intent** to commit that group. In **Audio**, select the delivery language/profile in the original mix review, choose the already imported original music and SFX sources, and edit its SFX gain and ducking envelope. Choose **Save original mix recipe** to commit the mix group.

The two groups are independent. Saving scene intent keeps an uncommitted mix draft, and saving the original mix keeps an uncommitted Film draft. Neither operation rewrites another profile's recipe. Controls use the observed canonical reply to recognize the submitted snapshot; resolving a callback by itself does not mark an edit as saved. Typing that occurs during a request remains available after its reply, including an intentional return to the value that preceded the request.

On an overlapping change, the review shows the field's starting value, its current saved value and the local draft. **Keep saved** and **Keep my draft** resolve that field's base. These choices do not commit a project change. The author saves again after reviewing the conflict. Unchanged fields follow the current project without replacing unrelated local inputs.

A draft belongs to one project ID, generation, branch, profile and group. Changing the profile or reopening another generation does not transplant that draft into the new scope. Local recovery copies preserve editable raw values, including invalid numeric input. Other document writers' copies require an explicit restore. A malformed owned copy remains untouched and blocks that editor's save until the author explicitly discards it. Storage failure or capacity exhaustion reports that the newest input may not survive closing the document; it does not claim that a backup succeeded.

The recovery budget is 256 KiB per copy and 64 copies / 2 MiB across recipe scopes. Dirty and unreadable copies are not removed automatically. A fresh document owns its own writer identity; duplicating a tab's session storage does not grant it ownership of the previous document's copy.

## Canonical data and changes

The schema remains Project schema 2. `production_design.delivery_recipes` is an optional map keyed by the delivery profile UUID. An absent or empty map has the historical representation; no empty recipe field is emitted merely because a project was opened.

```ts
type DeliveryRecipe = {
  schema: "motionwright.delivery-recipe/1";
  scene_intents: Array<{
    scene_id: string;
    role: string | null;
    archetype: string | null;
  }>;
  original_mix: {
    music_asset_id: string | null;
    sfx_asset_id: string | null;
    sfx_gain_db: number;
    duck_attenuation_db: number;
    duck_attack_samples: number;
    duck_release_samples: number;
  } | null;
};
```

The actual TypeScript and Rust types use closed role and archetype enums. Roles are `hook`, `problem`, `mechanism`, `evidence`, `comparison`, `reveal`, `payoff` and `cta`. Archetypes are `statement`, `split_explanation`, `architecture_reveal`, `comparison`, `metric`, `timeline`, `code_focus`, `diagram_build`, `object_spotlight`, `evidence_frame`, `product_proof` and `endcard`.

```json
{
  "type": "set_delivery_recipe",
  "deliverable_profile_id": "<existing profile UUID>",
  "recipe": "<DeliveryRecipe object, or null to remove the saved recipe>"
}
```

Recipe changes use the existing project effect grant and compare-and-swap revision boundary. Referenced profiles, scenes and assets must exist. An asset or profile still referenced by a recipe cannot be removed while leaving a dangling reference. A partial recipe is valid authoring state; the native realization performs its stricter admission check before it executes.

SFX gain accepts -80 through +12 dB. Duck attenuation accepts 0 through 30 dB. Attack and release are whole sample counts from 1 through 48,000. The global `project.audio.mix` continues to hold voice and music gain and intended loudness/true-peak targets. The per-profile recipe does not create a competing global mix authority.

Clearing a group is another explicit canonical change. The recipe operation currently uses ordinary project history and an explicit inverse edit; it is not part of the narrower `UndoCreativePatch` mechanism. Branch workspaces preserve the production design, with the existing whole-production-design branch conflict behavior. This feature does not claim field-level branch merge for the new map.

## Native realization boundary

Film options derive from the saved scene intent and the selected profile's canonical frame rate. The current Film path uses its supported Instrument Sans Variable and IBM Plex Mono font configuration. A caller cannot supply a conflicting unsaved scene mapping and then obtain a grant by presenting the saved profile's identity.

Original mix options derive from the saved recipe and the selected canonical voice. The current native boundary requires the supported full-length, distinct original voice/music/SFX stems, its documented 48 kHz stereo format and duration limits. A selected profile's explicit voice must agree with the active voice supported by that boundary. Unsupported semantic SFX scheduling is rejected rather than flattened into a claim of faithful scheduling.

Preflight/render controls remain blocked while the relevant draft is dirty, awaiting canonical confirmation, conflicted, based on a later revision, or blocked by an unreadable owned recovery copy. New rights confirmation is required at the realization boundary; it is not stored as reusable consent. The fixed native true-peak guard remains separate from the editable target. Frame windows, source digests, derived timing and font measurements remain derived evidence, not fields an operator can mark as verified by editing the recipe.

Changing a recipe does not itself execute Film, play a mastered result, approve a review, or establish visual or listening quality. Native artifact inspection and required human review are recorded separately against the realized output.

## Verification

The Rust domain and service suites cover references, numeric bounds, persistence, repeated revisions, conflict/lock rejection, removal and the existing compare-and-swap replay behavior. Build the opt-in real-service JSONL executable before running the Studio interaction suite:

```sh
cargo test --locked -p motionwright-domain
cargo test --locked -p motionwright-service --test delivery_recipe_persistence
cargo build --locked -p motionwright-service --example studio_review_session
```

Set `MOTIONWRIGHT_SERVICE_TEST_BIN` to the absolute path of that executable. Then run the Studio unit and browser tests with the repository's configured Node runtime:

```sh
npx vitest run src/deliveryRecipe.test.ts
npm run test:e2e -- tests/delivery-recipe.spec.ts --workers=1
```

The browser cases exercise the actual StudioService, grants, SQLite journal and CAS through explicitly identified test transport. They cover two profiles and both groups, multiple revisions, late replies, scope changes, conflicts, raw invalid input, recovery failures, locks/revoked grants and export/import into an independent store. Stores are closed completely and copied without filtering SQLite sidecars. Their full Project and journal observations accompany the sealed snapshots for independent reopen comparison.

Existing Film and native-audio UI contract fixtures also save a canonical recipe before requesting preflight. Those fixtures use explicitly simulated native responses and certify their UI contracts only. They do not substitute for the real-service persistence tests, a Tauri installation test, native renders or required human evaluation.

## Narrative cut editing

The full sequence and a custom cut are explicit choices in the output profile editor. A custom cut can temporarily contain no scenes while it is being edited. The editor preserves that empty choice across revisions, profile switches and local recovery, and asks the author to include a scene or choose **Use full sequence**. An empty custom cut cannot be saved or used for Film, native production or export. Its local mode is not added to the Project schema: the canonical empty scene list continues to represent the full sequence.

The local mode and scene list are reviewed together when another author changes the cut. Keeping the saved cut resolves both parts without discarding unrelated metadata input. Saving a valid custom cut retains the approved Project scene order. A delayed save reply confirms only its submitted selection; a later empty selection remains available for review.
