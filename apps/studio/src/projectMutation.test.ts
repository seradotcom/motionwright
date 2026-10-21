import { afterEach, describe, expect, it, vi } from "vitest";
import { bootstrap } from "./api";
import { fixtureBootstrap, fixtureProject } from "./fixture";
import { ProjectMutationGate, ProjectReadSupersededError, type ProjectRevision, type ProjectViewRevision } from "./projectMutation";

const base = (): ProjectRevision => ({ id: "project-a", generation: "generation-a", revision: 12, active_branch: "branch-a" });
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
    { ...base(), active_branch: "branch-b" },
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

  it("refuses a callback from another branch even if id, generation and revision still match", async () => {
    const source = base(), current = { ...source, active_branch: "another-branch" };
    const operation = vi.fn(async () => ({ ...source, revision: source.revision + 1 }));
    const publish = vi.fn(), pending = vi.fn();
    await expect(new ProjectMutationGate().run(source, () => current, operation, publish, pending))
      .rejects.toThrow("before this request");
    expect(operation).not.toHaveBeenCalled();
    expect(publish).not.toHaveBeenCalled();
    expect(pending).not.toHaveBeenCalled();
  });

  it("allows a canonical branch checkout and uses that observed branch for the following edit", async () => {
    const gate = new ProjectMutationGate();
    let visible = base();
    const checkout = { ...visible, active_branch: "branch-b", revision: visible.revision + 1 };
    await expect(gate.run(visible, () => visible, async () => checkout, next => { visible = next; }))
      .resolves.toEqual(checkout);
    const edit = vi.fn(async (source: ProjectRevision) => ({ ...source, revision: source.revision + 1 }));
    await gate.run(visible, () => visible, edit, next => { visible = next; });
    expect(edit).toHaveBeenCalledExactlyOnceWith(checkout);
    expect(visible).toEqual({ ...checkout, revision: checkout.revision + 1 });
  });

  it("serializes a portable replacement with ordinary edits and publishes only a new project", async () => {
    const gate = new ProjectMutationGate();
    let visible = base();
    const receipt = deferred<ProjectRevision>();
    const edit = vi.fn(async () => ({ ...visible, revision: visible.revision + 1 }));
    const pending = vi.fn();
    const importPromise = gate.run(visible, () => visible, () => receipt.promise,
      next => { visible = next; }, pending, "replace");
    await expect(gate.run(visible, () => visible, edit, vi.fn())).rejects.toThrow("Finish the current");
    expect(edit).not.toHaveBeenCalled();
    const imported = { id: "imported-project", generation: "fresh-generation", revision: 0, active_branch: "imported-branch" };
    receipt.resolve(imported);
    await expect(importPromise).resolves.toEqual(imported);
    expect(visible).toEqual(imported);
    expect(pending.mock.calls).toEqual([[true], [false]]);
    await gate.run(visible, () => visible, edit, next => { visible = next; });
    expect(visible.revision).toBe(1);
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
    deferredImport.resolve({ id: "new-id", generation: "new-generation", revision: 0, active_branch: "new-branch" });
    await assertion;
    expect(publish).not.toHaveBeenCalled();
    expect(visible.revision).toBe(14);
  });
});

