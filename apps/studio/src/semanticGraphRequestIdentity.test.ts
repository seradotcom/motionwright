import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyChange } from "./api";
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
