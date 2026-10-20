# Windows package verification scopes

`package-candidate.yml` provides three explicit dispatch scopes. The run name includes the scope and event commit. Automatic pull-request and push runs retain the full package selection.

| Scope | Quick policies and receipt contracts | Windows unit contracts | Windows build, install and first-use | Linux packages | macOS package inspection | Trusted all-platform provenance |
| --- | --- | --- | --- | --- | --- | --- |
| `all` (default) | Required | Required | Required | Required | Required | Eligible only in the existing trusted event contexts after all three package jobs succeed |
| `windows_contracts` | Required | Required | Not selected | Not selected | Not selected | Not selected |
| `windows_installed` | Required | Required | Required | Not selected | Not selected | Not selected |

For the reviewed PR #325 foreground-input probe, the windows-contracts job
checks **18 unique static Python unittest controls** in both discovery and
dotted-module modes before packaging. All 18 must pass without skips or
expected failures. This includes the native accessible-field selector negative;
the actual test-definition, Git source tree, CI runner, stdout/stderr bytes,
and run/job/attempt identities are checked. These synthetic G1 controls do not
claim a successful installed UI execution or human acceptance.

The quick policy job runs the source, acceptance and creative-status validators plus the existing candidate receipt and smoke contract suites for every scope. The acceptance and creative-status validators call the shared conformance checks. A failing prerequisite prevents the dependent package job from running. Windows packages still require both the receipt-policy and Windows-contract jobs. The existing Linux, macOS and Windows package steps, source receipts and failure handling are unchanged.

## Selecting the work

Use `windows_contracts` for an isolated change to a Windows contract test or the PowerShell exit-code guard when the reviewed impact does not reach the installed first-use flow. Use `windows_installed` when a Windows-only UI Automation or first-project probe change requires testing the real installed process. The dependency is concrete: `windows_cold_start.py` invokes the sibling `windows_first_project_ui.ps1`, and the Windows package job consumes that probe. Both files remain workflow trigger inputs.

A reviewed change to product code, shared package inputs, source locks, platform-independent receipt semantics, or the candidate composition may require `all` and additional affected workflows. The manual scope is an execution choice; it does not prove that a broader change is Windows-only. Review the actual diff and dependencies before selecting it. The default `all` remains the complete package gate for a final candidate.

For a reviewed branch whose exact source has been recorded:

```sh
gh workflow run package-candidate.yml \
  --repo seradotcom/motionwright \
  --ref REVIEWED_BRANCH \
  -f verification_scope=windows_installed
```

Record the branch object before dispatch and retain the returned run identity. Confirm the original job's actual checkout, run attempt and package receipts afterwards. A pull-request run may execute its merge commit instead of the reported PR head. A source change during dispatch requires reconciliation; it never authorizes relabelling old results.

## Reading the result

A successful `windows_installed` run proves only the operations and assertions actually reached in the Windows job. Linux and macOS are not selected; they have no new execution result. The provenance job remains excluded from both partial scopes and still depends on all three package jobs in a full trusted run.

Keep the original logs, package identities, installed-process observations, failure phase and any closed-store or first-project receipt. Distinguish source checks, synthetic contracts, installed process behavior and human acceptance. Compilation alone does not prove installation; first use alone does not prove upgrade, uninstall, interruption or recovery. Those remain separately required where the original criteria and release risk require them.

If a native action has an unknown outcome, preserve that receipt and reconcile its effect before deciding on any new action. Changing the dispatch scope does not authorize replaying that action. Preserve every known failure and omitted test. A successful scoped workflow never promotes a skipped job, another SHA, an older candidate or a formal acceptance criterion.

## Full candidate

The release integrator selects the actual integrated commit and the required G3 matrix. Use `all` for this workflow and retain the renderer, security, installer recovery, platform and conformance evidence required by the complete verification plan. A5 owns the formal criterion verdicts; the scope menu and workflow result do not grant release or human acceptance.
