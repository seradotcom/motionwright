# Revision-bound project pages

The storage and service layers expose three read-only operations:

- `project_projection_head(project_id)` returns the row-authoritative identity,
  title, document schema, and scene count.
- `project_scene_page(identity, cursor, limit)` returns ordered scene summaries.
- `project_scene_nodes(identity, scene, cursor, limit)` returns complete canvas
  nodes from one addressed scene.

The head and page operations do not deserialize a full `Project` or return
`document_json` to Rust. They query the persisted document using SQLite JSON1.
JSON1 can still scan or materialize the document internally. These APIs bound
returned payloads, not SQLite's internal CPU or memory consumption.

## Snapshot and scope

Every request holds one deferred SQLite read transaction for its header and
selected fragments. A writer on another connection cannot cause one response
to combine two revisions. A transaction ends before the response is returned;
it is not held while the user navigates.

The identity includes project UUID, generation UUID, and a canonical decimal
revision string. Revision strings preserve integers across JavaScript IPC.
`checked_stamp` validates the representation and constructs the existing
backend revision stamp. The identity and cursors are read markers, not grants
or credentials.

Each subsequent page checks the identity against its own snapshot. A changed
generation or revision rejects the request. The consumer must obtain a new head
and discard stale pages. Node pages also bind a scene ordinal and UUID; finding
the UUID at a different ordinal does not authorize that address.

The existing full-project loader and mutation paths retain global project
validation. A successful page validates the row/document authority, the
containers it reads, and the fields it returns. It does not certify unseen
nodes, branches, assets, or cross-page graph invariants. References to parents
and relations outside the page remain intact.

UUIDs are compared as UUID values, retaining the existing parser's accepted
representations. Interpreted duplicate authority keys are rejected. A key containing NUL cannot
impersonate a field consulted through SQLite paths. Revisions must remain exact JSON
integers and agree with SQLite's signed integer column; booleans, floating
point tokens and numeric strings cannot impersonate a revision.

## Bounded responses

A request accepts 1–64 scene summaries or 1–128 nodes. An out-of-range limit,
ordinal, identity, or cursor fails explicitly. Pages preserve contiguous array
order. The last page has no next cursor. A valid start at the collection count
returns an empty terminal page.

Selected JSON fragments have an aggregate input budget of 1 MiB. SQL first uses a conservative 2 MiB transport cap to account for UTF-16 database
encoding. Rust then checks each borrowed UTF-8 fragment and their aggregate
against 1 MiB before deserializing them. A second bounded serializer checks the complete output,
including wrappers, defaults, JSON escapes, and cursors, against 1 MiB. Schema
errors use bounded messages and never echo arbitrary JSON field names.

An oversized page returns `ProjectionError::TooLarge`; no partial page or cursor
is returned. The consumer may ask for fewer items. A node that exceeds the
single-item budget remains a complete node in storage; the API does not
truncate it or redefine the project's validity.

These bounds are operational limits. They are not an acceptance latency target
for large projects.

## Consumers and validation

A consumer can open a project using the head and first scene page, request nodes
for the visible scene, and keep selection as a scene address plus node UUID.
It must not construct a partial `Project` and submit it to existing mutation
logic. Writes continue to use the canonical service operations and their
revision checks.

Desktop/Studio virtualization, preview scheduling and installed-app acceptance
must exercise these APIs directly. A service test alone does not establish
those properties.

The regression suite covers persisted large-project projections, full-loader
compatibility, stale cursors, two-connection snapshot consistency, malformed
authority and selected fragments, byte limits, and preservation of logical
SQLite state and immutable blob bytes. Process/power-loss durability is outside
this read-only API's claims.
