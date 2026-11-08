# Independent Graph pre-effect review

The reviewer examines an encrypted, source-bound Graph producer carrier against independently fetched Git source. The tool does not execute Native, Host or Studio processes, publish commit statuses, or grant release acceptance.

## Secret isolation

Use a trusted Windows account or a POSIX account, including WSL with a genuine Linux filesystem. Physical machine separation is not required. The reviewer must control the decryption key and trusted source checkouts independently of the producer. Never expose the private key to the producer process, its runner, its checkout, logs, artifacts, or messages. Keep the key outside both trusted Git repositories.

The current tool rejects GitHub Actions and producer-tagged environments. A separate hosted reviewer would require an independently reviewed secret and approval isolation design; this implementation does not enable that route. Filesystem checks establish key access controls, not the substantive independence of an approval.

The reviewer must be available to finish the original live review within the producer's 1,200-second admission window. A scheduled check that runs after that window cannot provide admission.

## POSIX key custody

Use an owner-only directory on a genuine POSIX filesystem. A WSL path on a Windows-mounted volume must not rely on simulated mode bits. The reader requires the current effective user to own a regular file with one link and no group or other permissions. Symlink paths are rejected.

```sh
umask 077
mkdir -p /secure/graph-review
chmod 700 /secure/graph-review
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 \
  -pkeyopt rsa_keygen_pubexp:65537 -out /secure/graph-review/recipient.pem
chmod 600 /secure/graph-review/recipient.pem
```

## Windows key custody

Use a local directory outside the repositories. Protect the directory before creating a key. The following PowerShell commands use the current Windows token's user SID; replace the example directory with the reviewer's private directory.

```powershell
$reviewDirectory = 'C:\GraphReviewPrivate'
$reviewSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
New-Item -ItemType Directory -Path $reviewDirectory -ErrorAction Stop
icacls $reviewDirectory /setowner "*${reviewSid}"
icacls $reviewDirectory /inheritance:r /grant:r "*${reviewSid}:(OI)(CI)F"
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -pkeyopt rsa_keygen_pubexp:65537 -out "$reviewDirectory\recipient.pem"
icacls "$reviewDirectory\recipient.pem" /setowner "*${reviewSid}"
icacls "$reviewDirectory\recipient.pem" /inheritance:r /grant:r "*${reviewSid}:F"
```

Check every command's exit code before continuing. The file reader requires the owner to match the actual current token user, a protected DACL with one explicit current-user full-control or file-read grant, an ordinary file with exactly one link, and 1,025 through 16,384 bytes. It rejects inherited or foreign grants, reparse points, junction ancestors, UNC/device paths and alternate streams. It holds a file handle that denies sharing writes and deletion, reads security from that handle, and rechecks identity and security after reading. It never changes the key's ACL automatically.

Disclose only the RSA-3072/e65537 public key's canonical SPKI DER base64 and SHA256. Never send the private key or reuse another run's recipient or ciphertext.

## Retain and authenticate the original carrier

Select the exact reviewed commit and graph-flow case, passing only the public recipient. Before admission, the producer must execute zero Native cases. Retain the raw artifact ZIP with `tooling/graph_review_artifact.py`; use its `--help` for the source and run identity arguments. `gh run download` extracts artifacts and does not preserve the ZIP expected by the verifier.

The verifier also accepts an extracted directory containing exactly the same authenticated originals through its --artifact-path input; --artifact-zip remains a compatible alias. The raw ZIP transport additionally retains the GitHub artifact identity and digest.

The original artifact contains exactly these root members:

- `producer-originals.mwseal`
- `carrier.original.json`

Invoke the verifier with the retained ZIP, private key, independently fetched product and dependency repositories, exact source revisions and original run ID:

```sh
python -B tooling/graph_r74_external_reviewer.py \
  --artifact-zip /secure/graph-review/current.original.zip \
  --private-key /secure/graph-review/recipient.pem \
  --trusted-product /trusted/motionwright \
  --trusted-semwright /trusted/semwright \
  --source-sha "$SOURCE" --semwright-sha "$SEMWRIGHT_SOURCE" \
  --run-id "$RUN" --output /secure/graph-review/source-review.original.json
```

On Windows, use absolute Windows paths and PowerShell line continuation or a single command line. The tool authenticates encryption context, source/run/recipient identities, complete snapshot members, producer index, retained binary copies and source bytes against both trusted Git trees. A successful result establishes carrier and source authenticity only. The independent reviewer must still examine the original callbacks and process authority substantively.

## Genuine admission

Only after genuine independent review may the original workflow run actor publish the exact source-bound owner admission. The independent reviewer does not publish it. The producer checks the actor, exact source/run attempt, case context, target URL and reviewed index digest.

If decryption, source verification, custody, timing or substantive review fails, preserve the original failure evidence and withhold admission. Never synthesize an approval, ticket, key lineage, status or manifest. Actual Graph effects, process closure, original SQLite/CAS evidence and release acceptance remain separate verification obligations.
