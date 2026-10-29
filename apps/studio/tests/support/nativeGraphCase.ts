/**
 * Six source-authored Native Graph candidate cases. Source-only, always DENY.
 * Independent A1/A6 original producer/build/Host review must bind actual
 * callback demand and one SharedPool before initializer/SQLite/new client.
 * No caller-provided cap, posthoc count, pool reset, refund or replay.
 */
import { createHash } from "node:crypto";
import { lstatSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { ServiceLaunchContext } from "./scopedChangeService";

export type NativeGraphOperation = "authoring_context" | "upsert" | "remove" | "operation_recovery";
export type NativeGraphCaseId = "graph-source-review" | "graph-flow" | "graph-lost-reply"
  | "graph-removal" | "graph-label-causal-diagram" | "graph-label-system-flow";
export interface SourceAuthoredCase {
  readonly schema: "motionwright.native-graph-source-case/1";
  readonly case_id: NativeGraphCaseId;
  readonly test_title: string;
  readonly source: Readonly<{ head: string; tree: string; test_file_sha256: string; sdk: string }>;
  readonly launches: readonly Readonly<{ launch_index: number; operations: readonly NativeGraphOperation[] }>[];
  readonly prebackend_denials: readonly string[];
  readonly proposed_parent_budget: Readonly<{ cli: number; files: number; bytes: number }>;
  readonly digest_sha256: string;
  readonly admission: "DENY_INDEPENDENT_ORIGINAL_PRODUCER_MISSING";
  readonly native_authoring_approved: false;
  readonly automatic_retry: false;
}
const HEAD = "898d1956afad00b74e50dbf4bd57c3e18da275e6";
const TREE = "5b15bbf53a7caf512184ebc6e7efcb0f219cd925";
const SDK = "6725e7098546a4d47ab9314ce1d571b30c83a26c";
const TEST_SHA = "53fad1abd4ec85afa6f47b5b7b5305230bdef97a1acdbd34260b67ff6ba76beb";
const sourcePath = fileURLToPath(new URL("../semantic-graph-service.spec.ts", import.meta.url));
const title: Record<NativeGraphCaseId, string> = {
  "graph-source-review": "Graph creates, edits, reopens SQLite, persists an actual review and rechecks it after editing",
  "graph-flow": "Synthetic flow stores exact event times and rejects a real stale CAS without a new event",
  "graph-lost-reply": "A lost Graph reply cannot replay a committed SQLite mutation after reload",
  "graph-removal": "Graph removal rejects a prior-session grant and stale base, then removes once through the real UI and survives SQLite restart",
  "graph-label-causal-diagram": "Graph causal_diagram follows the edge midpoint through real saves and restores manual placement after SQLite restart",
  "graph-label-system-flow": "Graph system_flow follows the edge midpoint through real saves and restores manual placement after SQLite restart",
};
/**
 * Source-authored proposed callback order, not a production C_i. Actual
 * callback intent must still be verified from the original service/Host
 * before any capacity can be admitted. Denied grant requests are not Native
 * callbacks, and each restart is a new Native launch.
 */
const ops: Record<NativeGraphCaseId, NativeGraphOperation[][]> = {
  "graph-source-review": [
    ["authoring_context", "authoring_context", "upsert", "authoring_context", "upsert"], [],
  ],
  "graph-flow": [["authoring_context", "upsert"], []],
  "graph-lost-reply": [
    ["authoring_context", "upsert", "operation_recovery", "authoring_context", "upsert"], [],
  ],
  "graph-removal": [
    ["authoring_context", "upsert"], ["authoring_context", "remove"], [],
  ],
  "graph-label-causal-diagram": [
    ["authoring_context", "authoring_context", "upsert", "authoring_context", "upsert"],
    ["authoring_context", "upsert"], [],
  ],
  "graph-label-system-flow": [
    ["authoring_context", "authoring_context", "upsert", "authoring_context", "upsert"],
    ["authoring_context", "upsert"], [],
  ],
};
const beforeBackend: Record<NativeGraphCaseId, readonly string[]> = {
  "graph-source-review": [],
  "graph-flow": ["stale CAS grant"],
  "graph-lost-reply": [],
  "graph-removal": ["prior-session grant", "stale CAS grant"],
  "graph-label-causal-diagram": [],
  "graph-label-system-flow": [],
};
function checkOriginalCaseSource(id: NativeGraphCaseId): void {
  const stat = lstatSync(sourcePath);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1) throw Error("GRAPH_CASE_SOURCE_FILE_KIND");
  const raw = readFileSync(sourcePath);
  if (createHash("sha256").update(raw).digest("hex") !== TEST_SHA) throw Error("GRAPH_CASE_SOURCE_CHANGED");
  const text = raw.toString("utf8");
  const quotedLabel = "test(" + String.fromCharCode(96) + "Graph " + "$" + "{kind} follows the edge midpoint through real saves and restores manual placement after SQLite restart" + String.fromCharCode(96);
  const needle = id.startsWith("graph-label-") ? quotedLabel : 'test("' + title[id] + '"';
  if (!text.includes(needle)) throw Error("GRAPH_CASE_TEST_MISSING");
  if (id.startsWith("graph-label-") && !text.includes('for (const kind of ["causal_diagram", "system_flow"] as const)')) {
    throw Error("GRAPH_CASE_VARIANT_SOURCE_MISSING");
  }
}
export function inspectNativeGraphCase(id: NativeGraphCaseId): SourceAuthoredCase {
  if (!Object.hasOwn(ops, id) || !Object.hasOwn(title, id)) throw Error("GRAPH_CASE_NOT_AUTHORED");
  checkOriginalCaseSource(id);
  const launches = Object.freeze(ops[id].map((sequence, launch_index) => Object.freeze({
    launch_index, operations: Object.freeze([...sequence]),
  })));
  const cli = 1 + launches.reduce((total, item) => total + 1 + 8 * item.operations.length, 0);
  if (!Number.isSafeInteger(cli) || cli < 2 || cli > 65536) throw Error("GRAPH_CASE_BUDGET_OVERFLOW");
  const proposal = {
    schema: "motionwright.native-graph-source-case/1" as const,
    case_id: id, test_title: title[id],
    source: Object.freeze({ head: HEAD, tree: TREE, test_file_sha256: TEST_SHA, sdk: SDK }),
    launches, prebackend_denials: Object.freeze([...beforeBackend[id]]),
    proposed_parent_budget: Object.freeze({ cli, files: cli * 3, bytes: cli * 256 }),
    admission: "DENY_INDEPENDENT_ORIGINAL_PRODUCER_MISSING" as const,
    native_authoring_approved: false as const, automatic_retry: false as const,
  };
  return Object.freeze({ ...proposal, digest_sha256: createHash("sha256").update(JSON.stringify(proposal)).digest("hex") });
}
export function sixSourceAuthoredGraphCases(): readonly SourceAuthoredCase[] {
  return Object.freeze((Object.keys(title) as NativeGraphCaseId[]).map(inspectNativeGraphCase));
}
/** No in-test synthetic owner/boolean can grant Native effects. */
export function requireIndependentlyApprovedNativeCase(_case: SourceAuthoredCase): never {
  throw Error("GRAPH_AUTHENTIC_PRODUCER_BUILD_AND_ORIGINAL_REVIEW_REQUIRED");
}
/** Parser-only helper, never a C_i or credential issuer. */
export function canonicalNonnegativeUsize(raw: unknown): boolean {
  if (typeof raw !== "string" || !/^(0|[1-9][0-9]*)$/.test(raw)) return false;
  try { return BigInt(raw) <= BigInt(Number.MAX_SAFE_INTEGER); } catch { return false; }
}