// Presentation coordination only. These controlled promises and transport
// responses do not claim Store, grant, native-renderer, or installer coverage.
describe("explicit library read admission", () => {
  function open(gate: ProjectMutationGate, source: ProjectRevision | null,
    current: () => ProjectRevision | null, id: string, result: Promise<ProjectRevision>,
    publish: (project: ProjectRevision) => void, pending?: (busy: boolean) => void) {
    return gate.read({source,current,requestedProjectId:id,load:()=>result,
      project:project=>project,publish,pending});
  }

  it.each(["older resolves first", "newer resolves first"])("keeps the latest of two explicit opens: %s", async order => {
    const gate = new ProjectMutationGate();
    let visible: ProjectRevision | null = null;
    const a = deferred<ProjectRevision>(), b = deferred<ProjectRevision>();
    const pending = vi.fn(), publish = vi.fn((p: ProjectRevision) => { visible=p; });
    const first = open(gate,null,()=>visible,"a",a.promise,publish,pending);
    const rejected = expect(first).rejects.toBeInstanceOf(ProjectReadSupersededError);
    const second = open(gate,null,()=>visible,"b",b.promise,publish,pending);
    if (order === "older resolves first") {
      a.resolve({...base(),id:"a"}); await rejected;
      expect(visible).toBeNull();
      expect(pending.mock.calls).toEqual([[true],[true]]);
    }
    b.resolve({...base(),id:"b"}); await second;
    if (order === "newer resolves first") { a.resolve({...base(),id:"a"}); await rejected; }
    expect(visible).toEqual({...base(),id:"b"});
    expect(publish).toHaveBeenCalledTimes(1);
    expect(pending.mock.calls).toEqual([[true],[true],[false]]);
  });

  it("suppresses an old rejection after a newer unselected shell was accepted", async () => {
    const gate = new ProjectMutationGate();
    const firstReply = deferred<ProjectRevision>();
    const old = open(gate,null,()=>null,"a",firstReply.promise,vi.fn());
    const rejected = expect(old).rejects.toBeInstanceOf(ProjectReadSupersededError);
    const shell = vi.fn();
    await gate.read<ProjectRevision,null>({source:null,current:()=>null,requestedProjectId:null,
      load:async()=>null,project:()=>null,publish:shell});
    firstReply.reject(new Error("Old corrupt-project response"));
    await rejected;
    expect(shell).toHaveBeenCalledOnce();
  });

  it("allows an explicit same UUID reopen after generation rotation", async () => {
    const gate = new ProjectMutationGate();
    let visible=base();
    const restored={...visible,generation:"new-restored-generation",revision:0,active_branch:"restored-branch"};
    await open(gate,visible,()=>visible,visible.id,Promise.resolve(restored),next=>{visible=next;});
    expect(visible).toEqual(restored);
    await gate.run(visible,()=>visible,async p=>({...p,revision:1}),next=>{visible=next;});
    expect(visible).toEqual({...restored,revision:1});
  });

  it("a corrupt-project open error retains the current view, releases busy, and never loads a fallback", async () => {
    const gate = new ProjectMutationGate();
    let visible=base();
    const before={...visible}, load=vi.fn(async()=>{throw new Error("Stored selected document is corrupt");});
    const publish=vi.fn((p:ProjectRevision)=>{visible=p;});
    const pending=vi.fn();
    await expect(gate.read({source:visible,current:()=>visible,requestedProjectId:"b",load,
      project:(p:ProjectRevision)=>p,publish,pending})).rejects.toThrow("is corrupt");
    expect(load).toHaveBeenCalledOnce();expect(publish).not.toHaveBeenCalled();expect(visible).toEqual(before);
    expect(pending.mock.calls).toEqual([[true],[false]]);
    await gate.run(visible,()=>visible,async p=>({...p,revision:13}),p=>{visible=p;});
    expect(visible.revision).toBe(13);
  });

  it("serializes opens against an edit or import in either order before dispatch", async () => {
    const gate = new ProjectMutationGate();let visible=base();
    const pendingRead=deferred<ProjectRevision>();
    const reading=open(gate,visible,()=>visible,"b",pendingRead.promise,p=>{visible=p;});
    const edit=vi.fn(async(p:ProjectRevision)=>({...p,revision:p.revision+1}));
    await expect(gate.run(visible,()=>visible,edit,vi.fn())).rejects.toThrow("Finish the current");
    expect(edit).not.toHaveBeenCalled();
    pendingRead.resolve({...base(),id:"b"});await reading;
    const pendingEdit=deferred<ProjectRevision>();
    const editing=gate.run(visible,()=>visible,()=>pendingEdit.promise,p=>{visible=p;});
    const read=vi.fn(async()=>base());
    await expect(gate.read({source:visible,current:()=>visible,requestedProjectId:"a",load:read,
      project:p=>p,publish:vi.fn()})).rejects.toThrow("Finish the current");
    expect(read).not.toHaveBeenCalled();
    pendingEdit.resolve({...visible,revision:visible.revision+1});await editing;
  });

  it.each([
    {...base(),id:"wrong"}, {...base(),revision:11}, {...base(),revision:Number.NaN},
  ])("refuses an unrelated or regressing read result without publishing: %j", async response => {
    const gate=new ProjectMutationGate(), publish=vi.fn();
    await expect(open(gate,base(),base,base().id,Promise.resolve(response),publish)).rejects.toThrow("does not match");
    expect(publish).not.toHaveBeenCalled();
  });

  it("never publishes a selected project for an explicitly unselected shell", async () => {
    const publish=vi.fn();
    await expect(new ProjectMutationGate().read({source:null,current:()=>null,requestedProjectId:null,
      load:async()=>base(),project:p=>p,publish})).rejects.toThrow("No fallback was accepted");
    expect(publish).not.toHaveBeenCalled();
  });

  it("preserves a scope changed outside the read coordinator while a response awaited", async () => {
    const gate=new ProjectMutationGate();let visible=base();const reply=deferred<ProjectRevision>(), publish=vi.fn();
    const reading=open(gate,visible,()=>visible,"b",reply.promise,publish);
    const refused=expect(reading).rejects.toThrow("active project changed while opening");
    visible={...visible,active_branch:"new-branch"};reply.resolve({...base(),id:"b"});await refused;
    expect(publish).not.toHaveBeenCalled();expect(visible.active_branch).toBe("new-branch");
  });
});

