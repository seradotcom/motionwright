# Lossless technical assessment-history archive

The optional first entry of a row's assessment_history list may be an archive
with schema motionwright.assessment-history-zlib-json/1. It contains canonical
UTF-8 JSON compressed with zlib and base64, together with the entry count, raw
byte length and SHA-256. Subsequent history entries remain ordinary metadata.

The history guard expands both the previous and candidate histories before
comparison. It rejects changed prefixes, forged/empty prior criteria, bad
hashes, unrecognized formats, invalid base64 or duplicate JSON keys, oversized
decompression, and compressed records in the wrong position.

The archive is fully reversible and limited to 1 MiB uncompressed per record.
No original history, current-source proof, human acceptance, media or source
rights can be removed or inferred by using this format. Use the new codec only
after independent verification and integration of the guard itself.
