# Graph R74 — independent pre-effect reviewer (A5)

This is a **separate reviewer**, not the Graph producer, R74 owner, or release
approver. The A5 tool at tooling/graph_r74_external_reviewer.py examines one
encrypted, source-bound graph-flow producer carrier. It cannot run Native,
emit GitHub statuses, or grant Graph6/G2/G3.

## Non-negotiable operating separation

Use a separately controlled Linux reviewer machine/user with its own read-only
GitHub access. **Do not put the decryption private key on A1's Windows laptop,
inside Motionwright/Semwright, in GitHub Actions, logs, inboxes, or messages.**
The tool refuses runner and producer environments, unprotected private keys,
and key paths inside either Git checkout. These checks do not prove separate
OS account custody by themselves: the reviewer must establish that.

Before dispatch, the external reviewer must be online, able to download
artifacts, and able to finish substantive review inside the runner's single
original 1,200-second status window. An hourly automation is **not** a live
reviewer. If no live independent reviewer exists, **do not dispatch**.

## Provision independent RSA recipient — only on reviewer host

~~~sh
umask 077
mkdir -p /secure/a5-r74
chmod 700 /secure/a5-r74
openssl genpkey -algorithm RSA \
  -pkeyopt rsa_keygen_bits:3072 \
  -pkeyopt rsa_keygen_pubexp:65537 \
  -out /secure/a5-r74/recipient.pem
chmod 600 /secure/a5-r74/recipient.pem
openssl pkey -in /secure/a5-r74/recipient.pem \
  -pubout -outform DER > /secure/a5-r74/recipient.spki.der
base64 -w0 /secure/a5-r74/recipient.spki.der
sha256sum /secure/a5-r74/recipient.spki.der
~~~

Disclose **only** the canonical SPKI DER base64 and lowercase SHA256.
The producer's ci_private_evidence.public_key requires RSA-3072,
exponent 65537, canonical DER and exact digest. Never reuse historical
ciphertext, admission statuses, or another run's recipient.

## One source-bound reviewed run

A1/A6 selects phase1-native-graph and graph_case_mode=graph-flow on the
exact reviewed commit, passing only A5's SPKI public input. The original
runner must produce one artifact named
graph-originals-review-$SOURCE-$RUN-1 with these two files at its root:

- producer-originals.mwseal
- carrier.original.json

There must be zero Native cases before external owner-status admission.
No cross-run ZIP, historical ciphertext, local plaintext substitute,
modified source, or run_attempt greater than 1 is admissible.

As soon as the current encrypted artifact appears, the independent A5
identity downloads it. For example, on the external Linux reviewer:

~~~sh
gh run download "$RUN" -R seradotcom/motionwright \
  -n "graph-originals-review-$SOURCE-$RUN-1" \
  -D "/secure/a5-r74/$RUN"
# Inspect actual ZIP filename; do not assume one.
ls -la "/secure/a5-r74/$RUN"

python3 -B tooling/graph_r74_external_reviewer.py \
  --artifact-zip "/secure/a5-r74/$RUN/REPLACE_WITH_ACTUAL.zip" \
  --private-key /secure/a5-r74/recipient.pem \
  --trusted-product /secure/a5-checkouts/motionwright \
  --trusted-semwright /secure/a5-checkouts/semwright \
  --source-sha "$SOURCE" \
  --semwright-sha "$SEMWRIGHT_SOURCE" \
  --run-id "$RUN" \
  --output "/secure/a5-r74/$RUN/source-review.original.json"
~~~

The two trusted Git repositories are **independently fetched** by A5.
The verifier binds RSA-OAEP-SHA256 plus AES-GCM authenticated context,
run/source/recipient, complete regular snapshot members, producer index
and carrier digests, one-case callback roster, external binary copies,
and every recorded Motionwright/Semwright file against independently
trusted Git bytes and trees. A5 must still inspect important source
callbacks and Native authorizations separately.

A result of SOURCE_CARRIER_AUTHENTICATED_NO_OWNER_ADMISSION_NO_G2
establishes only ciphertext/source authenticity, not A5's final review,
a real Graph effect, or release acceptance.

## Original owner status — after genuine independent review only

Only the *original workflow run actor*, never the independent A5 reviewer,
may publish the one exact source-bound owner admission. After A5's
independent substantive review, with the current run ID and verified
index digest, the owner may publish:

~~~sh
gh api -X POST "repos/seradotcom/motionwright/statuses/$SOURCE" \
  -f state=success \
  -f context="mw-owner/phase1-native-graph/$RUN/1/graph-flow" \
  -f description="admission-sha256:$REVIEWED_INDEX_SHA256" \
  -f target_url="https://github.com/seradotcom/motionwright/actions/runs/$RUN"
~~~

The runner checks creator.id == run.actor.id, exact head_sha/run attempt
1, context, target URL and index digest. This is **owner admission only**,
not product or A5 acceptance. An A5-created status is deliberately
foreign and cannot satisfy the owner context.

If the reviewer cannot decrypt, source-verify, retain private-key
independence, reach GitHub in time, or substantively review the original
callback/Host authority, **do not post success**. Preserve HOLD and
original error evidence; never fake a new key, ticket, status, or
source manifest. After legitimate admission only graph-flow executes.
All six Graph cases, original Native/Host/SQLite/CAS post-effect
independent review, and formal G2/G3 remain separate obligations.
