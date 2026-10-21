# Snapshot authority for desktop project exports

The existing local bundle and OTIO commands consume a grant for one project,
generation, revision and destination. A later project read must not replace that
checked snapshot after the grant has been consumed.

## Existing boundary, completed

Both commands use the same preparation phase: open and validate the requested
project through the project-library reader, compare its generation and revision,
and consume the existing DeliverLocal grant for that exact scope and destination.
The prepared value is private, consumed by value, and cannot be serialized or
cloned into a retry token.

Bundle execution forwards the retained project stamp to the existing
StudioService.export_project_bundle_at operation. Storage compares that stamp
against the very Project captured inside its SQLite transaction, before reading
the retained journal and publishing the bundle. A peer revision committed before
this capture rejects the export without creating a destination. A later edit
does not replace an already captured backup.

OTIO execution serializes the Project retained by preparation. A peer edit may
therefore coexist with a successful export of the original checked snapshot;
it cannot substitute new project bytes. The existing OTIO document records the
exported project identity, generation and revision. Response fields, path rules,
loss reporting and no-overwrite behavior are preserved.

The generic desktop error presenter is moved unchanged into a shared module
so the desktop command and service integration target execute the same code.

## Causal regression and controls

The service integration target includes the actual desktop modules. Its fixture
uses two independent StudioService instances on one SQLite store, a real CAS
asset and actual filesystem exports. It interleaves a committed peer edit
between the same preparation and execution functions used by the commands.

Two cases detect substitution of a post-grant bundle or OTIO project. Four
controls cover the retained bundle project, journal presence and original blob
bytes, the exact OTIO document and response, preservation of occupied destinations,
and rejection of a changed destination subject. They also check consumed-token behavior and preservation
of the peer's committed revision. No production test hook or timing loop is used.

## Limits

The fixture grants exercise existing code and do not establish authentic
operator consent, principal isolation or an installed Broker. This change does
not mint or refresh grants, retry UNKNOWN work, change provider behavior or
publish any native output. Platform packaging, installed acceptance and
filesystem confinement remain separate criteria. The service integration target
is not an installed WebView test. Power-loss durability is not inferred.
