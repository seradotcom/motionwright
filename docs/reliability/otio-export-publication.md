# Safe publication of existing OTIO exports

An OTIO command must finish writing and synchronizing its authorized snapshot
before making the requested destination visible. A failed write must not leave
a truncated file under the final name.

The publication helper owns filesystem creation, promotion and cleanup.
It stages in the selected destination directory using the existing locked
tempfile package, writes and syncs the complete body, then uses
persist_noclobber. An occupied destination is rejected even if its bytes happen
to match. No overwrite or copy fallback is used.

Two fault tests write actual bytes to a real file, observe the destination
while the write is incomplete or unconfirmed, and inject a failure from the
write/finalize closure. They require that no final destination was visible or
retained, that foreign files remain byte-identical and that staging is cleaned.
A further control creates and synchronizes a competing destination after staging
and before publication. It requires a conflict, unchanged competing and foreign
bytes, and cleanup of the losing staging file. This control is added on FIX;
the eight earlier test bodies remain unchanged.

The existing command tests retain grant consumption, snapshot authority,
successful exact document bytes and occupied-destination behavior.

These are controlled I/O fault tests, not an observed operating-system ENOSPC
or power-loss test. The change does not establish filesystem confinement against
a malicious directory owner, universal crash durability, actor authentication
or installed application acceptance. No native renderer or new OTIO feature
is introduced.
