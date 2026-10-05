# Semwright Native SDK integration

Motionwright pins the Semwright revision recorded in `SOURCE_LOCK.json`.

The integration follows the Native SDK public contracts:

- `Application::new("motionwright", ...)` describes the cooperation surface.
- `ObservationProvider` enumerates bounded project projections.
- `OperationHandler` executes only registered bounded operations.
- the application-owned SQLite transaction checks `CallContext::expected()` at commit time;
- opaque revisions are strings, never lossy JavaScript counters;
- uncertain completion remains uncertain;
- Native SDK Graph and Effects features adapt into canonical authorities rather than creating local verdict engines.

Initial registered operations are intentionally small and composable rather than one unrestricted “apply arbitrary patch” command. The UI may expose richer gestures, but they are decomposed into typed service commands with exact affected resources.

Operation discovery never authorizes an invocation. Broker/Policy/Driver Host remain responsible for capability/consent/session/runtime authority.