describe("new project admission uses the existing mutation coordinator", () => {
  it("serializes creation from an empty library against another create and an open before dispatch", async () => {
    const gate = new ProjectMutationGate(), reply = deferred<ProjectRevision>();
    let visible: ProjectRevision | null = null;
    const publish = vi.fn((next: ProjectRevision) => { visible = next; });
    const pending = vi.fn();
    const first = gate.create(null, () => visible, () => reply.promise, publish, pending);
    expect(pending).toHaveBeenLastCalledWith(true);
    const second = vi.fn(async () => base());
    await expect(gate.create(null, () => visible, second, publish)).rejects.toThrow("Finish the current");
    const read = vi.fn(async () => base());
    await expect(gate.read({ source: null, current: () => visible, requestedProjectId: base().id,
      load: read, project: value => value, publish })).rejects.toThrow("Finish the current");
    expect(second).not.toHaveBeenCalled(); expect(read).not.toHaveBeenCalled();
    reply.resolve({ ...base(), revision: 0 }); await first;
    expect(visible).toEqual({ ...base(), revision: 0 }); expect(publish).toHaveBeenCalledOnce();
    expect(pending.mock.calls).toEqual([[true], [false]]);
  });

  it("never replaces a newer visible project when creation returns late", async () => {
    const gate = new ProjectMutationGate(), reply = deferred<ProjectRevision>();
    let visible = base(); const publish = vi.fn(); const pending = vi.fn();
    const first = gate.create(visible, () => visible, () => reply.promise, publish, pending);
    const rejected = expect(first).rejects.toThrow("active project changed during creation");
    visible = { ...visible, revision: visible.revision + 1 };
    reply.resolve({ ...base(), id: "created", generation: "created-generation", revision: 0 });
    await rejected; expect(publish).not.toHaveBeenCalled(); expect(visible.revision).toBe(base().revision + 1);
    expect(pending.mock.calls).toEqual([[true], [false]]);
  });

  it("releases admission after an unknown result without replaying the operation", async () => {
    const gate = new ProjectMutationGate(); let visible = base();
    const create = vi.fn(async () => { throw new Error("Synthetic unknown result"); });
    const publish = vi.fn();
    await expect(gate.create(visible, () => visible, create, publish)).rejects.toThrow("unknown result");
    expect(create).toHaveBeenCalledOnce(); expect(publish).not.toHaveBeenCalled();
    const saved = { ...visible, id: "recovered", generation: "recovered-generation", revision: 4 };
    await gate.read({ source: visible, current: () => visible, requestedProjectId: saved.id,
      load: async () => saved, project: value => value, publish: next => { visible = next; } });
    expect(visible).toEqual(saved); expect(create).toHaveBeenCalledOnce();
  });

  it.each([
    { ...base(), revision: 0 },
    { ...base(), id: "created", generation: "created-generation", revision: Number.NaN },
    { ...base(), id: "created", generation: "", revision: 0 },
  ])("refuses an invalid new-document result without changing the original: %j", async result => {
    const source = base(), publish = vi.fn();
    await expect(new ProjectMutationGate().create(source, () => source, async () => result, publish)).rejects.toThrow("expected new document");
    expect(publish).not.toHaveBeenCalled();
  });
});

