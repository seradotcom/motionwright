import { afterEach, describe, expect, it, vi } from "vitest";
import { fixtureProject } from "./fixture";
import {
  appendProjectLibraryItems,
  forgetProjectSelection,
  listProjectLibrary,
  openProjectFromLibrary,
  parseProjectLibraryPage,
  PROJECT_SELECTION_KEY,
  readProjectSelection,
  rememberOpenedProject,
  type ProjectLibraryInvoke,
  type ProjectLibraryPage,
  type ProjectLibrarySummary,
  type ProjectSelectionStorage,
  PROJECT_CREATION_PREFIX,
  createProjectWithRetainedIntent,
  discardProjectCreationCopy,
  readProjectCreations,
  recoverRetainedProjectCreation,
  type ProjectCreationIntent,
  type ProjectCreationInvoke,
} from "./projectLibrary";
import type { Project } from "./types";

// Transport/storage doubles below exercise UI contracts only. Real
// StudioService reads are covered by the desktop module's Rust tests.
const A = "abcdefab-1234-4567-89ab-abcdef123456";
const B = "abcdefab-1234-4567-89ab-abcdef123457";
const C = "abcdefab-1234-4567-89ab-abcdef123458";
const GEN = "abcdefab-1234-4567-89ab-abcdef123459";
const AT = "2026-10-17T06:00:00Z";

function project(id = A): Project {
  return { ...structuredClone(fixtureProject), id, generation: GEN, title: "Synthetic library UI fixture" };
}

function item(id = A, revision = "12"): ProjectLibrarySummary {
  return { id, generation: GEN, revision, title: `Synthetic ${id}`, updated_at: AT };
}

function page(items: ProjectLibrarySummary[] = [item()], truncated = false): ProjectLibraryPage {
  const last = items.at(-1);
  return { items, truncated, next_cursor: truncated && last ? { id: last.id, updated_at: last.updated_at } : null };
}

function saved(id: string): string {
  return JSON.stringify({ schema: "motionwright.project-selection/1", project_id: id });
}

class SelectionMemoryStorage implements ProjectSelectionStorage {
  readonly data = new Map<string, string>();
  writes = 0;
  removals = 0;
  failGet = false;
  failSet = false;
  failRemove = false;
  afterSet: (() => void) | null = null;
  get length() { return this.data.size; }
  key(index: number) { return [...this.data.keys()][index] ?? null; }
  getItem(key: string): string | null {
    if (this.failGet) throw new Error("Synthetic unavailable preference store");
    return this.data.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    if (this.failSet) throw new Error("Synthetic quota exceeded");
    this.writes++;
    this.data.set(key, value);
    this.afterSet?.();
  }
  removeItem(key: string): void {
    if (this.failRemove) throw new Error("Synthetic preference removal denied");
    this.removals++;
    this.data.delete(key);
  }
}

