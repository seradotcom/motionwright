# Workflow intelligence

Motionwright can inspect workflow-learning evidence from an owner-provisioned canonical Semwright session without becoming a second workflow authority.

## Trust boundary

The desktop shell reads the connection descriptor from `MOTIONWRIGHT_SEMWRIGHT_CONNECTION`. The descriptor must validate through the same `ProductionConnection` checks used by native production and must be bound to the exact Motionwright project resource.

Every workflow query is sent through the Semwright CLI/Broker boundary. Motionwright does not parse Semwright state files directly, bypass Broker/Policy, or infer live workflow state from local project history.

The read surface accepts provenance only when the response is owned by the built-in `semwright-core` authority with a valid descriptor digest.

## Read-only commands

The current workspace exposes:

- `workflow.traces.list`
- `workflow.patterns.list`
- `workflow.suggestions.list`
- `workflow.proposals.list`
- `workflow.candidates.list`
- `workflow.promotions.list`

No workflow mutation is allowlisted from this UI. Recording, compilation, proposal acceptance, verification, replay, promotion, demotion and dismissal remain outside Motionwright's observational surface.

## UI states

The workspace distinguishes three states:

- `available`: evidence came from the configured canonical Semwright connection;
- `unconfigured`: the desktop app has no owner-provisioned connection descriptor;
- `browser_demo`: the web-only development surface is active and intentionally returns empty evidence.

Browser demo mode never fabricates traces, candidates, promoted capabilities, or runtime provenance.

## Why this belongs in Motionwright

Repeated creative operations can become useful workflow evidence, but the creative project and the workflow authority have different ownership. Motionwright owns project intent, revisions, locks, reviews and renderer receipts. Semwright owns workflow recording, learning, replay, capability admission and runtime provenance.

Keeping the boundary read-only lets the editor surface useful automation opportunities without silently turning observations into executable behavior.