describe("synthetic desktop bootstrap transport contract", () => {
  afterEach(() => vi.unstubAllGlobals());
  function transport(response: unknown) {
    const invoke=vi.fn(async(_command: string, _args?: unknown, _options?: unknown)=>response);
    vi.stubGlobal("window",{__TAURI_INTERNALS__:{invoke}});
    return invoke;
  }
  const native_sdk={...fixtureBootstrap.native_sdk,mode:"tauri" as const};

  it("requests an explicitly unselected shell without manufacturing a Project", async () => {
    const reply={project:null,project_selection:{status:"unselected"},native_sdk};
    const invoke=transport(reply);
    expect(await bootstrap(null)).toEqual(reply);
    expect(invoke).toHaveBeenCalledExactlyOnceWith("bootstrap",{request:{project_id:null}},undefined);
  });

  it("returns unavailable for exactly the requested corrupt identity, without selecting or retrying another", async () => {
    const reply={project:null,project_selection:{status:"unavailable",project_id:fixtureProject.id,message:"Synthetic unreadable document"},native_sdk};
    const invoke=transport(reply);
    expect(await bootstrap(fixtureProject.id)).toEqual(reply);
    expect(invoke).toHaveBeenCalledOnce();
    expect(invoke.mock.calls[0][1]).toEqual({request:{project_id:fixtureProject.id}});
  });

  it("refuses implicit latest selection, legacy missing status, and a foreign returned project", async () => {
    transport({project:fixtureProject,project_selection:{status:"selected",project_id:fixtureProject.id},native_sdk});
    await expect(bootstrap(null)).rejects.toThrow("without an explicit identity");
    transport({project:fixtureProject,native_sdk});
    await expect(bootstrap(fixtureProject.id)).rejects.toThrow("invalid project startup");
    transport({project:{...fixtureProject,id:"foreign"},project_selection:{status:"selected",project_id:fixtureProject.id},native_sdk});
    await expect(bootstrap(fixtureProject.id)).rejects.toThrow("explicitly requested project");
  });

  it("carries the newly observed generation for the explicitly reopened identity", async () => {
    const project={...fixtureProject,generation:"abcdefab-1234-4567-89ab-abcdef123459",revision:0};
    const reply={project,project_selection:{status:"selected",project_id:project.id},native_sdk};
    transport(reply);
    expect((await bootstrap(project.id)).project).toEqual(project);
  });
});