describe("retained project creation at the UI transport boundary", () => {
  function grant() {
    return { token: A, effect: "project_create", project_id: null, generation: null, revision: null,
      workspace_session: GEN, expires_in_seconds: 45, one_time: true };
  }
  function result(intent: ProjectCreationIntent) {
    return { request_id: intent.request_id, replayed: false,
      project: { ...project(C), title: intent.title, revision: 0 } };
  }

  it("retains the exact original intent before both IPCs and does not share mutable grant arguments", async () => {
    const storage = new SelectionMemoryStorage();
    const call = vi.fn<ProjectCreationInvoke>(async (command, args) => {
      const retained = readProjectCreations(storage).copies;
      expect(retained).toHaveLength(1);
      expect(retained[0].value!.phase).toBe("unconfirmed");
      expect(retained[0].value!.intent.title).toBe("  The original title  ");
      if (command === "issue_project_creation_grant") {
        expect(args.request).toEqual(retained[0].value!.intent);
        (args.request as ProjectCreationIntent).title = "Synthetic transport mutation";
        return grant();
      }
      expect(command).toBe("create_project");
      const sent = args.request as { intent: ProjectCreationIntent; effect_grant: string };
      expect(sent.intent).toEqual(retained[0].value!.intent); expect(sent.effect_grant).toBe(A);
      return result(sent.intent);
    });
    const created = await createProjectWithRetainedIntent("  The original title  ", call, storage);
    expect(call.mock.calls.map(([command]) => command)).toEqual(["issue_project_creation_grant", "create_project"]);
    expect(created.outcome.project.title).toBe("  The original title  ");
    expect(created.outcome.project.revision).toBe(0);
    expect(created.copy.value).toMatchObject({ phase: "observed", projectId: C });
    expect(created.warning).toBeNull();
  });

  it.each(["removed", "changed", "unreadable", "unchanged"] as const)(
    "rechecks the retained creation after the grant when its copy is %s",
    async change => {
      const storage = new SelectionMemoryStorage();
      let releaseGrant!: (value: unknown) => void;
      const pendingGrant = new Promise<unknown>(resolve => { releaseGrant = resolve; });
      const call = vi.fn<ProjectCreationInvoke>(async (command, args) => {
        if (command === "issue_project_creation_grant") return pendingGrant;
        expect(command).toBe("create_project");
        const sent = args.request as { intent: ProjectCreationIntent; effect_grant: string };
        return result(sent.intent);
      });
      const pending = createProjectWithRetainedIntent("Keep the original title", call, storage);
      const [copy] = readProjectCreations(storage).copies;
      expect(copy.value!.intent.title).toBe("Keep the original title");
      let changedRaw = copy.raw;
      if (change === "removed") storage.removeItem(copy.key);
      if (change === "changed") {
        changedRaw = JSON.stringify({ ...copy.value, intent: { ...copy.value!.intent, title: "Later retained title" } });
        storage.setItem(copy.key, changedRaw);
      }
      if (change === "unreadable") storage.failGet = true;
      // Attach the rejection observer before resolving the real async boundary.
      const observed = change === "unchanged"
        ? expect(pending).resolves.toMatchObject({ outcome: { project: { title: "Keep the original title", revision: 0 } } })
        : expect(pending).rejects.toThrow();
      releaseGrant(grant());
      await observed;
      expect(call.mock.calls.map(([command]) => command)).toEqual(change === "unchanged"
        ? ["issue_project_creation_grant", "create_project"] : ["issue_project_creation_grant"]);
      storage.failGet = false;
      if (change === "removed") expect(storage.getItem(copy.key)).toBeNull();
      if (change === "changed") expect(storage.getItem(copy.key)).toBe(changedRaw);
      if (change === "unreadable") expect(storage.getItem(copy.key)).toBe(copy.raw);
    },
  );

  it("refuses a byte-changed but semantically identical creation copy after the grant", async () => {
    const storage = new SelectionMemoryStorage();
    const waiting = deferred<ReturnType<typeof grant>>();
    const call = vi.fn<ProjectCreationInvoke>(async command => {
      if (command === "issue_project_creation_grant") return waiting.promise;
      throw new Error("A byte-changed original must never reach create_project");
    });
    const creation = createProjectWithRetainedIntent("Keep exact original bytes", call, storage);
    const original = readProjectCreations(storage).copies[0];
    const altered = original.raw + " ";
    expect(JSON.parse(altered)).toEqual(JSON.parse(original.raw));
    storage.data.set(original.key, altered);
    const rejection = expect(creation).rejects.toThrow("retained creation changed");
    waiting.resolve(grant());
    await rejection;
    expect(call.mock.calls.map(([command]) => command)).toEqual(["issue_project_creation_grant"]);
    expect(storage.getItem(original.key)).toBe(altered);
    expect(storage.writes).toBe(1);
  });

  it("does not dispatch when its continuity write is unavailable or an earlier record is corrupt", async () => {
    const storage = new SelectionMemoryStorage(), call = vi.fn<ProjectCreationInvoke>();
    storage.failSet = true;
    await expect(createProjectWithRetainedIntent("Keep this title", call, storage)).rejects.toThrow();
    expect(call).not.toHaveBeenCalled(); expect(storage.data.size).toBe(0);
    storage.failSet = false;
    const key = PROJECT_CREATION_PREFIX + A; storage.setItem(key, "{original-corrupt-request");
    await expect(createProjectWithRetainedIntent("Keep this title", call, storage)).rejects.toThrow("Review the retained");
    expect(call).not.toHaveBeenCalled(); expect(storage.getItem(key)).toBe("{original-corrupt-request");
    expect(readProjectCreations(storage).copies[0].value).toBeNull();
  });

  it("keeps a lost creation response under one UUID and recovery None never dispatches a new grant or creation", async () => {
    const storage = new SelectionMemoryStorage();
    let original: ProjectCreationIntent | undefined;
    const call = vi.fn<ProjectCreationInvoke>(async (command, args) => {
      if (command === "issue_project_creation_grant") { original = structuredClone(args.request as ProjectCreationIntent); return grant(); }
      if (command === "create_project") throw new Error("Synthetic lost response after dispatch");
      expect(command).toBe("recover_project_creation"); expect(args.request).toEqual(original); return null;
    });
    await expect(createProjectWithRetainedIntent("A retained creation", call, storage)).rejects.toThrow("lost response");
    const copy = readProjectCreations(storage).copies[0];
    expect(copy.value!.intent).toEqual(original);
    for (let pass = 0; pass < 2; pass++) {
      const recovered = await recoverRetainedProjectCreation(copy, call, storage);
      expect(recovered.outcome).toBeNull(); expect(recovered.warning).toContain("does not authorize");
      expect(storage.getItem(copy.key)).toBe(copy.raw);
    }
    await expect(createProjectWithRetainedIntent("A retained creation", call, storage)).rejects.toThrow("Review the retained");
    expect(call.mock.calls.map(([command]) => command)).toEqual([
      "issue_project_creation_grant", "create_project", "recover_project_creation", "recover_project_creation",
    ]);
    expect([...storage.data.keys()]).toEqual([PROJECT_CREATION_PREFIX + original!.request_id]);
  });

  it("recovery uses its readonly command and keeps the original r0 distinct from a later current document", async () => {
    const storage = new SelectionMemoryStorage();
    const fail = vi.fn<ProjectCreationInvoke>(async command => {
      if (command === "issue_project_creation_grant") return grant();
      throw new Error("Synthetic unknown response");
    });
    await expect(createProjectWithRetainedIntent("Original project", fail, storage)).rejects.toThrow();
    const copy = readProjectCreations(storage).copies[0];
    const original = result(copy.value!.intent);
    const recovery = vi.fn<ProjectCreationInvoke>(async () => ({ ...original, replayed: true }));
    const recovered = await recoverRetainedProjectCreation(copy, recovery, storage);
    expect(recovery).toHaveBeenCalledExactlyOnceWith("recover_project_creation", { request: copy.value!.intent });
    expect(recovered.outcome!.project.revision).toBe(0);
    const current = { ...original.project, title: "Later edited title", revision: 8 };
    const open = vi.fn<ProjectLibraryInvoke>(async () => current);
    expect(await openProjectFromLibrary(recovered.outcome!.project.id, open)).toEqual(current);
    expect(recovered.outcome!.project).toEqual(original.project);
    discardProjectCreationCopy(recovered.copy, storage);
    expect(storage.length).toBe(0);
  });

  it.each(["title", "revision", "identity"])("rejects a mismatched original %s while retaining the request", async kind => {
    const storage = new SelectionMemoryStorage();
    const call = vi.fn<ProjectCreationInvoke>(async (command, args) => {
      if (command === "issue_project_creation_grant") return grant();
      const original = result((args.request as { intent: ProjectCreationIntent }).intent);
      if (kind === "title") original.project.title = original.project.title.trim();
      if (kind === "revision") original.project.revision = 1;
      if (kind === "identity") original.request_id = B;
      return original;
    });
    await expect(createProjectWithRetainedIntent("  Exact title  ", call, storage)).rejects.toThrow();
    expect(readProjectCreations(storage).copies[0].value!.phase).toBe("unconfirmed");
    expect(call.mock.calls).toHaveLength(2);
  });

  it("refuses a changed local copy before recovery or removal", async () => {
    const storage = new SelectionMemoryStorage();
    const call = vi.fn<ProjectCreationInvoke>(async command => {
      if (command === "issue_project_creation_grant") return grant();
      throw new Error("Synthetic unknown result");
    });
    await expect(createProjectWithRetainedIntent("Keep the original", call, storage)).rejects.toThrow();
    const copy = readProjectCreations(storage).copies[0];
    storage.setItem(copy.key, copy.raw + " "); const read = vi.fn<ProjectCreationInvoke>();
    await expect(recoverRetainedProjectCreation(copy, read, storage)).rejects.toThrow("changed");
    expect(() => discardProjectCreationCopy(copy, storage)).toThrow("changed");
    expect(read).not.toHaveBeenCalled(); expect(storage.getItem(copy.key)).toBe(copy.raw + " ");
  });

  it("retains a known successful result when local receipt update fails", async () => {
    const storage = new SelectionMemoryStorage();
    const call = vi.fn<ProjectCreationInvoke>(async (command, args) => {
      if (command === "issue_project_creation_grant") return grant();
      storage.failSet = true;
      return result((args.request as { intent: ProjectCreationIntent }).intent);
    });
    const created = await createProjectWithRetainedIntent("Known created project", call, storage);
    expect(created.outcome.project.title).toBe("Known created project");
    expect(created.warning).toContain("Creation was observed");
    expect(readProjectCreations(storage).copies[0].value!.phase).toBe("unconfirmed");
  });
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

afterEach(() => vi.unstubAllGlobals());

describe("project library read wire", () => {
  it("sends the actual list/open command payloads and leaves the loaded project unchanged", async () => {
    const source = project();
    const before = structuredClone(source);
    const call = vi.fn<ProjectLibraryInvoke>(async command => command === "list_projects" ? page() : source);
    expect(await listProjectLibrary({}, call)).toEqual(page());
    expect(call).toHaveBeenNthCalledWith(1, "list_projects", { request: { cursor: null, limit: 20 } });
    expect(await openProjectFromLibrary(A.toUpperCase(), call)).toEqual(source);
    expect(call).toHaveBeenNthCalledWith(2, "open_project", { request: { project_id: A } });
    expect(source).toEqual(before);
    expect(call.mock.calls.every(([command]) => ["list_projects", "open_project"].includes(command))).toBe(true);
  });

  it("preserves a paginated cursor and never treats a partial page as complete", async () => {
    const after = { id: A, updated_at: AT };
    const result = page([item(B)], true);
    const call = vi.fn<ProjectLibraryInvoke>(async () => result);
    expect(await listProjectLibrary({ cursor: after, limit: 1 }, call)).toEqual(result);
    expect(call).toHaveBeenCalledWith("list_projects", { request: { cursor: after, limit: 1 } });
  });

  it.each([0, 101, -1, 1.5, NaN, Infinity, null])("rejects invalid page size %s before invoking", async limit => {
    const call = vi.fn<ProjectLibraryInvoke>();
    await expect(listProjectLibrary({ limit: limit as number }, call)).rejects.toThrow("between 1 and 100");
    expect(call).not.toHaveBeenCalled();
  });

  it("rejects malformed cursor, repeated continuation and oversized response", async () => {
    const call = vi.fn<ProjectLibraryInvoke>(async () => page([item()], true));
    await expect(listProjectLibrary({ cursor: { id: A, updated_at: "\u0085" } }, call)).rejects.toThrow("cursor");
    expect(call).not.toHaveBeenCalled();
    await expect(listProjectLibrary({ cursor: { id: A, updated_at: AT } }, call)).rejects.toThrow("did not advance");
    const oversized = vi.fn<ProjectLibraryInvoke>(async () => page([item(A), item(B)]));
    await expect(listProjectLibrary({ limit: 1 }, oversized)).rejects.toThrow("did not advance");
  });

  it("keeps u64 revisions exact and rejects lossy/coerced values", () => {
    expect(parseProjectLibraryPage(page([item(A, "18446744073709551615")])).items[0].revision).toBe("18446744073709551615");
    for (const revision of ["18446744073709551616", "01", "-1", "1.0", "1e3", 12]) {
      expect(() => parseProjectLibraryPage(page([{ ...item(), revision: revision as string }]))).toThrow("invalid entry");
    }
  });

  it("rejects duplicate UUID aliases and contradictory pagination metadata", () => {
    expect(() => parseProjectLibraryPage(page([item(), item(A.toUpperCase())]))).toThrow("duplicate project identity");
    for (const result of [
      { ...page(), truncated: true },
      { ...page([item()], true), truncated: false },
      { items: [], truncated: true, next_cursor: { id: A, updated_at: AT } },
      { ...page([item()], true), next_cursor: { id: B, updated_at: AT } },
    ]) expect(() => parseProjectLibraryPage(result)).toThrow("continuation");
    expect(() => parseProjectLibraryPage({ ...page(), repaired: true })).toThrow("invalid page");
  });

  it("propagates genuine service errors without substituting a project or index", async () => {
    const call = vi.fn<ProjectLibraryInvoke>(async () => { throw new Error("Stored document is corrupt"); });
    await expect(openProjectFromLibrary(A, call)).rejects.toThrow("Stored document is corrupt");
    await expect(listProjectLibrary({}, call)).rejects.toThrow("Stored document is corrupt");
    expect(call).toHaveBeenCalledTimes(2);
  });

  it("rejects a foreign loaded identity and an unsafe canonical revision", async () => {
    const foreign = vi.fn<ProjectLibraryInvoke>(async () => project(B));
    await expect(openProjectFromLibrary(A, foreign)).rejects.toThrow("differs from the requested identity");
    const unsafe = vi.fn<ProjectLibraryInvoke>(async () => ({ ...project(), revision: Number.MAX_SAFE_INTEGER + 1 }));
    await expect(openProjectFromLibrary(A, unsafe)).rejects.toThrow("unsupported revision envelope");
    const incomplete = vi.fn<ProjectLibraryInvoke>(async () => ({ id: A, generation: GEN, active_branch: C, revision: 1 }));
    await expect(openProjectFromLibrary(A, incomplete)).rejects.toThrow("revision envelope");
  });

  it("deduplicates index observations across pages without mutating previous rows", () => {
    const previous = [item(A, "1"), item(B, "2")];
    const before = structuredClone(previous);
    const merged = appendProjectLibraryItems(previous, [item(B, "3"), item(C, "4")]);
    expect(merged.map(row => [row.id, row.revision])).toEqual([[A, "1"], [B, "3"], [C, "4"]]);
    expect(previous).toEqual(before);
  });
});

describe("remembered project is a checked view preference", () => {
  it("does not write while reading, requesting or resolving an open; remembrance is explicit", async () => {
    const storage = new SelectionMemoryStorage();
    vi.stubGlobal("window", { localStorage: storage });
    const before = readProjectSelection();
    const pending = deferred<Project>();
    const call = vi.fn<ProjectLibraryInvoke>(() => pending.promise);
    const opening = openProjectFromLibrary(A, call);
    expect(storage.writes).toBe(0);
    pending.resolve(project());
    const opened = await opening;
    expect(storage.writes).toBe(0);
    expect(rememberOpenedProject(opened, before).status).toBe("saved");
    expect(JSON.parse(storage.getItem(PROJECT_SELECTION_KEY)!)).toEqual({
      schema: "motionwright.project-selection/1", project_id: A,
    });
    expect(readProjectSelection()).toEqual({ status: "remembered", raw: saved(A), projectId: A });
  });

  it("remembers A/B and recovers only the last UUID through a new storage view", () => {
    const storage = new SelectionMemoryStorage();
    expect(rememberOpenedProject(project(A), readProjectSelection(storage), storage).status).toBe("saved");
    expect(rememberOpenedProject(project(B), readProjectSelection(storage), storage).status).toBe("saved");
    const anotherView: ProjectSelectionStorage = {
      getItem: key => storage.getItem(key), setItem: (key, value) => storage.setItem(key, value), removeItem: key => storage.removeItem(key),
    };
    expect(readProjectSelection(anotherView)).toMatchObject({ status: "remembered", projectId: B });
    expect(storage.data.size).toBe(1);
    expect(JSON.parse(storage.getItem(PROJECT_SELECTION_KEY)!)).not.toHaveProperty("generation");
    expect(JSON.parse(storage.getItem(PROJECT_SELECTION_KEY)!)).not.toHaveProperty("effect_grant");
  });

  it("an older open response does not replace the selection remembered during its await", async () => {
    const storage = new SelectionMemoryStorage();
    const beforeSlow = readProjectSelection(storage);
    const slow = deferred<Project>();
    const opening = openProjectFromLibrary(A, async () => slow.promise);
    const b = await openProjectFromLibrary(B, async () => project(B));
    expect(rememberOpenedProject(b, readProjectSelection(storage), storage).status).toBe("saved");
    slow.resolve(project(A));
    const a = await opening;
    expect(rememberOpenedProject(a, beforeSlow, storage).status).toBe("conflict");
    expect(storage.getItem(PROJECT_SELECTION_KEY)).toBe(saved(B));
    expect(storage.writes).toBe(1);
  });

  it("does not rewrite a matching UUID already remembered by another document", () => {
    const storage = new SelectionMemoryStorage();
    const oldObservation = readProjectSelection(storage);
    rememberOpenedProject(project(A), oldObservation, storage);
    expect(rememberOpenedProject(project(A), oldObservation, storage).status).toBe("saved");
    expect(storage.writes).toBe(1);
  });

  it("preserves all invalid preference bytes until an explicit checked forget", () => {
    for (const raw of [
      "{broken", "null", "[]", "x".repeat(1_025),
      JSON.stringify({ schema: "motionwright.project-selection/2", project_id: A }),
      JSON.stringify({ schema: "motionwright.project-selection/1", project_id: "bad" }),
      JSON.stringify({ schema: "motionwright.project-selection/1", project_id: A, effect_grant: "forged" }),
    ]) {
      const storage = new SelectionMemoryStorage();
      storage.data.set(PROJECT_SELECTION_KEY, raw);
      storage.data.set("theme", "dark");
      const observed = readProjectSelection(storage);
      expect(observed).toMatchObject({ status: "invalid", raw });
      expect(rememberOpenedProject(project(), observed, storage).status).toBe("blocked");
      expect(storage.getItem(PROJECT_SELECTION_KEY)).toBe(raw);
      expect(storage.writes + storage.removals).toBe(0);
      expect(forgetProjectSelection(observed, storage).status).toBe("cleared");
      expect(storage.getItem(PROJECT_SELECTION_KEY)).toBe(null);
      expect(storage.getItem("theme")).toBe("dark");
    }
  });

  it("retains the old preference on quota failure without mutating the opened project", () => {
    const storage = new SelectionMemoryStorage();
    storage.data.set(PROJECT_SELECTION_KEY, saved(A));
    const before = readProjectSelection(storage);
    const opened = project(B);
    const projectBefore = structuredClone(opened);
    storage.failSet = true;
    expect(rememberOpenedProject(opened, before, storage).status).toBe("unavailable");
    expect(storage.getItem(PROJECT_SELECTION_KEY)).toBe(saved(A));
    expect(opened).toEqual(projectBefore);
  });

  it("reports inaccessible storage and readback failures without inventing a saved selection", () => {
    expect(readProjectSelection(null).status).toBe("unavailable");
    vi.stubGlobal("window", { get localStorage() { throw new Error("Synthetic access denied"); } });
    expect(readProjectSelection().status).toBe("unavailable");
    const storage = new SelectionMemoryStorage();
    const before = readProjectSelection(storage);
    storage.afterSet = () => { storage.failGet = true; };
    expect(rememberOpenedProject(project(), before, storage).status).toBe("unavailable");
    expect(storage.data.get(PROJECT_SELECTION_KEY)).toBe(saved(A));
  });

  it("reports an intervening preference write observed on readback and does not retry it", () => {
    const storage = new SelectionMemoryStorage();
    storage.afterSet = () => { storage.data.set(PROJECT_SELECTION_KEY, saved(B)); };
    const result = rememberOpenedProject(project(A), readProjectSelection(storage), storage);
    expect(result).toMatchObject({ status: "conflict", observed: { projectId: B } });
    expect(storage.writes).toBe(1);
    expect(storage.getItem(PROJECT_SELECTION_KEY)).toBe(saved(B));
  });

  it("does not forget a newer selection and reports deletion errors", () => {
    const storage = new SelectionMemoryStorage();
    storage.data.set(PROJECT_SELECTION_KEY, saved(A));
    const before = readProjectSelection(storage);
    storage.data.set(PROJECT_SELECTION_KEY, saved(B));
    expect(forgetProjectSelection(before, storage).status).toBe("conflict");
    expect(storage.removals).toBe(0);
    storage.failRemove = true;
    expect(forgetProjectSelection(readProjectSelection(storage), storage).status).toBe("unavailable");
    expect(storage.getItem(PROJECT_SELECTION_KEY)).toBe(saved(B));
  });
});
