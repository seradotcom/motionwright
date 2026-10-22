# Pinned Semwright source records

These files preserve the upstream workspace metadata and license texts for the Semwright revision recorded in SOURCE_LOCK.json. They are audit inputs and are not a vendored SDK or a separate build workspace.

The evidence.json record binds each file to its upstream path, exact byte count, SHA-256 digest, and Git blob identity. Source admission checks the record and the retained files without executing a command from the source lock. Git attributes preserve these bytes across supported checkouts.

The source verification status establishes consistency with the reviewed upstream records. It does not attest that the complete SDK built or executed, settle the licenses of transitive dependencies, or authorize a particular distribution operation. Those decisions require their own evidence.

To change the dependency revision, review the corresponding upstream commit and source records, retain the exact metadata and license bytes, and update the lock, record, and byte-preservation attributes together. Keep previous audit records available through repository history.
