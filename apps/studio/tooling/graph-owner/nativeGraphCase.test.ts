import { describe, expect, it } from "vitest";
import { createHash } from "node:crypto";
import {
  canonicalNonnegativeUsize, inspectNativeGraphCase, sourceBoundNativeLaunchObserver,
  type SourceAuthoredCase,
  requireIndependentlyApprovedNativeCase, sixSourceAuthoredGraphCases,
  type NativeGraphCaseId,
} from "../../tests/support/nativeGraphCase";

describe("Native Graph candidate source custody", () => {
  const cases = sixSourceAuthoredGraphCases();

  it("enumerates only six source-bound real-service cases, all DENY", () => {
    expect(cases).toHaveLength(6);
    expect(new Set(cases.map(item => item.case_id)).size).toBe(6);
    for (const item of cases) {
      expect(item.source.head).toBe("898d1956afad00b74e50dbf4bd57c3e18da275e6");
      expect(item.source.sdk).toBe("6725e7098546a4d47ab9314ce1d571b30c83a26c");
      expect(item.source.test_file_sha256).toMatch(/^[0-9a-f]{64}$/);
      expect(item.admission).toBe("DENY_INDEPENDENT_ORIGINAL_PRODUCER_MISSING");
      expect(item.native_authoring_approved).toBe(false);
      expect(item.automatic_retry).toBe(false);
    }
  });

  it("models every native child restart without carrying unused callback credit", () => {
    expect(cases.map(item => item.launches.length)).toEqual([2, 2, 2, 3, 3, 3]);
    for (const item of cases) {
      for (const [i, launch] of item.launches.entries()) {
        expect(launch.launch_index).toBe(i);
        expect(Object.isFrozen(launch.operations)).toBe(true);
      }
      expect(item.launches.at(-1)?.operations).toEqual([]);
      const cli = 1 + item.launches.reduce((sum, launch) => sum + 1 + 8 * launch.operations.length, 0);
      expect(item.proposed_parent_budget).toEqual({ cli, files: 3 * cli, bytes: 256 * cli });
    }
  });

  it("does not treat service-level prebackend rejection as a Native callback", () => {
    expect(inspectNativeGraphCase("graph-removal").prebackend_denials).toEqual([
      "prior-session grant", "stale CAS grant",
    ]);
    expect(inspectNativeGraphCase("graph-flow").prebackend_denials).toEqual(["stale CAS grant"]);
    expect(inspectNativeGraphCase("graph-lost-reply").launches[0].operations).toContain("operation_recovery");
  });

  it("hashes source candidates without authorizing a process or mutation", () => {
    for (const item of cases) {
      const { digest_sha256, ...body } = item;
      expect(digest_sha256).toBe(createHash("sha256").update(JSON.stringify(body)).digest("hex"));
      expect(Object.isFrozen(item)).toBe(true);
      expect(() => requireIndependentlyApprovedNativeCase(item))
        .toThrow("GRAPH_AUTHENTIC_PRODUCER_BUILD_AND_ORIGINAL_REVIEW_REQUIRED");
    }
  });

  it("rejects unselected case IDs and prototype properties", () => {
    for (const name of ["", "__proto__", "constructor", "graph-seventh"] as string[]) {
      expect(() => inspectNativeGraphCase(name as NativeGraphCaseId)).toThrow("GRAPH_CASE_NOT_AUTHORED");
    }
  });

  it("only parses canonical decimal callback counts; never grants them", () => {
    for (const value of ["0", "1", String(Number.MAX_SAFE_INTEGER)]) {
      expect(canonicalNonnegativeUsize(value)).toBe(true);
    }
    for (const value of [null, undefined, 0, 1, true, "", "00", "+1", "-1",
      " 1", "1 ", "1.0", "0x01", "1e2", "999999999999999999999", "１", "١"]) {
      expect(canonicalNonnegativeUsize(value)).toBe(false);
    }
  });
});

