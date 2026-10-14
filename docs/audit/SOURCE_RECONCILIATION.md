# Motionwright source reconciliation and corrective audit

## Scope and reproducible cut

Baseline: `801b235607ddcd18f22fe598b468dc5b43811c0a` (merge of PR 193).
Corrections live on a separate audit branch; the baseline report is historical, not a claim about whatever main becomes later.

`BASELINE_801b235.json` inventories every original requirement, every creative expansion requirement and every acceptance identifier. It reads the immutable Git tree rather than trusting mutable working files. Each source pointer names its Git blob and is classified as implementation, test definition, workflow definition, documentation or metadata. It never treats a test or workflow definition as an executed result.

Reproduce to a NEW output path:

```sh
python tooling/reconcile_requirements.py --source-sha 801b235607ddcd18f22fe598b468dc5b43811c0a --output /outside-checkout/source-reconciliation.json
```

The original private catalogs were not available as readable archive bytes in this audit environment. Consequently this is **complete ID/pointer reconciliation plus a targeted source-defect audit**, not a complete criterion-by-criterion semantic reassessment or installed-product acceptance. Original criterion text must not be reconstructed from test names or published here.

## Recorded status, not development percentages

| Scope | Recorded status at the baseline |
| --- | --- |
| Original requirements | 208: 107 EVIDENCE_PRESENT, 93 PARTIAL_OR_GAP, 8 OPTIONAL_UNCLAIMED |
| Expansion requirements | 64: 42 partial, 4 bounded_implementation, 7 inherited_not_reassessed, 11 not_implemented |
| Private acceptance identifiers | 60 NOT_RUN; none independently accepted |
| Original requirements pointing to source changed since their ledger last changed | 187 of 208 |
| Original requirements without a directly linked test-definition file | 175 of 208 |
| Expansion requirements without a directly linked test-definition file | 25 of 64 |

The final two rows describe deficient *trace links*, not absence of tests. A workflow may execute tests not linked in a particular row. Likewise, changed source does not prove a requirement is satisfied, missing or broken. The 51.4% evidence-present fraction is not a defensible code-completion percentage.

The original ledger last changed at `c4a823a`; the expansion ledger last changed at `bfe4d1b`. Identifiers, recorded statuses and all 60 NOT_RUN cases are preserved by this audit.

## Reproduced and corrected defects

### MWAUD-001: Studio can race imports and replace its active view

The original asset and voice import callbacks checked React `busy` but never acquired that common busy state. Normal edits relied on asynchronous React state updates alone. All three callbacks eventually published a captured bootstrap object unconditionally. A pending response could therefore overwrite a later visible project/generation/revision; two events could also dispatch before a React update made `busy` visible.

`ProjectMutationGate` now acquires synchronously and is shared by normal edits, asset import and measured voice import. The pending indicator covers all three. Publishing requires the current source identity/revision to match the original request and a non-regressing matching response. A changed active view is retained. Failures release the latch; there is no queue, retry, cancellation claim or new backend authority. Native SDK CAS and effect grants are unchanged.

Unit tests cover simultaneous requests, project/generation/revision switches, malformed or regressing responses, stale dispatch and failure cleanup. Browser tests exercise delayed synthetic asset and voice IPC through the actual App callbacks. Browser mocks are not installed-native import evidence.

### MWAUD-002: false-positive public acceptance metadata

On the unmodified baseline, all four synthetic counterexamples returned exit zero:

| Counterexample | Baseline result |
| --- | --- |
| PASS with `independent: "false"` and `[{}]` | product_pass=1 |
| PASS pointing to a nonexistent receipt | product_pass=1 |
| Requirement evidence `.` (a directory) | accepted |
| Absolute requirement evidence outside the checkout | accepted |

The public checker now requires a literal Boolean independence flag, canonical in-repo regular files, no symlinks or Windows reparse components, no duplicate JSON fields/nonfinite values, and nonempty evidence for evidence-present/verified claims. Public acceptance descriptors must bind a curated case-scoped JSON receipt by path, SHA-256 and size. NOT_RUN cannot contain review/evidence; missing, tampered, duplicate or foreign-case receipts are refused.

The separate release-readiness reporter already remained BLOCKED on the baseline. These defects are **not demonstrated release-authorization bypasses**. Hashes and Boolean fields still cannot authenticate a human reviewer or prove the private scenario was performed. No public acceptance record is promoted.

### MWAUD-003: expansion could preserve only a nonempty substitute ledger

