# Contributing

Motionwright is built as an external Semwright Native SDK consumer.

## Rules that matter

1. Keep creative-domain ownership in Motionwright. Do not recreate Broker, Policy, Driver Host, Project Graph, Effect Conformance or the global job runtime here.
2. Every target-bound mutation must use application-owned transactional CAS.
3. Do not infer permission from operation discovery.
4. Generated media or code is a realization of intent, not the only project source of truth.
5. UI, external client and Native SDK paths must converge on the same service/storage model.
6. Do not weaken assertions, hide skipped gates or replace failed evidence with prose.
7. Do not add paid-provider calls to the default path.

## Local versus CI

Local checks should be small and bounded. Heavy Rust, Tauri, browser, renderer, fuzz, coverage and native integration work belongs in GitHub Actions.

## Commit style

Use focused conventional commits, for example:

- `feat(domain): add branch merge conflict model`
- `feat(studio): add timeline trim transaction`
- `test(native): cover stale revision rejection`
- `docs(renderers): declare Manim capability losses`
