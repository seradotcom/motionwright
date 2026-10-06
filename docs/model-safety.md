# Model and proposal safety

Motionwright treats model output as untrusted proposal input. A model, planner or manual tool does not mutate creative state by returning text.

## What the project records

A model invocation receipt is intentionally small: provider/model identity, optional provider version, the exact Motionwright base revision, bounded resource references, declared data classes, an invocation budget and outcome. Prompt bodies, hidden reasoning, chain-of-thought, credentials and provider secrets are not fields in the project model.

Recording a receipt is itself a revision-checked Motionwright change. The receipt must target the exact current revision, use unique bounded data classes and resource references, and point only at resources that exist at admission time. Historical receipts remain provenance after later edits.

## Proposal admission

Proposal sets are closed typed data. Admission verifies the proposal set base revision and every target before the set becomes project state.

- scene-scoped edits cannot escape to another scene;
- selection-scoped edits must stay within explicit selected resource refs;
- scene-order proposals must be a complete duplicate-free permutation of project scenes;
- renderer choices are restricted to Motionwright renderer identifiers;
- beat rewrites must reference an existing narrative/scene beat and obey scope;
- unknown resource references fail closed.

Selecting a proposal records intent only. It does not execute its edits. Normal locks, CAS, review and transaction gates still apply to any eventual change.

## External providers

The desktop does not silently activate a paid or external model provider, and this repository does not define a hidden fallback provider. Any future provider transport must add an explicit outbound-consent boundary for text, frames, audio or source code before network dispatch and must keep credentials outside portable project state. Until that transport is implemented and verified, Motionwright makes no claim that the Studio itself has sent data to a model provider.


## Local preflight boundary

The Alternatives workspace can build a local motionwright-model-preflight/1 manifest before any provider transport. The manifest names exactly one provider/model, exact project generation/revision, explicit resource references, declared data classes, budget, estimated byte exposure and a SHA-256 fingerprint. Every disclosed row is labeled untrusted data.

Metadata and source classes are separate. Remote metadata inspection does not authorize text, frame/image bytes, audio bytes or source code. Source-class requests use immutable asset digests and verified local blob sizes where applicable. Unsupported class/resource pairs fail closed instead of broadening the context.

The preflight always reports fallback_provider as null and network_dispatched as false. The Studio currently does not implement a built-in remote-model transport, so preflight is not represented as a completed provider call. Manual editing and external agents using the public Native SDK remain functional without any model subscription. A future remote transport must consume this boundary and add a user-controlled consent token before reading/sending source bytes; it may not infer that consent from metadata access.