describe("bounded observations share the existing project actor", () => {
  const id = "f1000000-0000-4000-8000-000000000001";
  const generation = "f1000000-0000-4000-8000-000000000002";
  const projected = (revision = "18446744073709551615"): ProjectViewRevision =>
    ({ kind: "projection", identity: { project_id: id, generation, revision } });
  const observedRead = (gate: ProjectMutationGate, source: ProjectViewRevision | null,
    current: () => ProjectViewRevision | null, result: Promise<ProjectViewRevision>,
    publish = vi.fn(), pending = vi.fn()) => gate.readView({
      source, current, requestedProjectId: id, load: () => result, view: value => value, publish, pending,
    });

  it("keeps a native u64 revision exact without inventing an editable branch", async () => {
    const source = projected();
    const publish = vi.fn();
    await observedRead(new ProjectMutationGate(), source, () => source, Promise.resolve(projected()), publish);
    expect(publish).toHaveBeenCalledExactlyOnceWith(source);
    expect(source).toEqual({ kind: "projection", identity: { project_id: id, generation, revision: "18446744073709551615" } });
    expect("project" in source).toBe(false);
  });

  it.each([
    ["9007199254740993", "9007199254740992"],
    ["18446744073709551615", "18446744073709551614"],
    ["100", "99"],
  ])("refuses exact decimal regression %s to %s", async (before, after) => {
    const source = projected(before), publish = vi.fn();
    await expect(observedRead(new ProjectMutationGate(), source, () => source, Promise.resolve(projected(after)), publish))
      .rejects.toThrow("does not match");
    expect(publish).not.toHaveBeenCalled();
  });

  it.each(["01", "18446744073709551616", "1e4", "-1"])("rejects a noncanonical source before dispatch: %s", async revision => {
    const source = projected(revision), load = vi.fn(async () => projected());
    await expect(new ProjectMutationGate().readView({
      source, current: () => source, requestedProjectId: id, load, view: value => value, publish: vi.fn(),
    })).rejects.toThrow("before opening");
    expect(load).not.toHaveBeenCalled();
  });

  it.each(["older first", "newer first"])("uses one read epoch across full and projected views: %s", async order => {
    const gate = new ProjectMutationGate(), old = deferred<ProjectViewRevision>(), latest = deferred<ProjectRevision>();
    let visible: ProjectViewRevision | null = null;
    const pending = vi.fn(), publish = vi.fn((value: ProjectViewRevision) => { visible = value; });
    const first = observedRead(gate, null, () => visible, old.promise, publish, pending);
    const refused = expect(first).rejects.toBeInstanceOf(ProjectReadSupersededError);
    const second = gate.read({ source: null, current: () => visible?.kind === "full" ? visible.project : null,
      requestedProjectId: "newer-full", load: () => latest.promise, project: value => value,
      publish: value => publish({ kind: "full", project: value }), pending });
    if (order === "older first") { old.resolve(projected()); await refused; expect(visible).toBeNull(); }
    latest.resolve({ ...base(), id: "newer-full" }); await second;
    if (order === "newer first") { old.resolve(projected()); await refused; }
    expect(visible).toEqual({ kind: "full", project: { ...base(), id: "newer-full" } });
    expect(publish).toHaveBeenCalledOnce();
    expect(pending.mock.calls).toEqual([[true], [true], [false]]);
  });

  it("a page read synchronously blocks both an edit and creation on the shared latch", async () => {
    const gate = new ProjectMutationGate(), source = projected(), reply = deferred<ProjectViewRevision>();
    const reading = observedRead(gate, source, () => source, reply.promise);
    const effect = vi.fn(async () => ({ ...base(), id: "created" }));
    await expect(gate.run(base(), base, effect, vi.fn())).rejects.toThrow("Finish the current");
    await expect(gate.create(null, () => null, effect, vi.fn())).rejects.toThrow("Finish the current");
    expect(effect).not.toHaveBeenCalled(); expect(gate.activity.busy).toBe(true);
    reply.resolve(source); await reading; expect(gate.activity.busy).toBe(false);
  });

  it("an actual mutation latch blocks a projected read before its load runs", async () => {
    const gate = new ProjectMutationGate(), reply = deferred<ProjectRevision>();
    const editing = gate.run(base(), base, () => reply.promise, vi.fn());
    const load = vi.fn(async () => projected());
    await expect(gate.readView({ source: null, current: () => null, requestedProjectId: id,
      load, view: value => value, publish: vi.fn() })).rejects.toThrow("Finish the current");
    expect(load).not.toHaveBeenCalled(); reply.resolve({ ...base(), revision: 13 }); await editing;
  });

  it.each(["project", "generation", "revision", "view kind"])("refuses a late page after an observed %s change", async field => {
    const gate = new ProjectMutationGate(), source = projected("12"), reply = deferred<ProjectViewRevision>(), publish = vi.fn();
    let visible = source;
    const reading = observedRead(gate, source, () => visible, reply.promise, publish);
    const refused = expect(reading).rejects.toThrow("changed while opening");
    visible = field === "view kind" ? { kind: "full", project: { id, generation, revision: 12, active_branch: "real-full-branch" } }
      : { kind: "projection", identity: {
        project_id: field === "project" ? "f1000000-0000-4000-8000-000000000003" : id,
        generation: field === "generation" ? "f1000000-0000-4000-8000-000000000004" : generation,
        revision: field === "revision" ? "13" : "12",
      } };
    reply.resolve(source); await refused; expect(publish).not.toHaveBeenCalled();
  });

  it("admits an explicit restored generation without treating a lower revision as the old generation", async () => {
    const source = projected(), next: ProjectViewRevision = { kind: "projection", identity: {
      project_id: id, generation: "f1000000-0000-4000-8000-000000000004", revision: "0",
    } };
    const publish = vi.fn();
    await observedRead(new ProjectMutationGate(), source, () => source, Promise.resolve(next), publish);
    expect(publish).toHaveBeenCalledExactlyOnceWith(next);
  });

  it("a failed projection observation is read once and preserves the current view", async () => {
    const gate = new ProjectMutationGate(), source = projected("12"), load = vi.fn(async () => { throw { code: "unavailable" }; });
    let visible = source;
    const publish = vi.fn((next: ProjectViewRevision) => { visible = next; });
    await expect(gate.readView({ source, current: () => source, requestedProjectId: id, load,
      view: () => source, publish })).rejects.toEqual({ code: "unavailable" });
    expect(load).toHaveBeenCalledOnce(); expect(publish).not.toHaveBeenCalled();
    expect(visible).toBe(source); expect(gate.activity).toEqual({ serial: 1, busy: false });
  });

  it("refuses a create callback whose projected source already changed without dispatch", async () => {
    const source = projected("12"), operation = vi.fn(async () => base());
    await expect(new ProjectMutationGate().create(null, () => null, operation, vi.fn(), undefined,
      { source, current: () => projected("13") })).rejects.toThrow("before creation");
    expect(operation).not.toHaveBeenCalled();
  });

  it("preserves a newer projected view when the original creation returns late", async () => {
    const gate = new ProjectMutationGate(), source = projected("12"), reply = deferred<ProjectRevision>(), publish = vi.fn();
    let visible = source;
    const creation = gate.create(null, () => null, () => reply.promise, publish, undefined, { source, current: () => visible });
    const refused = expect(creation).rejects.toThrow("changed during creation");
    visible = projected("13"); reply.resolve({ ...base(), id: "created", revision: 0 }); await refused;
    expect(publish).not.toHaveBeenCalled(); expect(visible).toEqual(projected("13"));
  });

  it("a creation from pages must return a distinct actual document", async () => {
    const source = projected("12"), publish = vi.fn();
    await expect(new ProjectMutationGate().create(null, () => null, async () => ({ ...base(), id, revision: 0 }),
      publish, undefined, { source, current: () => source })).rejects.toThrow("expected new document");
    expect(publish).not.toHaveBeenCalled();
  });
});
