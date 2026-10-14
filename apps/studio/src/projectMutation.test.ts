import { describe, expect, it, vi } from "vitest";
import { ProjectMutationGate, type ProjectRevision } from "./projectMutation";

const base = (): ProjectRevision => ({ id: "project-a", generation: "generation-a", revision: 12 });
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

describe("one source-bound Studio mutation path", () => {
  it("blocks a concurrent edit synchronously while an import awaits IPC", async () => {
    const gate = new ProjectMutationGate();
    let visible = base();
    const receipt = deferred<ProjectRevision>();
    const second = vi.fn(async () => ({ ...visible, revision: 14 }));
    const pending = vi.fn();
    const first = gate.run(visible, () => visible, () => receipt.promise, p => { visible = p; }, pending);
    await expect(gate.run(visible, () => visible, second, p => { visible = p; }, pending))
      .rejects.toThrow("Finish the current project change or import");
    expect(second).not.toHaveBeenCalled();
    expect(pending.mock.calls).toEqual([[true]]);
    receipt.resolve({ ...visible, revision: 13 });
    await first;
    expect(visible.revision).toBe(13);
    expect(pending.mock.calls).toEqual([[true], [false]]);
    await gate.run(visible, () => visible, second, p => { visible = p; });
    expect(visible.revision).toBe(14);
  });

  it.each([
    { ...base(), id: "project-b" },
    { ...base(), generation: "restored-generation" },
    { ...base(), revision: 14 },
  ])("never publishes a late result after a visible scope change: %j", async (replacement) => {
    const gate = new ProjectMutationGate();
    const original = base();
    let visible = original;
    const receipt = deferred<ProjectRevision>();
    const publish = vi.fn((p: ProjectRevision) => { visible = p; });
    const result = gate.run(original, () => visible, () => receipt.promise, publish);
    const assertion = expect(result).rejects.toThrow("active project changed");
    visible = replacement;
    receipt.resolve({ ...original, revision: 13 });
    await assertion;
    expect(publish).not.toHaveBeenCalled();
    expect(visible).toEqual(replacement);
  });

  it.each([
    { ...base(), id: "wrong-project", revision: 13 },
    { ...base(), generation: "wrong-generation", revision: 13 },
    { ...base(), revision: 11 },
    { ...base(), revision: Number.NaN },
  ])("refuses an unrelated or regressing backend response: %j", async (response) => {
    const publish = vi.fn();
    const gate = new ProjectMutationGate();
    await expect(gate.run(base(), base, async () => response, publish)).rejects.toThrow("response does not match");
    expect(publish).not.toHaveBeenCalled();
  });

  it("releases the latch after failure without retrying the request", async () => {
    const gate = new ProjectMutationGate();
    const pending = vi.fn();
    const failed = vi.fn(async () => { throw new Error("CAS conflict"); });
    await expect(gate.run(base(), base, failed, vi.fn(), pending)).rejects.toThrow("CAS conflict");
    expect(failed).toHaveBeenCalledTimes(1);
    expect(pending.mock.calls).toEqual([[true], [false]]);
    const publish = vi.fn();
    await gate.run(base(), base, async p => ({ ...p, revision: 13 }), publish);
    expect(publish).toHaveBeenCalledTimes(1);
  });

  it("refuses an already stale source before dispatch", async () => {
    const operation = vi.fn(async () => base());
    const gate = new ProjectMutationGate();
    await expect(gate.run(base(), () => ({ ...base(), revision: 14 }), operation, vi.fn()))
      .rejects.toThrow("before this request");
    expect(operation).not.toHaveBeenCalled();
  });

  it("serializes a portable replacement with ordinary edits and publishes only a new project", async () => {
    const gate = new ProjectMutationGate();
    let visible = base();
    const receipt = deferred<ProjectRevision>();
    const edit = vi.fn(async () => ({ ...visible, revision: 13 }));
    const pending = vi.fn();
    const importPromise = gate.run(visible, () => visible, () => receipt.promise,
      next => { visible = next; }, pending, "replace");
    await expect(gate.run(visible, () => visible, edit, vi.fn())).rejects.toThrow("Finish the current");
    expect(edit).not.toHaveBeenCalled();
    receipt.resolve({ id: "imported-project", generation: "fresh-generation", revision: 0 });
    await expect(importPromise).resolves.toEqual(visible);
    expect(visible.id).toBe("imported-project");
    expect(pending.mock.calls).toEqual([[true], [false]]);
    await gate.run(visible, () => visible, edit, next => { visible = next; });
    expect(visible.revision).toBe(13);
  });

  it.each([
    { ...base(), revision: 0 },
    { ...base(), id: "imported-project", generation: "generation-a", revision: 0 },
    { ...base(), id: "", generation: "new-generation", revision: 0 },
    { ...base(), id: "imported-project", generation: "", revision: 0 },
    { ...base(), id: "imported-project", generation: "new-generation", revision: -1 },
    { ...base(), id: "imported-project", generation: "new-generation", revision: NaN },
  ])("refuses a forged or non-new portable project identity: %j", async response => {
    const publish = vi.fn();
    await expect(new ProjectMutationGate().run(base(), base, async () => response,
      publish, undefined, "replace")).rejects.toThrow("expected project identity");
    expect(publish).not.toHaveBeenCalled();
  });

  it("preserves a switched view when a portable import returns late", async () => {
    const gate = new ProjectMutationGate();
    const original = base();
    let visible = original;
    const deferredImport = deferred<ProjectRevision>();
    const publish = vi.fn((next: ProjectRevision) => { visible = next; });
    const result = gate.run(original, () => visible, () => deferredImport.promise,
      publish, undefined, "replace");
    const assertion = expect(result).rejects.toThrow("active project changed");
    visible = { ...original, revision: 14 };
    deferredImport.resolve({ id: "new-id", generation: "new-generation", revision: 0 });
    await assertion;
    expect(publish).not.toHaveBeenCalled();
    expect(visible.revision).toBe(14);
  });
});