The expansion checker previously required only a nonempty original `requirements`/`cases` field and followed caller-provided ledger locations. It now requires the exact canonical original paths and validates all original 208/60 identities and evidence invariants using the shared checker. Expansion rows also reject malformed collections and linked evidence outside source. All 64 delta identities remain unchanged.

### MWAUD-004: native host presence was mislabeled as a runtime connection

Studio previously described any Tauri host as Native SDK connected. This does not establish that a Semwright Broker, provider or native renderer is healthy or authorized. The header now reports desktop mode, with a pin/status disclaimer. The Integrations inspector remains authoritative for native runtime compatibility. Browser and synthetic desktop tests validate the label, not an actual provider connection.

### MWAUD-005: portable bundle restore bypassed the shared editing latch

A verified portable project import previously called `importProjectBundle` independently and published the returned project through `onImported` without synchronizing with ordinary edits, asset imports or voice imports. This could replace the visible project while a previous source edit was still pending, even though the native storage/CAS operations remained separate. Portable import now acquires the **same synchronous view latch before the effect grant and IPC**. The response must contain a distinct nonempty project ID and a rotated nonempty generation with a nonnegative revision, and the previous project snapshot must still be current before publication. Existing renderer receipts and editorial selection are reset only for an admitted replacement. Errors do not retry the import or claim backend rollback; owner reconciliation remains necessary after an unknown remote outcome. Unit tests and delayed synthetic-IPC browser tests cover concurrent edits, malformed responses, source switching, and follow-up editing. These tests are not native installed-device acceptance.

### Updated immutable-source reconciliation (after the baseline)

The automated inventory was also rerun **without changing the original baseline JSON** against `origin/main` `7a0000f9a133a40db127c753cd0339293e2cba08`. The 208 v0.1 entries remain 107 `EVIDENCE_PRESENT`, 93 `PARTIAL_OR_GAP`, 8 `OPTIONAL_UNCLAIMED`; all 60 independent acceptance cases remain `NOT_RUN`. The v0.5 source ledger now records **43 partial, 4 bounded, 7 inherited without reevaluation and 10 not implemented**: `MW05-E11-03` (bounded OTIO/source-media interchange) advanced from `not_implemented` to `partial`, with human receiver, third-party rights, full property fidelity and actual StudioService import still open. These are reported ledger labels, **not** an independent criterion-level audit or proof of product completion. The owner-private catalog bytes remain unavailable through this audit's connector.

### MWAUD-006: exact-source CI failures must remain distinct from product defects