describe("source-only real-helper launch tuple reconciliation", () => {
  it("accepts only an original ordered tuple with same helper, Store and Native executable", () => {
    const candidate = inspectNativeGraphCase("graph-flow");
    const expected = { binary_path: "C:\\native.exe", database_path: "C:\\studio.sqlite3" };
    const observer = sourceBoundNativeLaunchObserver(candidate, expected);
    const helper_id = "123e4567-e89b-42d3-a456-426614174000";
    for (const launch_index of [0, 1]) {
      const observed = Object.freeze({ helper_id, launch_index, ...expected });
      const result = observer.reserve(observed);
      expect(result.case_digest_sha256).toBe(candidate.digest_sha256);
      expect(result.admission).toBe("DENY_INDEPENDENT_ORIGINAL_PRODUCER_MISSING");
    }
    expect(() => observer.assertAllObserved()).not.toThrow();
    expect(() => observer.reserve(Object.freeze({ helper_id, launch_index: 2, ...expected }))).toThrow();
  });
  it("latches a mismatched launch without a retry/refund", () => {
    const candidate = inspectNativeGraphCase("graph-removal");
    const expected = { binary_path: "C:\\native.exe", database_path: "C:\\store.sqlite3" };
    const observer = sourceBoundNativeLaunchObserver(candidate, expected);
    const tuple = Object.freeze({ helper_id: "123e4567-e89b-42d3-a456-426614174000",
      launch_index: 0, binary_path: "C:\\other.exe", database_path: expected.database_path });
    expect(() => observer.reserve(tuple)).toThrow("GRAPH_CASE_ACTUAL_HELPER_LAUNCH_OR_STORE_MISMATCH");
    expect(() => observer.reserve(Object.freeze({ ...tuple, binary_path: expected.binary_path })))
      .toThrow("GRAPH_CASE_ORIGINAL_RESERVATION_TAINTED");
  });
});

describe("source-bound producer observer immutable original custody", () => {
  it("rejects replaced callback bodies even with retained valid case ID and digest", () => {
    const source = inspectNativeGraphCase("graph-flow");
    const expected = { binary_path: "C:\\\\native.exe", database_path: "C:\\\\studio.sqlite3" };
    const changed = structuredClone(source) as unknown as {
      launches: Array<{ launch_index: number; operations: Array<string> }>;
    };
    changed.launches[0].operations[0] = "remove";
    expect(() => sourceBoundNativeLaunchObserver(changed as SourceAuthoredCase, expected))
      .toThrow("GRAPH_CASE_SOURCE_CANDIDATE_CHANGED");
    const extra = structuredClone(source) as unknown as {
      launches: Array<{ launch_index: number; operations: Array<string> }>;
    };
    extra.launches.push({ launch_index: 2, operations: [] });
    expect(() => sourceBoundNativeLaunchObserver(extra as SourceAuthoredCase, expected))
      .toThrow("GRAPH_CASE_SOURCE_CANDIDATE_CHANGED");
  });

  it("pins actual binary/Store values against later caller alias mutation", () => {
    const source = inspectNativeGraphCase("graph-flow");
    const originally = { binary_path: "native-original.exe", database_path: "store-original.sqlite3" };
    const observer = sourceBoundNativeLaunchObserver(source, originally);
    originally.binary_path = "native-substituted.exe";
    originally.database_path = "store-substituted.sqlite3";
    const helper_id = "123e4567-e89b-42d3-a456-426614174000";
    for (const launch_index of [0, 1]) {
      expect(observer.reserve(Object.freeze({ helper_id, launch_index,
        binary_path: "native-original.exe", database_path: "store-original.sqlite3" }))
        .candidate_callback_operations).toEqual(source.launches[launch_index].operations);
    }
    expect(() => observer.assertAllObserved()).not.toThrow();
  });
});

describe("retained source snapshots resist post-construction caller mutations", () => {
  it("uses canonical frozen callback operations after original input is changed", () => {
    const source = inspectNativeGraphCase("graph-flow");
    const mutable = structuredClone(source) as unknown as {
      launches: Array<{ launch_index: number; operations: string[] }>;
    };
    const expected = { binary_path: "native-original.exe", database_path: "store-original.sqlite3" };
    const observer = sourceBoundNativeLaunchObserver(mutable as SourceAuthoredCase, expected);
    mutable.launches[0].operations[0] = "remove";
    mutable.launches.push({ launch_index: 2, operations: ["upsert"] });
    const helper_id = "123e4567-e89b-42d3-a456-426614174000";
    const first = observer.reserve(Object.freeze({ helper_id, launch_index: 0, ...expected }));
    expect(first.candidate_callback_operations).toEqual(["authoring_context", "upsert"]);
    const final = observer.reserve(Object.freeze({ helper_id, launch_index: 1, ...expected }));
    expect(final.candidate_callback_operations).toEqual([]);
    expect(() => observer.assertAllObserved()).not.toThrow();
  });
});
