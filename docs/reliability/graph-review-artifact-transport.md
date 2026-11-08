# Raw Graph review artifact transport

`gh run download` extracts artifact members into a directory. That directory is
not the original ZIP required by the offline reviewer's `--artifact-zip` input.
Use the bounded transport command to retain the raw GitHub artifact response and
its API provenance without extracting or decrypting it.

On a trusted review host, provide the exact product commit and currently running
manual workflow ID. The output must be a new absolute directory beneath an
existing canonical parent. Use a private filesystem location. On POSIX the tool
creates the directory with mode 0700. The final ZIP and result use mode 0600; API streams inherit the host umask inside that private directory.

```sh
SOURCE=REPLACE_WITH_EXACT_40_CHARACTER_COMMIT
RUN=REPLACE_WITH_EXACT_RUN_ID
python3 -B tooling/graph_review_artifact.py \
  --source-sha "$SOURCE" --run-id "$RUN" \
  --output "/secure/graph-review/$RUN"
```

For native Windows PowerShell, set `$SOURCE` and `$RUN` to the same exact
identifiers, then use an existing private parent directory:

```powershell
python -B tooling/graph_review_artifact.py --source-sha "$SOURCE" --run-id "$RUN" --output "C:\Private\graph-review\$RUN"
```

The command resolves the installed `gh` executable on PATH on Windows, WSL or
Linux and invokes read-only `gh api` without a shell. Its host must have
authenticated read access to `seradotcom/motionwright`, Python,
and the existing tooling dependencies. It never requests a private key. Do not
put a review private key on the producing runner.

The run must still be `in_progress`, use `workflow_dispatch`, attempt 1, the exact
source commit, and `.github/workflows/studio-service-verification.yml`. Both run
repositories must identify `seradotcom/motionwright`. The selected artifact must
have the unique exact name
`graph-originals-review-$SOURCE-$RUN-1`, belong to the same run/source/repository
IDs, remain unexpired, and carry a SHA256 digest. Run and artifact metadata are
observed again before a successful result is written. A completed or retried run
is rejected; the command does not wait, retry, dispatch, or post a status.

`artifact.original.zip` contains the exact downloaded bytes, checked against the
API length and digest. The tool verifies exactly two regular ZIP members,
`producer-originals.mwseal` and `carrier.original.json`, plus their CRCs, without
extracting or decrypting them. Original API responses, command intents, stderr,
exit receipts, and `transport-result.original.json` remain in the private output
directory. A failure may leave diagnostic originals but produces no successful
result; choose a fresh directory for another separately authorized observation.

Pass the retained ZIP to the separately supplied offline reviewer on its
review host, using independently trusted product and SDK clones and
platform-appropriate protected key custody:

```sh
python3 -B tooling/graph_r74_external_reviewer.py \
  --artifact-zip "/secure/graph-review/$RUN/artifact.original.zip" \
  --private-key /secure/reviewer/private.pem \
  --trusted-product /secure/trusted/motionwright \
  --trusted-semwright /secure/trusted/semwright \
  --source-sha "$SOURCE" --semwright-sha "$SEMWRIGHT_SOURCE" \
  --run-id "$RUN" --output "/secure/graph-review/$RUN/offline-review.json"
```

`SEMWRIGHT_SOURCE` must be the independently established pinned SDK commit. The
transport result establishes GitHub delivery provenance only. Independent source
and encrypted-content review, any external admission decision, and physical case
execution remain separate. Neither command grants owner admission by itself.

The scope is deliberately bounded: a complete first page of at most 100 run
artifacts, at most 256 MiB of ZIP bytes and declared expanded member bytes, and
the API adapter's 120-second limit per request. Larger or incomplete
listings and oversized archives are rejected instead of changing the selection.
The adapter checks response size after recording its stdout; it does
not impose a streaming disk quota. Filesystem access controls and available disk
space remain responsibilities of the trusted host.