CI for `ec4fe7104d2cf8c839ed7b0d3f3c530644f5084a` initially reported three red jobs, not three independently confirmed product regressions. The `studio` job ([run 38514359833](https://github.com/seradotcom/motionwright/actions/runs/38514359833)) and `variant-studio` ([run 38514359924](https://github.com/seradotcom/motionwright/actions/runs/38514359924)) failed on the same incorrect new unit-test expectation: it captured the old `visible` snapshot before the asynchronous import completed, then compared that old snapshot to the freshly imported result. The test now pins an explicit expected replacement and verifies that the visible project equals the admitted replacement; its post-import edit advances one revision. The substantive guard assertions are retained. The security source-boundary job ([run 38514359953](https://github.com/seradotcom/motionwright/actions/runs/38514359953)) failed because its blanket Python regression discovery ran source-history integrity tests on `actions/checkout`'s shallow default. The source-boundary checkout now fetches complete Git history with credentials persistence disabled. The source-traceability workflow already required full history; **no test is skipped or allowed to claim valid drift on a shallow clone**. Exact-SHA reruns remain required before promoting the PR.

### MWAUD-007: failed profile commits could produce a false saved selection

`App.commit` deliberately caught errors for legacy fire-and-forget editor controls but resolved `Promise<void>` even if native `apply_change` rejected or the revision-bound view gate refused the response. `DeliveryProfiles.createProfile` and `deriveProfile` both awaited that promise and then selected a newly generated ID, potentially making a rejected profile edit look locally successful. The Deliver workspace now receives a **separate strict `commitRequired`**, which preserves native CAS and effect-grant semantics but propagates the rejection to `DeliveryProfiles.run`. That existing scoped handler displays the actual failure, prevents `setSelectedId` from running, and retains the previous persisted project and profile. Successful commits still publish through the same source-revision gate and then select the saved ID. No other editor's callback contract is broadened, avoiding unhandled promise rejections from existing fire-and-forget controls. Synthetic desktop-browser negative tests exercise rejected first attempts and successful second attempts for both `New profile` and `Derive selected`; this is not an installed-owner acceptance pass.

### MWAUD-008: failed scene creation discarded an uncommitted draft

The Project rail's `add_scene` form formerly awaited the nonthrowing `App.commit` callback, then always closed the form and reset the scene name, even if the native source-CAS commit was denied. That represented rejected authoring as an apparently completed UI operation and discarded the draft. The rail now uses the narrowly scoped `commitRequired` callback already used by Delivery, catches actual rejections locally, leaves the name/form intact, prevents a second submission while pending and closes/resets only after an acknowledged revision update. Reorder and other fire-and-forget rail controls retain their original global error handling. A synthetic desktop IPC browser regression verifies the first rejected commit leaves the draft/revision untouched, followed by a successful same-source second request that adds exactly one scene. No native/installed acceptance is inferred.

### Continuing audit boundary: other optimistic editor updates

Source inspection also identified other components that await the nonthrowing generic `commit` and then adjust local selection, dirty state or revision bases. These include `CanvasWorkspace`, `HistoryWorkspaces`, `IntegrationsWorkspace` and multiple creative workbenches such as `CreativeEvaluatorWorkbench` and `MeasuredDataWorkbench`. They are **unverified risk candidates**, not individually reproduced bugs: some rely on revision refresh or independent backend guards and must be inspected on their actual call paths before a broad contract change. Do not simply make every generic `commit` reject, because numerous existing `void commit(...)` event handlers would then risk unhandled promise rejections. Expand strict committing only behind components that provide scoped error boundaries and regression tests.

## Remaining work by impact and boundary

| Priority | Work that remains open | Boundary |
| --- | --- | --- |
| P1 | Reconcile each private original criterion with exact implementation, named tests and SHA-bound executed evidence | Original catalogs are required; do not promote all 187 changed rows automatically |
| P1 | Installed editor journey: create/import, edit, real voice, native preview, master export, reopen and recover | Synthetic browser tests and CI renders alone do not certify the installed journey |
| P1 | Motion Canvas provider-generation loss / unknown original outcomes | Existing issue 49; retain original job identity and prohibit blind retries |
| P1 | Owner-authorized MLT project cleanup and lifecycle receipts | Existing issue 91; read-only inventory is not cleanup execution |
| P1 | Transitive Linux GTK/glib advisory closure | Existing issue 89; absence of a direct affected call is not remediation |
| P1 | Independent acceptance, physical-device installation/signing and release authorization | Existing issue 126; zero private cases are promoted here |
| P2 | Per-requirement source-to-test-to-result mapping and stale status correction | Presence of a broad source file or passing unrelated workflow is insufficient |
| P2 | Source-native capture/media, narration revisions, broader typography/cameras and round trips | Preserve bounded semantics and explicit fidelity loss; coordinate with active feature branches |
| P2 | Measured native incremental-render equivalence and fair performance comparisons | Derived-index reuse does not establish renderer speedup |
| P2 | External clients, Platform/Launchwright handoff and three complete creative families / ten real revisions | Contract fixtures and synthetic edits are narrower evidence |

The baseline expansion ledger explicitly claims no implementation for MW05-E04-01, E04-03, E07-04, E08-04, E09-04, E11-03, E12-01 through E12-04, and E13-02. These are HyperFrames/fframes, new cinematic Blender work, full sound production, comparative benchmarking, real-media NLE interoperability, external/paid generation and identity/reconciliation, and dynamic extension admission. Open feature PRs may contain work not merged at this source cut; they are neither absent work nor accepted main functionality.

## Validation and handoff

Run `tooling/acceptance_policy.py`, `tooling/creative_status_policy.py` and their regression tests. Existing private intake and release-readiness tests must continue passing. CI runs Studio unit/type checks and browser regression tests on the candidate; do not transfer successful baseline CI verdicts to the audit branch.

The new lightweight **Source Requirement Traceability** workflow checks out **full Git history** (not a shallow clone), validates both public ledgers, runs adversarial reconciliation tests and retains one immutable, SHA-bound JSON artifact for all 272 IDs and 60 original acceptance cases. The scanner now rejects duplicated/nonfinite fields, symlink evidence entries, fake source paths and shallow-history drift. Its green workflow indicates **structural source reconciliation only**; it does not demonstrate feature completeness, user acceptance, native rendering or release readiness. CI evidence remains distinct and attached to its own exact SHA.

Only this audit's worktree is modified. There is no merge of the audit PR into main, release, tag, provider installation, expensive workstation build, secret change or original private media upload. Original acceptance remains NOT_RUN and public release remains unauthorized.
