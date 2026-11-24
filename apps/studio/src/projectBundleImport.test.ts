import { describe, expect, it, vi } from "vitest";
import {
  BUNDLE_IMPORT_PREFIX, checkedBundlePlan, discardBundleImportCopy, importBundleWithRetainedPlan,
  readBundleImports, recoverRetainedBundleImport,
} from "./projectBundleImport";
import type { PortableBundlePlan, Project } from "./types";

function memory() {
  const entries = new Map<string, string>();
  return { entries, get length() { return entries.size; }, key: (index: number) => [...entries.keys()][index] ?? null,
    getItem: (key: string) => entries.get(key) ?? null,
    setItem: (key: string, value: string) => { entries.set(key, value); },
    removeItem: (key: string) => { entries.delete(key); } };
}
const projectId = "11111111-1111-4111-8111-111111111111";
const sourceGeneration = "22222222-2222-4222-8222-222222222222";
const generation = "33333333-3333-4333-8333-333333333333";
const operationId = "44444444-4444-4444-8444-444444444444";
const plan: PortableBundlePlan = { project_id: projectId, title: "Original portable project",
  source_generation: sourceGeneration, revision: "7", event_count: 7, blob_count: 2,
  total_blob_bytes: "4096", rotates_generation: true, fingerprint: "a".repeat(64) };
const project = { id: projectId, generation, revision: 7, active_branch: operationId } as Project;
const committed = { operation_id: operationId, project_id: projectId, generation, fingerprint: plan.fingerprint };

describe("portable import continuity and readonly reconciliation", () => {
  it("retains the exact inspection before dispatch and removes it only after explicit admission", async () => {
    const storage = memory();
    const dispatch = vi.fn(async (path: string, fingerprint: string) => {
      const { copies } = readBundleImports(storage);
      expect(copies).toHaveLength(1); expect(copies[0].value?.plan).toEqual(plan);
      expect(path).toBe("/portable/original"); expect(fingerprint).toBe(plan.fingerprint);
      return project;
    });
    const result = await importBundleWithRetainedPlan(" /portable/original ", plan, dispatch, storage);
    expect(dispatch).toHaveBeenCalledTimes(1);
    expect(readBundleImports(storage).copies).toHaveLength(1);
    discardBundleImportCopy(result.copy, storage);
    expect(readBundleImports(storage).copies).toEqual([]);
  });

  it("a lost reply keeps one copy and only reads the original commit without another grant or import", async () => {
    const storage = memory(), dispatch = vi.fn(async () => { throw new Error("reply lost after commit"); });
    await expect(importBundleWithRetainedPlan("/portable/original", plan, dispatch, storage)).rejects.toThrow("reply lost");
    const copy = readBundleImports(storage).copies[0];
    const read = vi.fn(async () => null);
    expect(await recoverRetainedBundleImport(copy, read, storage)).toBeNull();
    expect(await recoverRetainedBundleImport(copy, read, storage)).toBeNull();
    await expect(importBundleWithRetainedPlan("/another", plan, dispatch, storage)).rejects.toThrow("retained portable import");
    expect(dispatch).toHaveBeenCalledTimes(1);
    expect(read).toHaveBeenCalledTimes(2);
    expect(read).toHaveBeenCalledWith("recover_project_bundle_import",
      { request: { project_id: projectId, fingerprint: plan.fingerprint } });
    const recover = vi.fn(async () => committed);
    expect(await recoverRetainedBundleImport(copy, recover, storage)).toEqual(committed);
    expect(recover).toHaveBeenCalledWith("recover_project_bundle_import",
      { request: { project_id: projectId, fingerprint: plan.fingerprint } });
    expect(storage.getItem(copy.key)).toBe(copy.raw);
  });

  it("storage failure prevents all import authority and dispatch", async () => {
    const storage = memory(), dispatch = vi.fn(async () => project);
    storage.setItem = () => { throw new Error("storage unavailable"); };
    await expect(importBundleWithRetainedPlan("/original", plan, dispatch, storage)).rejects.toThrow("storage unavailable");
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("a changed continuity copy cannot publish or erase a completed reply", async () => {
    const storage = memory();
    let key = "";
    await expect(importBundleWithRetainedPlan("/original", plan, async () => {
      key = storage.key(0)!; storage.setItem(key, "independent retained bytes"); return project;
    }, storage)).rejects.toThrow("changed in another window");
    expect(storage.getItem(key)).toBe("independent retained bytes");
  });

  it("foreign or malformed commit bindings never close an original import", async () => {
    const storage = memory();
    const { copy } = await importBundleWithRetainedPlan("/original", plan, async () => project, storage);
    for (const result of [
      { ...committed, project_id: operationId }, { ...committed, fingerprint: "b".repeat(64) },
      { ...committed, generation: sourceGeneration }, { ...committed, operation_id: "00000000-0000-0000-0000-000000000000" },
      { ...committed, authority: true },
    ]) {
      await expect(recoverRetainedBundleImport(copy, async () => result, storage)).rejects.toThrow("inconsistent");
    }
    expect(storage.getItem(copy.key)).toBe(copy.raw);
    storage.setItem(copy.key, "later bytes");
    expect(() => discardBundleImportCopy(copy, storage)).toThrow("changed");
    expect(storage.getItem(copy.key)).toBe("later bytes");
  });

  it("invalid inspections and unreadable retained copies cannot enter a fresh import", async () => {
    const storage = memory(), dispatch = vi.fn(async () => project);
    expect(() => checkedBundlePlan({ ...plan, revision: 7 })).toThrow("inspection");
    expect(() => checkedBundlePlan({ ...plan, rotates_generation: false })).toThrow("inspection");
    storage.setItem(BUNDLE_IMPORT_PREFIX + operationId, "{unreadable");
    await expect(importBundleWithRetainedPlan("/original", plan, dispatch, storage)).rejects.toThrow("retained portable import");
    expect(dispatch).not.toHaveBeenCalled();
    expect(readBundleImports(storage).copies[0].raw).toBe("{unreadable");
  });
});
