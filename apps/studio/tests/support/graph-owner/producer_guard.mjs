/**
 * Graph case pre-admission source verifier, deliberately read-only and DENY.
 * The independently reviewed A6 OriginalWriter/SharedPool must bind the real
 * authoring test, service build, Native build, actual Host, opened Store and
 * helper launch tuples before producing any MOTIONWRIGHT_GRAPH_CALLBACK_CAP.
 * Never use this parser or a caller string as that approval.
 */
import { createHash } from "node:crypto";

const OPERATIONS = new Set(["authoring_context", "upsert", "remove", "operation_recovery"]);
const EXPECTED_CASES = new Set([
  "graph-source-review", "graph-flow", "graph-lost-reply", "graph-removal",
  "graph-label-causal-diagram", "graph-label-system-flow",
]);
function need(value, reason) { if (!value) throw new Error(reason); }
function exact(value, names, reason) {
  need(value && typeof value === "object" && !Array.isArray(value), reason);
  need(Object.keys(value).sort().join("|") === names.slice().sort().join("|"), reason);
}
const hash = value => createHash("sha256").update(value).digest("hex");
// Three Git object IDs in this source are SHA-1 (40 hex); the test hash and
// candidate digest are SHA-256 (64 hex). Never widen either class.
const canonicalGitObject = raw => typeof raw === "string" && /^[0-9a-f]{40}$/.test(raw);
const canonicalDigest = raw => typeof raw === "string" && /^[0-9a-f]{64}$/.test(raw);
const safe = number => Number.isSafeInteger(number) && number >= 0;
const equality = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/**
 * This accepts source evidence for review only.  No branch of this module
 * grants native authority, even when an untrusted caller fabricates a success.
 */
export function inspectSourceBoundCandidate(candidate, externalOriginals = null) {
  const DENY = "DENY_INDEPENDENT_ORIGINAL_NATIVE_PRODUCER_AND_BUILD_REQUIRED";
  exact(candidate, [
    "schema", "case_id", "test_title", "source", "launches", "prebackend_denials",
    "proposed_parent_budget", "digest_sha256", "admission",
    "native_authoring_approved", "automatic_retry",
  ], "GRAPH_CANDIDATE_SHAPE");
  need(candidate.schema === "motionwright.native-graph-source-case/1", "GRAPH_CASE_SCHEMA");
  need(EXPECTED_CASES.has(candidate.case_id), "GRAPH_CASE_ID");
  need(typeof candidate.test_title === "string" && candidate.test_title.length > 40, "GRAPH_TEST_TITLE");
  exact(candidate.source, ["head", "tree", "test_file_sha256", "sdk"], "GRAPH_CASE_SOURCE_SHAPE");
  need(canonicalGitObject(candidate.source.head) &&
    canonicalGitObject(candidate.source.tree) &&
    canonicalGitObject(candidate.source.sdk) &&
    canonicalDigest(candidate.source.test_file_sha256),
    "GRAPH_CASE_SOURCE_IDENTITY");
  need(Array.isArray(candidate.launches) && candidate.launches.length >= 2 && candidate.launches.length <= 8,
    "GRAPH_CASE_LAUNCH_COUNT");
  let callbackTotal = 0;
  for (const [index, launch] of candidate.launches.entries()) {
    exact(launch, ["launch_index", "operations"], "GRAPH_LAUNCH_SHAPE");
    need(launch.launch_index === index && Array.isArray(launch.operations)
      && launch.operations.length <= 64, "GRAPH_LAUNCH_ORDER");
    for (const operation of launch.operations) {
      need(OPERATIONS.has(operation), "GRAPH_UNAUTHORED_CALLBACK");
      callbackTotal++;
    }
  }
  need(candidate.launches.at(-1).operations.length === 0, "GRAPH_CLOSED_READONLY_LAUNCH");
  need(Array.isArray(candidate.prebackend_denials)
    && candidate.prebackend_denials.every(value => typeof value === "string" && value.length > 1),
    "GRAPH_DENIAL_PLAN");
  const cli = 1 + candidate.launches.length + callbackTotal * 8;
  exact(candidate.proposed_parent_budget, ["cli", "files", "bytes"], "GRAPH_PARENT_CANDIDATE_SHAPE");
  need(safe(cli) && cli >= 2 && cli <= 65536
    && equality(candidate.proposed_parent_budget, { cli, files: cli * 3, bytes: cli * 256 }),
    "GRAPH_PARENT_CANDIDATE_MATH");
  need(candidate.proposed_parent_budget.files <= 4096
    && candidate.proposed_parent_budget.bytes <= 512 * 1024 * 1024,
    "GRAPH_PARENT_CANDIDATE_SHARED_POOL");
  need(candidate.admission === "DENY_INDEPENDENT_ORIGINAL_PRODUCER_MISSING"
    && candidate.native_authoring_approved === false && candidate.automatic_retry === false,
    "GRAPH_NATIVE_SELF_APPROVAL");
  const { digest_sha256, ...body } = candidate;
  need(canonicalDigest(digest_sha256)
    && hash(JSON.stringify(body)) === digest_sha256, "GRAPH_CASE_DIGEST");
  // Even an apparently matching original source does not prove that an
  // independently approved Native backend reached the same owner and Store.
  if (externalOriginals !== null) {
    need(typeof externalOriginals === "object" && !Array.isArray(externalOriginals), "GRAPH_EXTERNAL_ORIGINAL_SHAPE");
  }
  return Object.freeze({
    schema: "motionwright.native-graph-candidate-review/1",
    case_id: candidate.case_id,
    candidate_sha256: digest_sha256,
    authored_launch_count: candidate.launches.length,
    authored_callback_candidates: callbackTotal,
    candidate_cli_budget: cli,
    status: DENY,
    admitted_callback_cap: null,
    caller_granted_authority: false,
    side_effects: 0,
  });
}
export function requireNativeGraphParentAdmission(candidate, original) {
  inspectSourceBoundCandidate(candidate, original);
  throw new Error("GRAPH_NATIVE_PARENT_ORIGINALS_AND_INDEPENDENT_APPROVAL_MISSING");
}

/**
 * The reviewed R57 private factory imports these two names. Retain its exact
 * denying interface until the independent authentic build/Store/Host/owner
 * producers are bound. A shaped candidate or approved:true cannot replace
 * the missing original or issue a capability.
 */
export async function admitOriginalProducers() {
  throw new Error("SOURCE_PRODUCER_ORIGINALS_AND_REVIEWED_CONTRACT_UNAVAILABLE");
}
export async function runIndependentSameStoreReopen() {
  throw new Error("INDEPENDENT_SAME_STORE_REOPEN_PRODUCER_AND_REVIEWED_CONTRACT_UNAVAILABLE");
}
