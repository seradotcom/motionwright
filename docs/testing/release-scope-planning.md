# Release scope planning

The release scope planner describes input variants, matrix instances, jobs and guarded steps that need evidence for an exact candidate. It reads local Git objects and retained GitHub response bytes. It does not dispatch workflows, repeat installers, cancel jobs, admit artifacts or authorize a release.

The catalog separates supported observation bindings from reviewed scope descriptions. Package bindings check the consistency of original API metadata. Other source definitions have reviewed plans while their scope-specific original-evidence readback remains pending.

## Reviewed scope catalog

Schema 2 includes the sixty workflow definitions in the reviewed snapshot. Each definition is fixed by its SHA-256. The planner compares those definitions with the actual candidate inventory; a new, missing or changed definition becomes NEEDS_REVIEW. Matching a count of workflow names does not prove coverage.

Other than the existing package observation contracts, each scope records typed inputs, dispositions for finite input options, job guards and dependencies, matrix instances, source-step ordinals and source-block hashes. Executable source commands and artifact-retention steps remain visible.

| Workflow family | Reviewed selection | Coverage boundary |
| --- | --- | --- |
| Candidate packages | all and nsis_lifecycle | all omits the update job; lifecycle requires compilation, inspection and installation |
| Blender | static-glb and original-scene | Shared source controls accompany both; original Scene has separate save/edit/reopen custody |
| Manim | static-scene, camera120 and camera120-source-cycle | The single invocation and source-cycle publication oracles differ |
| MLT sequence | Four normal modes, repaired-g2 and film180-g2 | Source-only is a conditional preflight alternative, never a physical substitute |
| MLT diagnostic | Source compiler opt-in and official runtime intake | Prerequisite artifacts retain their own source identities |
| Native render | Repeat enabled | Includes fresh owner, Broker and Driver Host; retained reliability limitations still apply |
| Project portability | full on three OSes and otio_export on Windows/macOS | Full includes cache contracts; selected OTIO export has a separate nine-case branch |
| Runtime first use | all on three OSes | Includes preflight and platform-specific reference preservation |
| Store.apply recovery | Both platforms on the product SHA fixed by the definition | Product and workflow checkouts differ and require candidate-source reconciliation |
| Studio service | all within the integrated helper roster | Source-built service/SQLite; no installed Tauri, native-render or human result is implied |
| Verification plan | Candidate enabled, empty comparison base | Planning and conformance guards only |

The other workflows retain distinct evidence classes for source, real service/SQLite, browser, synthetic media, physical storage, native media, measured performance, packages and installed behavior. A browser or synthetic observation cannot satisfy physical-media requirements merely because its workflow succeeded.

### Package lifecycle

Candidate Packages selects windows-update only for an explicit nsis_compile or nsis_lifecycle dispatch. Its install/upgrade/uninstall step additionally requires nsis_lifecycle.

Lifecycle requires compiler and payload-inspection success in the same original attempt. A second compile-only dispatch is not mandatory when those originals are complete. Compile-only may diagnose a different oracle, but cannot cover lifecycle.

The current distinct-version fixture does not establish interruption recovery or reopening an edited upgraded project. Those require separate reviewed scopes and originals. The word all is a workflow option, not a complete release scope.

### Conditional source coverage

Manim source contracts execute before each physical scenario and retain their reports. A fourth source-only dispatch is unnecessary when the relevant exact-source originals already exist.

MLT needs a qualified decision. Its final-MP4 branches execute the five source groups, but normal does not retain all source-report directories and repaired G2 does not include the Film directory. Film180 G2 retains both directories and before/after source records. Only complete original readback, or demonstrated equivalent custody, can discharge the source-only alternative. A green job or artifact name is insufficient. Source-only and G2 also use different Git cleanliness checks; the catalog does not claim unconditional equivalence of every command.

The reviewed MLT scope retains decoded AAC tail, timestamp and priming limitations. Its physical audiovisual-master and human-acceptance exclusions remain exclusions. This planner does not weaken tolerances or claim that a historical physical audio defect is fixed.

### Outstanding dispositions

The historical glib laboratory requires a registry dependency graph that differs from the current local-backport graph. Applicable FFI oracles need an explicit succession decision. FFrames measures an unadmitted upstream experiment; original requirement mapping must determine its release role. Neither disposition is a PASS or waiver.

Store.apply fixes a product source independently from its gate source. Its plan reports PINNED_PRODUCT_ORIGINALS and NEEDS_SOURCE_RECONCILIATION. Scoped source equivalence and original evidence may justify reuse after review, but cannot turn that diagnostic into an execution of another commit.

Security dependency review is a PR-only differential action. Preserve original PR base/head and integration relationships separately. Candidate advisory checks remain a distinct obligation.

## Run the planner

Use complete lowercase commit SHAs:

```sh
python -B tooling/release_scope_plan.py \
  --repo . \
  --repository OWNER/REPOSITORY \
  --source-sha FULL_CANDIDATE_SHA \
  --planner-sha FULL_PLANNER_AND_CATALOG_SHA
```

The candidate SHA selects workflow definitions and tree. The optional planner SHA selects helper/catalog and defaults to candidate SHA. The executed helper must equal its selected Git blob; only the exact LF-to-CRLF checkout transformation is allowed on Windows. The report records both identities and hashes.

