# Native production boundary

Motionwright owns creative project state, revisions, branches and local production receipts. It does not own Semwright execution authority.

## Owner-provisioned connection

The desktop runtime accepts an explicit motionwright-semwright-connection/1 file containing an absolute Semwright CLI path, its SHA-256 digest, broker socket, private session file, output root and the exact Motionwright resource bound to that workspace. Paths are revalidated before each call, symlinks and group/world-writable connection paths are rejected on Unix, and the CLI digest must still match.

The connection is not a generic command tunnel. Motionwright enables a closed production command set for Motion Canvas composition/render operations and MLT frame encoding, sync probing, AV muxing and render job operations. Broker output must be a typed Semwright envelope with Driver provenance, the expected provider identity, a descriptor SHA-256 and a provider generation.

## Receipts and retry safety

Production receipts are application-owned history, not execution authority. Each row is anchored to:

- Motionwright project ID, generation and revision;
- caller request ID;
- SHA-256 of the exact command and JSON arguments;
- production stage and bounded response/error metadata.

A request ID cannot be reused with different input. A completed request replays its stored result instead of dispatching again. A request whose mutation outcome is unknown fails closed and must be inspected before any retry. Late results remain attached to their original project revision and never silently become current after creative edits.

## Authority boundary

A valid local receipt means only that Motionwright recorded a Broker interaction. It does not imply render quality, Project Graph admission, Effect Conformance PASS, creative approval or freshness. Those claims require their canonical Semwright evidence paths.