/**
 * One-shot source-only observer for the ORIGINAL ScopedChangeService's
 * reserveLaunch(context) callback, invoked before credentials or child spawn.
 * It binds the real helper UUID, binary and Store to a reviewed case candidate
 * and checks every reopen. It creates neither a pool nor a client capability.
 */
export function sourceBoundNativeLaunchObserver(
  candidate: SourceAuthoredCase,
  expected: Readonly<{ binary_path: string; database_path: string }>,
): Readonly<{
  reserve(context: Readonly<ServiceLaunchContext>): Readonly<{
    helper_id: string; launch_index: number; case_digest_sha256: string;
    candidate_callback_operations: readonly NativeGraphOperation[];
    admission: "DENY_INDEPENDENT_ORIGINAL_PRODUCER_MISSING";
  }>;
  assertAllObserved(): void;
}> {
  const canonical = inspectNativeGraphCase(candidate.case_id);
  // A caller must supply the entire exact source-authored immutable body, not
  // just a retained case ID/digest attached to substituted operations.
  if (JSON.stringify(candidate) !== JSON.stringify(canonical)) {
    throw Error("GRAPH_CASE_SOURCE_CANDIDATE_CHANGED");
  }
  const pinned = Object.freeze({
    binary_path: expected.binary_path, database_path: expected.database_path,
  });
  if (typeof pinned.binary_path !== "string" || !pinned.binary_path ||
      typeof pinned.database_path !== "string" || !pinned.database_path) {
    throw Error("GRAPH_CASE_ORIGINAL_BUILD_OR_STORE_MISSING");
  }
  let next = 0, originalHelperId: string | null = null, stopped = false;
  const reserve = (context: Readonly<ServiceLaunchContext>) => {
    if (stopped) throw Error("GRAPH_CASE_ORIGINAL_RESERVATION_TAINTED");
    try {
      if (!Object.isFrozen(context) ||
          !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(context.helper_id) ||
          context.binary_path !== pinned.binary_path ||
          context.database_path !== pinned.database_path ||
          next >= canonical.launches.length ||
          context.launch_index !== next ||
          (originalHelperId !== null && originalHelperId !== context.helper_id)) {
        throw Error("GRAPH_CASE_ACTUAL_HELPER_LAUNCH_OR_STORE_MISMATCH");
      }
      if (originalHelperId === null) originalHelperId = context.helper_id;
      const expectedLaunch = canonical.launches[next];
      next += 1; // Never refund a failed/uncertain original launch.
      return Object.freeze({
        helper_id: context.helper_id, launch_index: context.launch_index,
        case_digest_sha256: canonical.digest_sha256,
        candidate_callback_operations: expectedLaunch.operations,
        admission: "DENY_INDEPENDENT_ORIGINAL_PRODUCER_MISSING" as const,
      });
    } catch (error) { stopped = true; throw error; }
  };
  const assertAllObserved = () => {
    if (stopped || next !== canonical.launches.length) {
      throw Error("GRAPH_CASE_ORIGINAL_LAUNCH_ROSTER_INCOMPLETE");
    }
  };
  return Object.freeze({ reserve, assertAllObserved });
}