Git replacement objects are disabled. Dirty working-tree files cannot enter the candidate snapshot. The helper invokes no Cargo, renderer, installer, GitHub or workflow. It prints to stdout and has no output-file mutation option.

Ownership reservations and resource-budget leases remain operational context. The authority header is descriptive: ownership_or_budget_lease=NOT_VALIDATED, authorizes_dispatch=false, execution_admission=NOT_PERFORMED. The planner reads no coordination ledger and does not use a lease as technical evidence.

| Exit code | Meaning |
| --- | --- |
| 0 | Inventory/scope reviewed, dispositions resolved, and all required metadata bindings implemented and consistent; observations remain unadmitted |
| 3 | Definition review, scope disposition or execution observation is incomplete |
| 2 | Input/source identity or structure refused |

The shipped mixed catalog remains incomplete for execution observations because most scope-specific readers are not implemented. A successful process exit would still not be a technical-criteria verdict.

## Supported original execution observations

The optional --observations PATH reads a JSON array. Implemented package bundles have exactly these fields:

- schema: motionwright.release-scope-observation/1
- workflow and workflow_sha256: reviewed definition
- case_id and inputs: exact catalog case
- source: repository, sha and tree
- run_json and run_sha256: original run response text and UTF-8 digest
- jobs_json and jobs_sha256: original complete jobs response text and digest

Preserve a specific attempt's original API responses before constructing a bundle. Read-only endpoints are actions/runs/RUN_ID/attempts/ATTEMPT and actions/runs/RUN_ID/attempts/ATTEMPT/jobs. The jobs response must be complete: total_count equals retained jobs. Do not concatenate pages, silently discard jobs or combine attempts. Large paginated runs require a reviewed custody extension.

The package workflow's original display_title binds case and candidate SHA. The reader checks repository, workflow, event, source, run, attempt, job identities and required steps. Main-branch push metadata is supported only where explicitly cataloged.

A required job or step that is absent, skipped, cancelled, failed or running cannot satisfy a case, even when its container is green. Compiler/inspection steps remain mandatory for lifecycle. Hashes, other commits/attempts, pagination and duplicates are checked. The planner does not choose a favorable attempt from several supplied records.

These checks establish supplied metadata consistency. They do not authenticate GitHub responses or inspect scope-specific artifacts. Valid metadata remains UNADMITTED_EXECUTION_OBSERVATIONS with scope_evidence_readback=NOT_PERFORMED.

For the other reviewed descriptions, supplying API success does not implement a reader. Those rows remain READBACK_REQUIRED, record the count of supplied candidate bundles and cannot complete observation coverage. Unsupported bundles are neither authenticated nor promoted into executions.

## Three completion properties

| Field | Meaning |
| --- | --- |
| definition_review_complete | Every observed workflow matches a reviewed source definition |
| scope_plan_complete | Definition review complete, with no unresolved applicability or fixed-product dispositions |
| scope_observations_complete | Every required case has a supported and consistent observation binding |

Missing execution evidence does not erase source review. Source review cannot become an execution result. Historical/experimental dispositions can leave all definitions reviewed while scope planning remains incomplete.

None of these properties means G3 is ready. Every report retains candidate_readiness=NOT_ASSESSED, technical_criteria_verdict=NOT_GRANTED, release_verdict=NOT_GRANTED, human_acceptance=NOT_RUN, evidence_admission=NOT_PERFORMED, automatic_dispatch=false and retry_authorized=false.

Workflow definitions are not the original requirement/acceptance inventories. Absent capabilities, helper dependency changes, exact tool/input identities, installed behavior, physical fidelity and human evaluation still need applicable evidence. Read-only planning credits zero saved executions/minutes.

## Extending a scope

Review exact workflow bytes, helper-controlled behavior and original criteria. Record every finite input option, job, applicable matrix member and required guarded step. Explain subset relationships instead of scheduling duplicate restricted variants.

The structural reader supports reviewed literal dispatch input forms, both existing step indentations, multiline guards, named/unnamed executable steps, artifact retention and reviewed verification actions. It does not evaluate arbitrary YAML or GitHub expressions. Job/preamble hashes bind matrix declarations, inputs and surrounding source.

Source ordinals are coordinates within a workflow job, not API step numbers. Generated setup/post steps, unnamed steps and default matrix display names require original-run mapping. Proposed names are not evidence of execution.

Updating only a workflow hash cannot bypass changed inputs, preamble, job body, guard, executable-step inventory or step bytes. Validation rejects duplicate paths/cases/steps/instances, missing input dispositions, incorrect boolean types, omitted matrix instances, uncovered jobs and undeclared executable steps. There is no generic skip waiver, automatic acceptance switch or dispatch instruction.

New evidence readers must bind exact source, workflow, case, inputs, run and attempt. Component prerequisites and fixed-product diagnostics keep their own identities. Their admissible use does not transfer a PASS onto another candidate SHA.

## Local controls

```sh
python -B -m unittest tooling.tests.test_release_scope_plan
python -B -m unittest discover -s tooling/tests -p test_release_scope_plan.py
```

Controls use synthetic API data and temporary Git repositories. They exercise skipped/missing observations, stale identities, pagination/digests, unknown definitions, replacement isolation, typed inputs, matrix omissions, changed dispatch/job source, moved ordinals, dispositions and unsupported-reader boundaries. They run no installer or physical production.

Set temporary directories inside the authorized project workspace before invoking them. Product and GitHub evidence must be collected and admitted separately.
