# CAS verification through a retained regular file

Content-addressed verification uses the same protected source opener as file
import. Linux and macOS reject a substituted leaf symlink with NOFOLLOW and
open nonblocking so a substituted special file cannot make admission wait for
a writer. Windows opens the reparse point itself and rejects a reparse or
non-regular handle.

The verifier retains that admitted handle, captures its size, and reads at most
that size plus one byte. Excess bytes are rejected before hashing; premature
EOF is rejected. A matching digest therefore cannot disguise growth or
truncation during the observed read. Interrupted reads preserve the budget.

Both Store::verify_blob and the required-blob check in Store::apply use this
path. A rejected new mutation leaves the document, event journal and receipt
unchanged. Existing committed receipt replay keeps its historical behavior and
does not re-read a source or start another effect.

This boundary protects the opened leaf and bounds one verification. It does not
claim atomic snapshots of concurrent same-size writes or protect arbitrary
ancestor-directory replacement. Consumers still need their existing authority,
CAS retention and expected digest checks.

## Regression coverage

The storage unit tests in blob_hash_tests exercise the real verifier and apply
boundary. Owned synthetic files grow or shrink at the first read while their
resulting digest would otherwise match. Another case replaces the leaf with
an equal-byte symlink before open. Empty, small and multichunk stable sources
remain valid.

Run the selected suite with:

    cargo test --locked -p motionwright-storage --lib blob_hash_tests

Windows symlink tests require an environment already authorized to create
symlinks and are explicitly ignored in the ordinary run. Execute those cases
with --ignored on a suitable runner; an ignored test is not evidence of that
platform boundary. No test changes the host's symlink policy.
