# Motionwright v0.5 — Progressive access by external local clients (E10-03)

**Status: partially implemented, owner-local development profile. Not a remote
MCP/Driver Host grant, not a complete release.**

The existing Motionwright application already exposes 53 registered
Semwright Native SDK capabilities through the canonical Driver Host. The
original application records the project and edit history in the shared
`StudioService` and SQLite store. This addition deliberately **does not**
invent a second backend, a persistent HTTP service, a second scheduler, a
duplicate hundred-command MCP catalog or a privileged remote context.

## Concrete client interface

`crates/native/examples/local_agent_actor.rs` is an ephemeral one-shot
developer CLI that allows independently launched local processes to use the
same canonical project store. It consumes a bounded JSON request in an
owner-selected filesystem location, an **absolute, regular, non-symlink
existing SQLite database**, and optional explicit `--owner-edit`. Supported
request actions:

- `discover`: read a 1–32 item page of the **actual** Semwright Native SDK
  Driver capability catalog. Schemas are compiled by the Native SDK; this
  adapter does not duplicate or redefine the 53 original descriptors.
- `observe`: use the **actual Native SDK** `Query`, `PageCursor`,
  `ObservationPage`, `CallContext::application_local` and
  `MotionwrightObserver` contract to read one bounded project scope at
  the exact resource generation and revision.
- `preview`: validate one existing domain change against a cloned project,
  without writing or pretending future-generated UUIDs are already committed.
- `commit`: only with an explicit `--owner-edit` local invocation, parse an
  allowlisted change, require exact project generation/revision and
  owner-selected request ID, then call the **same** `StudioService::apply`
  transactional CAS method used by the real editor.
- `init`: requires `--owner-edit` to initialize the local owner-selected
  SQLite project.

The bounded allowlist accepts `project.rename`, `scene.add`,
`narrative.premise.set` and `brief.set`. These are already registered
Semwright Native SDK operations, not newly invented capabilities. It rejects
unknown fields, forged operations, oversized pages, stale revisions,
cross-project generations, reused non-progressing cursors and unapproved
local writes. Running an OS process with `--owner-edit` is an owner-local
**manual developer action**, not an authenticated remote-agent permission.
Clients cannot create remote Host authority by including a flag or token in
project source; Native SDK writes still require canonical Driver Host
execution context.

## Independent-client acceptance (disposable CI)

`tooling/agent-local/two_clients_e2e.py` launches **distinct operating-system
processes for every call**, not multiple mock classes or two roles sharing
an in-memory `Project` or chat history.

The test creates one original SQLite project. Client A discovers all SDK
capabilities through 9-item pages and reads the initial project summary.
Client B independently reads that same revision. Client A validates and
commits a scoped rename. Client B's stale write must be refused, then it
reads the current version, previews and commits a new scene. Client A
observes the new scene and paginates the exact original project journal.
Client A commits a narrative change; Client B's old journal cursor is
invalidated and must be reissued. Cross-generation, unknown operation,
unapproved write and unbounded catalog requests are rejected.
All three accepted revisions are persisted by `StudioService`.

**What this proves:** two compatible **local** clients can resume the same
source and read/create valid revisions without sharing conversations.

**Not proved:** remote transport availability, authenticated network/MCP
agents, multi-tenant teams/worker permissions, Host-mediated external
source mutation, cross-machine synchronization, human design acceptance or
publish rights. Those require the real Semwright Host/Platform
authorization and acceptance separately. The CLI example must never be
advertised as a live HTTP server, independent trust boundary, or a
production remote agent interface.

CI workflow: `.github/workflows/v05-agent-two-clients.yml`, pinning
the current Semwright Native SDK dependency and using disposable GitHub
Actions runners. Source-only code in an app CLI does not by itself fulfill
E10-03 release acceptance.
