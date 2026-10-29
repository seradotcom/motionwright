import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { inspectSourceBoundCandidate, requireNativeGraphParentAdmission,
  admitOriginalProducers, runIndependentSameStoreReopen } from "../../tests/support/graph-owner/producer_guard.mjs";

function candidate() {
  const operations = ["authoring_context", "upsert"];
  const launches = [{ launch_index: 0, operations }, { launch_index: 1, operations: [] }];
  const cli = 1 + 2 + 8 * operations.length;
  const body = {
    schema: "motionwright.native-graph-source-case/1",
    case_id: "graph-flow", test_title: "Synthetic flow stores exact event times and rejects a real stale CAS without a new event",
    source: { head: "a".repeat(40), tree: "b".repeat(40),
      test_file_sha256: "c".repeat(64), sdk: "d".repeat(40) },
    launches, prebackend_denials: ["stale CAS grant"],
    proposed_parent_budget: { cli, files: cli * 3, bytes: cli * 256 },
    admission: "DENY_INDEPENDENT_ORIGINAL_PRODUCER_MISSING",
    native_authoring_approved: false, automatic_retry: false,
  };
  return { ...body, digest_sha256: createHash("sha256").update(JSON.stringify(body)).digest("hex") };
}
test("reviewed source shape never grants a Native callback", () => {
  const result = inspectSourceBoundCandidate(candidate());
  assert.equal(result.status, "DENY_INDEPENDENT_ORIGINAL_NATIVE_PRODUCER_AND_BUILD_REQUIRED");
  assert.equal(result.admitted_callback_cap, null);
  assert.equal(result.authored_callback_candidates, 2);
  assert.equal(result.side_effects, 0);
  assert.throws(() => requireNativeGraphParentAdmission(candidate(), { approved: true }),
    /GRAPH_NATIVE_PARENT_ORIGINALS_AND_INDEPENDENT_APPROVAL_MISSING/);
});
test("caller approval, cap override or replay cannot amend a source plan", () => {
  for (const change of [
    { callback_cap: 100 }, { native_authoring_approved: true },
    { automatic_retry: true }, { admission: "PASS" },
  ]) {
    assert.throws(() => inspectSourceBoundCandidate({ ...candidate(), ...change }), /GRAPH_/);
  }
});
test("invalid callback identity, launch order and capacities fail closed", () => {
  const badOperations = candidate();
  badOperations.launches[0].operations = ["unreviewed-backend-effect"];
  assert.throws(() => inspectSourceBoundCandidate(badOperations), /GRAPH_UNAUTHORED_CALLBACK/);
  const skipped = candidate();
  skipped.launches[1].launch_index = 8;
  assert.throws(() => inspectSourceBoundCandidate(skipped), /GRAPH_LAUNCH_ORDER/);
  const forged = candidate();
  forged.proposed_parent_budget.bytes += 1;
  assert.throws(() => inspectSourceBoundCandidate(forged), /GRAPH_PARENT_CANDIDATE_MATH/);
});
test("missing or tampered plan digest and source identity are never accepted", () => {
  const missing = candidate(); delete missing.digest_sha256;
  assert.throws(() => inspectSourceBoundCandidate(missing), /GRAPH_CANDIDATE_SHAPE/);
  const altered = candidate(); altered.source.head = "x".repeat(64);
  assert.throws(() => inspectSourceBoundCandidate(altered), /GRAPH_CASE_SOURCE_IDENTITY/);
  const changed = candidate(); changed.test_title += " amended after source seal";
  assert.throws(() => inspectSourceBoundCandidate(changed), /GRAPH_CASE_DIGEST/);
});

test("actual companion six case outputs reach review DENY rather than invalid source width", async () => {
  const { sixSourceAuthoredGraphCases } = await import("../../tests/support/nativeGraphCase.ts");
  const real = sixSourceAuthoredGraphCases();
  assert.equal(real.length, 6);
  for (const candidate of real) {
    const review = inspectSourceBoundCandidate(candidate);
    assert.equal(review.candidate_sha256, candidate.digest_sha256);
    assert.equal(review.status, "DENY_INDEPENDENT_ORIGINAL_NATIVE_PRODUCER_AND_BUILD_REQUIRED");
    assert.equal(review.admitted_callback_cap, null);
    assert.equal(review.side_effects, 0);
  }
});
test("wrong source widths or source forgery fail before review", () => {
  const longGit = candidate();
  longGit.source.head = "a".repeat(64);
  assert.throws(() => inspectSourceBoundCandidate(longGit), /GRAPH_CASE_SOURCE_IDENTITY/);
  const shortDigest = candidate();
  shortDigest.source.test_file_sha256 = "c".repeat(40);
  assert.throws(() => inspectSourceBoundCandidate(shortDigest), /GRAPH_CASE_SOURCE_IDENTITY/);
  const alienSDK = candidate();
  alienSDK.source.sdk = "z".repeat(40);
  assert.throws(() => inspectSourceBoundCandidate(alienSDK), /GRAPH_CASE_SOURCE_IDENTITY/);
});
test("retained R57 private factory entry points stay fail closed", async () => {
  await assert.rejects(admitOriginalProducers({ approved: true }),
    /SOURCE_PRODUCER_ORIGINALS_AND_REVIEWED_CONTRACT_UNAVAILABLE/);
  await assert.rejects(runIndependentSameStoreReopen({ verified: true }),
    /INDEPENDENT_SAME_STORE_REOPEN_PRODUCER_AND_REVIEWED_CONTRACT_UNAVAILABLE/);
});
