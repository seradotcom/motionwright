# FixedTxt offline physical readback

This package versions the existing bounded reader and typed probe for original
Motionwright source 76f05572ec7a41035fc42054e27d01c869cd6176 and Semwright SDK
797e70884d35a068c72149ffae3e6df365bb0b64. The historical readers stay untouched;
provenance.json records their hashes and the new version.

The typed probe changes exactly four identity literals: the RED/GREEN observer
hashes and the fixture helper's hash and byte length. The helper's only semantic
delta lowercases emitted IDs. The probe still joins IDs through the authenticated
original alias map. Its geometry, Known/Unknown, text, font, mask, frame, and
numerical predicates remain unchanged. R37 and R38 remain the frozen numerical
authorities.

The result reader authenticates the ciphertext, original P/R/Q, public record,
source closure, and full selected snapshot before reading the original request,
clock, and optional session bundle. Each control original must have its own
independent P digest. A partial session bundle or an fx namespace without its
session originals is rejected.

Authenticated individual captions retain a 750 KiB PNG limit. Session captions
use the producer's existing 746 KiB limit; other fixtures retain 768 KiB.
The same resolved limit is required in the original request and every applicable
P row, then passed to PNG reads and the decoder. Neither images nor producer
limits are changed. All 180 mandatory PNGs are CRC checked; exactly the frozen
three, four, or seven samples are decoded and compared with original RGBA.

The reader validates the retained runner clock's schema, context, boot identifier,
request digest, finite values, and exact original duration. It does not query the
offline reader host's boot identifier or use its monotonic clock to reconstruct
the runner's state. Session clocks are additionally joined through the existing
source-bound validate_clock_bundle function. This does not issue or extend any
live permission.

Launch through an exact-source/digest-bound wrapper that preserves the existing
owner-private ACL checks, 1536 MiB process memory limit, 512 MiB reserve, disk
floor, one decoder, and decode deadlines. The reader has no Native producer,
workflow dispatch, R28, or formal-acceptance effect. Its output keeps actual
numeric verdicts, including failures.

The versioned reader contains no machine-specific private root. Direct command-line
use preserves the approved helper's original path rules. A separately authenticated
private wrapper may call main(configure_private_paths=...) after validating its
source digest, the operator-authorized roots, and the existing owner-private ACL.
That callback carries the local routing policy; it does not issue a Native grant.

Output receipts belong only to a directory successfully created by the current
invocation. A pre-existing directory, a lost creation race, or a replaced output
directory cannot receive this invocation's terminal receipt.

test_fixedtxt_readback.py exercises offline contract fixtures only.
request-contract-fixture.json is a public-shaped request used for those pure
checks; it is not a dispatch grant or evidence of a Native execution. The tests
cover distinct PNG limits, forged/missing originals, namespace and clock
binding, mandatory frame cardinality, and over-limit rejection before decoding.
