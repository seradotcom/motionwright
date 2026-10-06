# Workflow distillation

Motionwright exposes Semwright Workflow Distillation without creating a second workflow engine, store, permission model, recorder or promotion authority.

## Trust boundary

The desktop shell reads the connection descriptor from `MOTIONWRIGHT_SEMWRIGHT_CONNECTION`. The descriptor must validate through the same `ProductionConnection` checks used by native production and must be bound to the exact Motionwright project resource.

Every workflow read or action is sent through the Semwright CLI/Broker boundary. Motionwright does not parse Semwright state files directly, mint grants, bypass Policy, or infer live workflow state from local creative history.

Responses are accepted only when provenance identifies the built-in `semwright-core` authority with a valid descriptor digest.

## Evidence reads

The workspace reads:

- `workflow.traces.list`
- `workflow.patterns.list`
- `workflow.suggestions.list`
- `workflow.proposals.list`
- `workflow.candidates.list`
- `workflow.promotions.list`

Browser demo mode returns empty evidence and never fabricates traces, candidates, promoted capabilities or runtime provenance.

## Explicit V1 recording and compilation

Recording is user-initiated with `workflow.record.start` and user-stopped with `workflow.record.stop`. There is no background desktop recorder. Argument-value capture is off by default and must be enabled explicitly for a recording.

Selected explicit traces can be compiled with `workflow.compile`. Repeated suggestions can enter the same canonical compiler through `workflow.suggestion.compile`. Motionwright never writes directly to a workflow database.

## V2 advisory and V3 proposals

Patterns and suggestions are advisory. Merely observing repetition does not execute an operation, change permissions or promote a capability.

A proposal can be inspected with `workflow.proposal.plan` without persistence or execution. Exact proposal acceptance uses `workflow.proposal.accept`, and still does not authorize a replay or production mutation by itself.

## Verification, replay and promotion

Candidates use the canonical gates:

1. `workflow.verify` checks current capability schemas and descriptor digests.
2. `workflow.replay` re-authorizes every recipe step through Broker/Policy using current inputs and current authority.
3. `workflow.promote` is attempted only explicitly; Semwright rejects promotion without its required successful verification/replay evidence.

Motionwright treats replay as a mutation for uncertain-outcome handling because a replay can execute real application steps. A transport timeout is not interpreted as proof that the operation did or did not happen.

The UI does not expose trace deletion, arbitrary demotion, suggestion dismissal or other generic workflow commands. Adding any such operation requires an explicit product contract and allowlist change.

## Stochastic steps

A Recipe that invokes AI or another stochastic system does not become deterministic when compiled or promoted. Workflow automation can bind inputs, invoke operations and validate results while creative outputs may still vary. Human taste decisions are not silently turned into constants.

## UI consent

Operational workflow controls appear only with a live canonical connection. The user must enable workflow changes for the current workspace session before record/compile/accept/verify/replay/promote controls are enabled. This UI gate is not a security authority; Semwright still decides every operation.

Recording state stored in browser session storage is only a UI hint so the user can leave the workspace while demonstrating operations. Semwright remains the source of truth; a stale hint cannot authorize or complete an operation.

## Failure behavior

If the connection is missing, bound to another project, fails digest/path validation, or a canonical policy/gate rejects an action, Motionwright fails closed. The user can refresh evidence and inspect current policy, consent, replay or descriptor state. The app does not switch provider, fake acceptance or fall back to local workflow execution.
