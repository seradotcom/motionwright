import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyChange } from "./api";
import { readSemanticGraphOperationRecovery } from "./semanticGraphService";
import { GRAPH_RECOVERY_READ_ERROR } from "./semanticGraphRecovery";
import { fixtureBootstrap } from "./fixture";
import { graphSourceSnapshot, newSemanticGraph, type SemanticGraphChange } from "./semanticGraph";
import type { Change, Project } from "./types";

const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));

const ORIGINAL_KEY = "ABCDEF12-3456-4789-ABCD-EF1234567890";
const GRANT = "10101010-2020-4030-8040-505050505050";
const GENERATED_KEY = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";

function source(remove: boolean): { project: Project; change: SemanticGraphChange } {
  const project = structuredClone(fixtureBootstrap.project);
  const graph = newSemanticGraph(project, project.scenes[0], project.deliverables[0], "causal_diagram");
  graph.authorship.author_label = "Wire test author"; graph.authorship.rationale = "Synthetic request identity fixture";
  return { project, change: remove
    ? { type: "remove_semantic_graph", graph_id: graph.id, scene_id: graph.scene_id, source_snapshot: graphSourceSnapshot(project) }
    : { type: "upsert_semantic_graph", graph } };
}

beforeEach(() => {
  invokeMock.mockReset();
  vi.stubGlobal("window", { __TAURI_INTERNALS__: {} });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("retained Graph application identity at the existing mutation boundary", () => {
  for (const remove of [false, true]) {
    it(`uses the exact retained key for the grant and ${remove ? "Remove" : "Upsert"} request`, async () => {
      const { project, change } = source(remove);
      const frozen = structuredClone(change);
      const generated = vi.spyOn(crypto, "randomUUID");
      const response = { ...structuredClone(project), revision: project.revision + 1 };
      invokeMock.mockResolvedValueOnce({ token: GRANT }).mockResolvedValueOnce(response);

      expect(await applyChange(project, change, ORIGINAL_KEY)).toBe(response);
      expect(generated).not.toHaveBeenCalled();
      expect(invokeMock.mock.calls).toEqual([
        ["issue_effect_grant", { request: {
          effect: "project_edit", project_id: project.id, generation: project.generation,
          revision: project.revision, subject: ORIGINAL_KEY,
        } }],
        ["apply_change", { request: {
          project_id: project.id, generation: project.generation, revision: project.revision,
          request_id: ORIGINAL_KEY, effect_grant: GRANT, change: frozen,
        } }],
      ]);
      expect(change).toEqual(frozen);
    });
  }

  it("does not invent a key when a Graph caller lost its retained identity", async () => {
    const { project, change } = source(false);
    const generated = vi.spyOn(crypto, "randomUUID");
    await expect(applyChange(project, change)).rejects.toThrow("original Graph request identity");
    expect(invokeMock).not.toHaveBeenCalled();
    expect(generated).not.toHaveBeenCalled();
  });

  it("rejects blank, padded, malformed and non-string Graph keys before requesting a grant", async () => {
    const { project, change } = source(true);
    const generated = vi.spyOn(crypto, "randomUUID");
    for (const key of ["", " " + ORIGINAL_KEY, ORIGINAL_KEY + "\n", "new-key", null, 7]) {
      await expect(applyChange(project, change, key as unknown as string)).rejects.toThrow("original Graph request identity");
    }
    expect(invokeMock).not.toHaveBeenCalled();
    expect(generated).not.toHaveBeenCalled();
  });

  it("does not attach the retained Graph key to another editor's change", async () => {
    const project = structuredClone(fixtureBootstrap.project);
    const change: Change = { type: "rename_project", title: "Another editor" };
    await expect(applyChange(project, change, ORIGINAL_KEY)).rejects.toThrow("another editor change");
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it("retains the ordinary generated-key behavior for another editor", async () => {
    const project = structuredClone(fixtureBootstrap.project);
    const change: Change = { type: "rename_project", title: "Another editor" };
    vi.spyOn(crypto, "randomUUID").mockReturnValue(GENERATED_KEY);
    invokeMock.mockResolvedValueOnce({ token: GRANT }).mockResolvedValueOnce(project);
    await applyChange(project, change);
    expect(invokeMock).toHaveBeenNthCalledWith(1, "issue_effect_grant", { request: {
      effect: "project_edit", project_id: project.id, generation: project.generation,
      revision: project.revision, subject: GENERATED_KEY,
    } });
    expect(invokeMock).toHaveBeenNthCalledWith(2, "apply_change", { request: {
      project_id: project.id, generation: project.generation, revision: project.revision,
      request_id: GENERATED_KEY, effect_grant: GRANT, change,
    } });
  });

  it("never dispatches the Graph change when its original grant request is rejected", async () => {
    const { project, change } = source(false);
    const rejection = new Error("Grant was denied");
    invokeMock.mockRejectedValueOnce(rejection);
    await expect(applyChange(project, change, ORIGINAL_KEY)).rejects.toBe(rejection);
    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(invokeMock.mock.calls[0][0]).toBe("issue_effect_grant");
  });

  it("preserves an uncertain original rejection without a new key or automatic retry", async () => {
    const { project, change } = source(true);
    const generated = vi.spyOn(crypto, "randomUUID");
    const uncertain = { schema: "motionwright.graph-mutation-error/1", request_id: ORIGINAL_KEY,
      outcome_known: false, code: "graph_mutation_failed" };
    invokeMock.mockResolvedValueOnce({ token: GRANT }).mockRejectedValueOnce(uncertain);
    await expect(applyChange(project, change, ORIGINAL_KEY)).rejects.toBe(uncertain);
    expect(invokeMock).toHaveBeenCalledTimes(2);
    expect(generated).not.toHaveBeenCalled();
  });
});

const recoverySource = (change: SemanticGraphChange) => change.type === "upsert_semantic_graph" ? change.graph.authorship.source_snapshot : change.source_snapshot;
const pendingRecord = (change: SemanticGraphChange) => ({
  schema: "motionwright.semantic-graph-operation-recovery/1", request_id: ORIGINAL_KEY,
  source_snapshot: structuredClone(recoverySource(change)), record: { state: "pending" },
});
async function sanitizedRead(read: Promise<unknown>) {
  const result = await read.then(() => { throw new Error("Recovery unexpectedly resolved"); }, (reason: unknown) => reason);
  expect(result).toBeInstanceOf(Error); expect(Object.getPrototypeOf(result)).toBe(Error.prototype);
  expect((result as Error).message).toBe(GRAPH_RECOVERY_READ_ERROR);
  expect(result).not.toHaveProperty("schema"); expect(result).not.toHaveProperty("outcome_known"); expect(result).not.toHaveProperty("cause");
}

describe("original Graph recovery read wire", () => {
  it.each([false, true])("reads the exact original key and change once despite later caller edits (remove=%s)", async remove => {
    const { change } = source(remove), original = structuredClone(change), reply = pendingRecord(original);
    let resolve!: (value: unknown) => void;
    invokeMock.mockReturnValueOnce(new Promise<unknown>(done => { resolve = done; }));
    const generated = vi.spyOn(crypto, "randomUUID"), reading = readSemanticGraphOperationRecovery(ORIGINAL_KEY, change);
    recoverySource(change).revision++;
    if (change.type === "upsert_semantic_graph") change.graph.id = GENERATED_KEY; else change.graph_id = GENERATED_KEY;
    const calls = [["semantic_graph_operation_recovery", { request: { request_id: ORIGINAL_KEY, change: original } }]];
    expect(invokeMock.mock.calls).toEqual(calls); resolve(reply);
    await expect(reading).resolves.toEqual(reply); expect(invokeMock.mock.calls).toEqual(calls);
    expect(generated).not.toHaveBeenCalled();
  });
  it("keeps decoder identity independent of mutable transport arguments", async () => {
    const { change } = source(false), original = structuredClone(change), reply = pendingRecord(original);
    let resolve!: (value: unknown) => void;
    invokeMock.mockReturnValueOnce(new Promise<unknown>(done => { resolve = done; }));
    const generated = vi.spyOn(crypto, "randomUUID"), reading = readSemanticGraphOperationRecovery(ORIGINAL_KEY, change);
    const sent = invokeMock.mock.calls[0][1] as { request: { request_id: string; change: SemanticGraphChange } };
    sent.request.request_id = GENERATED_KEY; recoverySource(sent.request.change).revision++;
    resolve(reply); await expect(reading).resolves.toEqual(reply);
    expect(change).toEqual(original); expect(invokeMock).toHaveBeenCalledTimes(1); expect(generated).not.toHaveBeenCalled();
  });
  it("rejects malformed and nil keys before any native command or generated identity", async () => {
    const { change } = source(false), generated = vi.spyOn(crypto, "randomUUID");
    for (const id of ["", " " + ORIGINAL_KEY, ORIGINAL_KEY + "\n", "00000000-0000-0000-0000-000000000000", "replacement-key", null, undefined, 7])
      await sanitizedRead(readSemanticGraphOperationRecovery(id as string, change));
    expect(invokeMock).not.toHaveBeenCalled(); expect(generated).not.toHaveBeenCalled();
  });
  it.each([
    "PRIVATE_RECOVERY_SENTINEL /private/store.sqlite3",
    new Error("PRIVATE_RECOVERY_SENTINEL"),
    { schema: "motionwright.graph-mutation-error/1", request_id: ORIGINAL_KEY, outcome_known: true, code: "graph_mutation_failed" },
    { message: "PRIVATE_RECOVERY_SENTINEL", path: "/private/store.sqlite3" },
  ])("sanitizes a read rejection without interpreting original write knownness (%j)", async reason => {
    const { change } = source(false), original = structuredClone(change);
    invokeMock.mockRejectedValueOnce(reason); const generated = vi.spyOn(crypto, "randomUUID");
    await sanitizedRead(readSemanticGraphOperationRecovery(ORIGINAL_KEY, change));
    expect(invokeMock.mock.calls).toEqual([["semantic_graph_operation_recovery", { request: { request_id: ORIGINAL_KEY, change: original } }]]);
    expect(change).toEqual(original); expect(generated).not.toHaveBeenCalled();
  });
  it("sanitizes malformed fulfilled data and never retries it", async () => {
    const { change } = source(false); invokeMock.mockResolvedValueOnce({ ...pendingRecord(change), diagnostic: "PRIVATE_RECOVERY_SENTINEL" });
    const generated = vi.spyOn(crypto, "randomUUID");
    await sanitizedRead(readSemanticGraphOperationRecovery(ORIGINAL_KEY, change));
    expect(invokeMock).toHaveBeenCalledTimes(1); expect(generated).not.toHaveBeenCalled();
  });
  it("requires actual StudioService access without a fixture fallback", async () => {
    const { change } = source(false); vi.stubGlobal("window", {}); const generated = vi.spyOn(crypto, "randomUUID");
    await sanitizedRead(readSemanticGraphOperationRecovery(ORIGINAL_KEY, change));
    expect(invokeMock).not.toHaveBeenCalled(); expect(generated).not.toHaveBeenCalled();
  });
});
