import { writeFile } from "node:fs/promises";
import { createHash, randomUUID } from "node:crypto";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { ScopedChangeService, type ServiceRpcRequest, type ServiceRpcResponse } from "./support/scopedChangeService";
import type { Change, Project, Scene } from "../src/types";
import type { CameraDraft } from "../src/cameraReframeDraft";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "A binary built from this exact Rust/SQLite source revision is required.");
test.setTimeout(120_000);
const button=(page:Page,name:string)=>page.getByRole("button",{name,exact:true});
const applyRequests=(service:ScopedChangeService)=>service.transcript.filter(item=>
  item.direction==="request" && "command" in item.payload &&
  item.payload.command==="apply_change");
const camera=(page:Page)=>page.getByLabel("End center X",{exact:true});

test("Camera reframe keeps raw input, commits twice and reopens exact SQLite Project",async({page},info)=>{
 const service=await ScopedChangeService.create({
  databasePath:info.outputPath("store","camera.sqlite3"),desktopInvokeFixture:true,
 });
 let latest:Project|undefined;
 try{
  const initial=(await service.fixtureBootstrap()).project;
  const scene=initial.scenes.find(item=>item.renderer==="motion-canvas" &&
    item.nodes.length>0 && item.nodes.length<=32 && item.beats.length===0 &&
    item.nodes.every(node=>node.keyframes.length===0 && node.parent_id===null&&node.kind!=="group") &&
    item.camera.zoom===1 && item.camera.rotation_deg===0);
  if(!scene)throw new Error("The real service Project has no eligible original planar Camera scene.");
  await service.attachFixturePage(page,initial.id);await page.goto("/");
  const open=async()=>{
    await button(page,"Production").click();
    await page.getByLabel("Production working scene",{exact:true}).selectOption({value:scene.id});
    await button(page,"Camera").click();
    await expect(page.getByRole("heading",{name:"Camera Reframe",exact:true})).toBeVisible();
  };
  await open();
  await camera(page).fill("1e");
  await page.getByLabel("Camera duration frames",{exact:true}).fill("");
  await expect(button(page,"Attach camera reframe")).toBeDisabled();
  await button(page,"Brief").click();await open();
  await expect(camera(page)).toHaveValue("1e");
  await expect(page.getByLabel("Camera duration frames",{exact:true})).toHaveValue("");
  await page.reload();await open();
  await expect(camera(page)).toHaveValue("1e");
  await expect(page.getByLabel("Camera duration frames",{exact:true})).toHaveValue("");
  expect(applyRequests(service)).toHaveLength(0);
  const firstX=scene.camera.center_x+12, secondX=scene.camera.center_x+19;
  await camera(page).fill(String(firstX));
  await page.getByLabel("Camera duration frames",{exact:true}).fill("90");
  await expect(button(page,"Attach camera reframe")).toBeEnabled();
  await button(page,"Attach camera reframe").click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(initial.revision+1));
  latest=await service.project(initial.id);
  expect(latest.revision).toBe(initial.revision+1);
  const first=latest.production_design?.camera_reframes?.find(item=>item.scene_id===scene.id);
  expect(first?.end_center_x).toBe(firstX);
  expect(latest.scenes.find(item=>item.id===scene.id)?.camera.center_x).toBe(firstX);
  await button(page,"Brief").click();await open();
  await expect(camera(page)).toHaveValue(String(firstX));
  await camera(page).fill(String(secondX));
  await button(page,"Update camera reframe").click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(initial.revision+2));
  latest=await service.project(initial.id);
  const next=latest.production_design?.camera_reframes?.find(item=>item.scene_id===scene.id);
  expect(next?.id).toBe(first?.id);
  expect(next?.end_center_x).toBe(secondX);
  const journal=await service.history(initial.id,initial.revision);
  expect(journal.map(item=>item.change.type)).toEqual(["upsert_camera_reframe","upsert_camera_reframe"]);
  expect(applyRequests(service)).toHaveLength(2);
  await service.close();await service.start();await page.reload();await open();
  await expect(camera(page)).toHaveValue(String(secondX));
  expect(await service.project(initial.id)).toEqual(latest);
  expect(await service.history(initial.id,initial.revision)).toEqual(journal);
  expect(applyRequests(service)).toHaveLength(2);
  const evidence=info.outputPath("camera-reframe-source-real-sqlite.json");
  await writeFile(evidence,JSON.stringify({
    source:"real StudioService + SQLite and authored browser Camera interaction",
    initialRevision:initial.revision,finalRevision:latest.revision,
    sceneId:scene.id,cameraId:next!.id,firstX,secondX,
    events:journal.map(item=>item.change.type),applyCount:2,
    excludes:["native Film pixels","installed app","human approval"],
  },null,2));
  await info.attach("camera-reframe-source-real-sqlite",{path:evidence,contentType:"application/json"});
 }finally{
  await service.finalizeClosedStore(info.outputPath("closed-camera-store"),latest,
    [],[()=>page.close()]);
 }
});

test("Camera unconfirmed response cannot replay a real SQLite mutation",async({page},info)=>{
 const service=await ScopedChangeService.create({
  databasePath:info.outputPath("store","camera-lost-reply.sqlite3"),desktopInvokeFixture:true,
 });
 let latest:Project|undefined;
 const original=service.invoke.bind(service);
 let lost=false;
 try{
  const initial=(await service.fixtureBootstrap()).project;
  const scene=initial.scenes.find(item=>item.renderer==="motion-canvas"&&item.nodes.length>0&&
    item.nodes.length<=32&&!item.beats.length&&
    item.nodes.every(node=>node.keyframes.length===0&&!node.parent_id&&node.kind!=="group"));
  if(!scene)throw new Error("No eligible authored Camera scene in real SQLite fixture.");
  const recoveryScope=JSON.stringify([initial.id,initial.generation,initial.active_branch,scene.id]);
  const readRecovery=async()=>{
    const bytes=await page.evaluate(encoded=>{
      const writerId=sessionStorage.getItem("motionwright.camera-writer.v1:"+encoded);
      if(!writerId)throw new Error("Missing actual Camera writer pointer.");
      const key="motionwright.camera-recovery.v1:"+encoded+":"+writerId;
      const raw=localStorage.getItem(key);
      if(!raw)throw new Error("Missing actual Camera recovery bytes.");
      return {writerId,key,raw};
    },encodeURIComponent(recoveryScope));
    const copy=JSON.parse(bytes.raw) as {schema:string;writerId:string;scope:string;draft:CameraDraft};
    expect(copy.schema).toBe("motionwright.camera-recovery/1");
    expect(copy.scope).toBe(recoveryScope);
    expect(copy.writerId).toBe(bytes.writerId);
    expect(copy.draft.scope).toBe(recoveryScope);
    return {...bytes,sha256:createHash("sha256").update(bytes.raw).digest("hex"),draft:copy.draft};
  };
  await service.attachFixturePage(page,initial.id);await page.goto("/");
  await button(page,"Production").click();
  await page.getByLabel("Production working scene",{exact:true}).selectOption({value:scene.id});
  await button(page,"Camera").click();
  await expect(page.getByRole("heading",{name:"Camera Reframe"})).toBeVisible();
  service.invoke=async <T=unknown>(command:string,args:Record<string,unknown>={}):Promise<T>=>{
    const result=await original<T>(command,args);
    const change=(args.request as {change?:{type?:string}}|undefined)?.change;
    if(!lost&&command==="apply_change"&&change?.type==="upsert_camera_reframe"){
      lost=true;
      throw new Error("Transport lost its reply after real SQLite Camera commit");
    }
    return result;
  };
  const accepted=scene.camera.center_x+12;
  await camera(page).fill(String(accepted));
  await expect(button(page,"Attach camera reframe")).toBeEnabled();
  await button(page,"Attach camera reframe").click();
  await expect(page.getByRole("region",{name:"Unconfirmed Camera submission"})).toBeVisible();
  await expect(button(page,"Attach camera reframe")).toBeDisabled();
  latest=await service.project(initial.id);
  expect(latest.revision).toBe(initial.revision+1);
  expect(latest.production_design?.camera_reframes?.[0]?.end_center_x).toBe(accepted);
  const journal=await service.history(initial.id,initial.revision);
  expect(journal.map(item=>item.change.type)).toEqual(["upsert_camera_reframe"]);
  expect(applyRequests(service)).toHaveLength(1);
  const unknownCopy=await readRecovery();
  expect(unknownCopy.draft.awaiting?.operation).toBe("upsert");
  expect(unknownCopy.draft.awaiting?.revision).toBe(initial.revision);
  expect(unknownCopy.draft.awaiting?.id).toEqual(expect.any(String));
  const applied=applyRequests(service)[0].payload;
  if(!("command" in applied))throw new Error("Expected the actual service apply request.");
  const request=applied.args?.request as {
    project_id:string;generation:Project["generation"];revision:number;request_id:string;
    change:{type:"upsert_camera_reframe";reframe:unknown};
  };
  expect(unknownCopy.draft.awaiting?.source).toEqual(request.change.reframe);
  // The local Camera attempt ID and the service request_id are distinct domains.
  const responses=service.transcript.filter(item=>item.direction==="response"&&
    "ok" in item.payload&&item.payload.id===applied.id);
  expect(responses).toHaveLength(1);
  expect("ok" in responses[0].payload&&responses[0].payload.ok).toBe(true);
  await button(page,"Brief").click();await button(page,"Production").click();
  await button(page,"Camera").click();
  expect(applyRequests(service)).toHaveLength(1);
  await page.reload();await button(page,"Production").click();await button(page,"Camera").click();
  await expect(camera(page)).toHaveValue(String(accepted));
  const reloadedCopy=await readRecovery();
  await info.attach("camera-lost-reply-persistent-unknown",{
    body:JSON.stringify({
      request:{project_id:request.project_id,generation:request.generation,
        revision:request.revision,request_id:request.request_id},
      unknownCopy,reloadedCopy,
      savedRevision:latest.revision,applyCount:applyRequests(service).length,
      excludes:["native Film pixels","installed app","human approval"],
    },null,2),contentType:"application/json",
  });
  // Matching saved values after reload do not acknowledge the lost callback.
  await expect(page.getByRole("region",{name:"Unconfirmed Camera submission"})).toBeVisible();
  await expect(page.getByRole("button",{name:/^(Attach|Update) camera reframe$/})).toBeDisabled();
  expect(reloadedCopy.writerId).toBe(unknownCopy.writerId);
  expect(reloadedCopy.key).toBe(unknownCopy.key);
  expect(reloadedCopy.draft.awaiting).toEqual(unknownCopy.draft.awaiting);
  expect(reloadedCopy.draft.fields).toEqual(unknownCopy.draft.fields);
  expect(reloadedCopy.draft.initial).toEqual(unknownCopy.draft.initial);
  expect(reloadedCopy.draft.baseRevision).toBe(unknownCopy.draft.baseRevision);
  expect(reloadedCopy.draft.base).toEqual(unknownCopy.draft.base);
  expect(reloadedCopy.draft.sequence).toBe(unknownCopy.draft.sequence);
  expect(await service.project(initial.id)).toEqual(latest);
  expect(await service.history(initial.id,initial.revision)).toEqual(journal);
  expect(applyRequests(service)).toHaveLength(1);
 }finally{
  service.invoke=original;
  await service.finalizeClosedStore(info.outputPath("closed-camera-store"),latest,
    [],[()=>page.close()]);
 }
});

// Keep the additional authority helpers local to these five service cases.
{
  type CameraChange = Extract<Change, { type: "upsert_camera_reframe" | "detach_camera_reframe" }>;
  type ApplyRequest = {
    project_id: string;
    generation: Project["generation"];
    revision: number;
    request_id: string;
    effect_grant: string;
    change: CameraChange;
  };
  type CapturedApply = {
    args: Record<string, unknown>;
    request: ApplyRequest;
    argsSha256: string;
  };
  type OriginalTerminal =
    | { status: "fulfilled"; project: Project }
    | { status: "rejected"; error: string };
  type CameraCopy = {
    writerId: string;
    key: string;
    raw: string;
    sha256: string;
    draft: CameraDraft;
  };

  const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
  const saveButton = (page: Page) => page.getByRole("button", { name: /^(Attach|Update) camera reframe$/ });
  const sha256 = (value: string) => createHash("sha256").update(value).digest("hex");

  function cameraScene(project: Project): Scene {
    const scene = project.scenes.find(item => item.renderer === "motion-canvas"
      && item.nodes.length > 0 && item.nodes.length <= 32 && item.beats.length === 0
      && item.nodes.every(node => node.keyframes.length === 0 && node.parent_id === null && node.kind !== "group")
      && item.camera.zoom === 1 && item.camera.rotation_deg === 0
      && !project.production_design?.camera_reframes?.some(camera => camera.scene_id === item.id));
    if (!scene) throw new Error("The real fixture has no eligible original planar Camera scene.");
    return scene;
  }

  async function openCamera(page: Page, sceneId: string) {
    await button(page, "Production").click();
    await page.getByLabel("Production working scene", { exact: true }).selectOption({ value: sceneId });
    await button(page, "Camera").click();
    await expect(page.getByRole("heading", { name: "Camera Reframe", exact: true })).toBeVisible();
  }

  function otherCameraScene(project: Project, original: Scene): Scene {
    const scene = project.scenes.find(item => item.id !== original.id && item.renderer === "motion-canvas")
      ?? project.scenes.find(item => item.id !== original.id);
    if (!scene) throw new Error("The real fixture needs a second actual scene for Camera scope isolation.");
    return scene;
  }

  async function prepareOtherSceneDraft(page: Page, project: Project, scene: Scene) {
    await openCamera(page, scene.id);
    // Author B before submitting A: pending may legitimately disable all inputs.
    await page.getByLabel("End center X", { exact: true }).fill("1e");
    await page.getByLabel("Camera duration frames", { exact: true }).fill("");
    const copy = await readCameraCopy(page, project, scene);
    expect(copy.draft.fields.end_center_x).toBe("1e");
    expect(copy.draft.fields.duration_frames).toBe("");
    expect(copy.draft.awaiting).toBeNull();
    return copy;
  }

  async function expectOtherSceneDraftVisible(page: Page, scene: Scene) {
    await expect(page.getByLabel("Production working scene", { exact: true })).toHaveValue(scene.id);
    await expect(page.getByLabel("End center X", { exact: true })).toHaveValue("1e");
    await expect(page.getByLabel("Camera duration frames", { exact: true })).toHaveValue("");
  }

  /**
   * Observe settlement in the current browser document. This is a pass-through
   * wrapper: the exact service result/error still reaches the product, unchanged.
   * Waiting only for a Node-side service reply is too early for a late UI callback.
   */
  async function observeBrowserInvoke(page: Page, command: "bootstrap" | "apply_change") {
    const key = "__a3_camera_observed_invoke_" + randomUUID().replaceAll("-", "");
    await page.evaluate(({ key, command }) => {
      type Probe = { started: number; fulfilled: number; rejected: number; settled: number };
      type Bridge = { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      const host = window as unknown as Record<string, unknown>;
      const bridge = host.__TAURI_INTERNALS__ as Bridge | undefined;
      if (!bridge || typeof bridge.invoke !== "function") throw new Error("The real service bridge is absent.");
      const original = bridge.invoke;
      const probe: Probe = { started: 0, fulfilled: 0, rejected: 0, settled: 0 };
      host[key] = probe;
      bridge.invoke = async (actual, args = {}) => {
        const observed = actual === command;
        if (observed) probe.started += 1;
        try {
          const result = await original.call(bridge, actual, args);
          if (observed) probe.fulfilled += 1;
          return result;
        } catch (error) {
          if (observed) probe.rejected += 1;
          throw error;
        } finally {
          if (observed) probe.settled += 1;
        }
      };
    }, { key, command });
    return {
      async wait() {
        await page.waitForFunction(key => {
          const probe = (window as unknown as Record<string, unknown>)[key] as { settled: number } | undefined;
          return !!probe && probe.settled >= 1;
        }, key, { timeout: 10_000 });
        // Let the product's awaiting continuation and its React effects complete.
        await page.evaluate(() => new Promise<void>(resolve => {
          requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
        }));
        return page.evaluate(key => (window as unknown as Record<string, unknown>)[key] as {
          started: number; fulfilled: number; rejected: number; settled: number;
        }, key);
      },
    };
  }

  async function inspectSavedCamera(page: Page, allowPredecessorLabel = false) {
    const observation = await observeBrowserInvoke(page, "bootstrap");
    const inspect = allowPredecessorLabel
      ? page.getByRole("button", { name: /^(Inspect saved Camera|I verified this operation was not saved)$/ })
      : button(page, "Inspect saved Camera");
    await inspect.click();
    expect(await observation.wait()).toEqual({ started: 1, fulfilled: 1, rejected: 0, settled: 1 });
  }

  /** Read the actual persisted bytes. This helper never modifies browser storage. */
  async function readCameraCopy(page: Page, project: Project, scene: Scene): Promise<CameraCopy> {
    const scope = JSON.stringify([project.id, project.generation, project.active_branch, scene.id]);
    const encodedScope = encodeURIComponent(scope);
    const stored = await page.evaluate(encoded => {
      const writerId = window.sessionStorage.getItem("motionwright.camera-writer.v1:" + encoded);
      if (!writerId) throw new Error("Camera has no persisted session writer.");
      const key = "motionwright.camera-recovery.v1:" + encoded + ":" + writerId;
      const raw = window.localStorage.getItem(key);
      if (!raw) throw new Error("Camera writer has no recoverable bytes.");
      return { writerId, key, raw };
    }, encodedScope);
    const decoded = JSON.parse(stored.raw) as {
      schema?: string; writerId?: string; scope?: string; draft?: CameraDraft;
    };
    expect(decoded.schema).toBe("motionwright.camera-recovery/1");
    expect(decoded.writerId).toBe(stored.writerId);
    expect(decoded.scope).toBe(scope);
    if (!decoded.draft) throw new Error("Camera recovery bytes do not contain an author draft.");
    expect(decoded.draft.scope).toBe(scope);
    return { ...stored, sha256: sha256(stored.raw), draft: decoded.draft };
  }

  function expectSamePendingCopy(original: CameraCopy, observed: CameraCopy) {
    expect(original.draft.awaiting).not.toBeNull();
    // In particular, a peer's equal values cannot remove the original request ID.
    expect(observed.draft.awaiting).toEqual(original.draft.awaiting);
    expect(observed.draft.fields).toEqual(original.draft.fields);
    expect(observed.draft.initial).toEqual(original.draft.initial);
    expect(observed.draft.base).toEqual(original.draft.base);
    expect(observed.draft.baseRevision).toBe(original.draft.baseRevision);
    expect(observed.draft.sequence).toBe(original.draft.sequence);
    expect(observed.draft.cameraId).toBe(original.draft.cameraId);
    expect(observed.draft.sceneId).toBe(original.draft.sceneId);
  }

  function expectExactCopy(original: CameraCopy, observed: CameraCopy) {
    expect(observed.writerId).toBe(original.writerId);
    expect(observed.key).toBe(original.key);
    expect(observed.raw).toBe(original.raw);
    expect(observed.sha256).toBe(original.sha256);
    expect(observed.draft).toEqual(original.draft);
  }

  async function expectUnknown(page: Page) {
    await expect(page.getByRole("region", { name: "Unconfirmed Camera submission", exact: true })).toBeVisible();
    await expect(saveButton(page)).toBeDisabled();
  }

  async function attachJson(info: TestInfo, name: string, value: unknown) {
    await info.attach(name, { body: JSON.stringify(value, null, 2), contentType: "application/json" });
  }

  function rawApplies(service: ScopedChangeService): ServiceRpcRequest[] {
    return service.transcript.flatMap(entry => entry.direction === "request"
      && "command" in entry.payload && entry.payload.command === "apply_change" ? [entry.payload] : []);
  }

  function applyRequest(payload: ServiceRpcRequest): ApplyRequest {
    return payload.args?.request as ApplyRequest;
  }

  function terminalExchange(service: ScopedChangeService, requestId: string) {
    const requests = rawApplies(service).filter(payload => applyRequest(payload).request_id === requestId);
    expect(requests).toHaveLength(1);
    const request = requests[0];
    const responses: ServiceRpcResponse[] = service.transcript.flatMap(entry => entry.direction === "response"
      && "ok" in entry.payload && entry.payload.id === request.id ? [entry.payload] : []);
    expect(responses).toHaveLength(1);
    return { rpcId: request.id, response: responses[0] };
  }

  /**
   * Hold the first authentic UI apply before sending it to Rust. Grants and every
   * other call remain real. The stored arguments are dispatched exactly once when
   * released; no Project, grant, success or failure result is supplied by the test.
   */
  function holdOriginal(service: ScopedChangeService, changeType: CameraChange["type"]) {
    const previous = service.invoke;
    const invokeOriginal = previous.bind(service);
    let releaseGate!: () => void;
    const gate = new Promise<void>(resolve => { releaseGate = resolve; });
    let finish!: (outcome: OriginalTerminal) => void;
    const terminal = new Promise<OriginalTerminal>(resolve => { finish = resolve; });
    let captured: CapturedApply | null = null;
    let uiAttempts = 0;
    let originalRustSends = 0;
    let settled = false;
    let released = false;

    const intercepted = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const candidate = args.request as { change?: { type?: string } } | undefined;
      if (command !== "apply_change" || candidate?.change?.type !== changeType) {
        return invokeOriginal<T>(command, args);
      }
      uiAttempts += 1;
      // A duplicate is a genuine product request and is counted. Never replace it
      // with a mock rejection that could conceal automatic replay by the product.
      if (captured) return invokeOriginal<T>(command, args);
      const bytes = JSON.stringify(args);
      const frozenArgs = JSON.parse(bytes) as Record<string, unknown>;
      const request = frozenArgs.request as ApplyRequest;
      if (typeof request.request_id !== "string" || !request.request_id
        || typeof request.effect_grant !== "string" || !request.effect_grant) {
        throw new Error("The held UI request lacks its real service request ID or grant.");
      }
      captured = { args: frozenArgs, request, argsSha256: sha256(bytes) };
      await gate;
      originalRustSends += 1;
      try {
        const result = await invokeOriginal<Project>(command, frozenArgs);
        settled = true;
        finish({ status: "fulfilled", project: result });
        return result as T;
      } catch (error) {
        settled = true;
        finish({ status: "rejected", error: error instanceof Error ? error.message : String(error) });
        throw error;
      }
    };
    service.invoke = intercepted;

    return {
      get captured() { return captured; },
      get counts() { return { uiAttempts, originalRustSends, settled, released }; },
      release() {
        if (!released) { released = true; releaseGate(); }
      },
      async drain(): Promise<OriginalTerminal | null> {
        this.release();
        return captured ? terminal : null;
      },
      restore() {
        if (service.invoke !== intercepted) throw new Error("Another writer replaced the Camera transport gate.");
        service.invoke = previous;
      },
    };
  }

  type HeldOriginal = ReturnType<typeof holdOriginal>;

  async function capturedOriginal(held: HeldOriginal): Promise<CapturedApply> {
    await expect.poll(() => held.captured !== null, { timeout: 10_000 }).toBe(true);
    if (!held.captured) throw new Error("No original Camera apply was captured.");
    return held.captured;
  }

  function publicRequest(original: CapturedApply) {
    // Preserve a digest of the exact arguments without exposing the bearer grant.
    const { project_id, generation, revision, request_id, change } = original.request;
    return { argsSha256: original.argsSha256, project_id, generation, revision, request_id, change };
  }

  function loseOriginalReply(service: ScopedChangeService) {
    const previous = service.invoke;
    const invokeOriginal = previous.bind(service);
    let captured: CapturedApply | null = null;
    let terminal: OriginalTerminal | null = null;
    let finish!: (outcome: OriginalTerminal) => void;
    const finished = new Promise<OriginalTerminal>(resolve => { finish = resolve; });
    let lost = false;
    const intercepted = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const request = args.request as ApplyRequest | undefined;
      if (captured || command !== "apply_change" || request?.change?.type !== "upsert_camera_reframe") {
        return invokeOriginal<T>(command, args);
      }
      const bytes = JSON.stringify(args);
      const frozenArgs = JSON.parse(bytes) as Record<string, unknown>;
      captured = { args: frozenArgs, request: frozenArgs.request as ApplyRequest, argsSha256: sha256(bytes) };
      let result: Project;
      try {
        result = await invokeOriginal<Project>(command, frozenArgs);
        terminal = { status: "fulfilled", project: result };
        finish(terminal);
      } catch (error) {
        terminal = { status: "rejected", error: error instanceof Error ? error.message : String(error) };
        finish(terminal);
        throw error;
      }
      lost = true;
      // The actual service has already committed. Lose only its browser reply.
      throw new Error("Transport lost its reply after real SQLite Camera commit");
    };
    service.invoke = intercepted;
    return {
      get captured() { return captured; },
      get terminal() { return terminal; },
      get lost() { return lost; },
      async drain() { return captured ? finished : null; },
      restore() {
        if (service.invoke !== intercepted) throw new Error("Another writer replaced the Camera lost-reply observer.");
        service.invoke = previous;
      },
    };
  }

  function holdCameraInspection(service: ScopedChangeService, projectId: string) {
    const previous = service.invoke;
    const invokeOriginal = previous.bind(service);
    let releaseGate!: () => void;
    const gate = new Promise<void>(resolve => { releaseGate = resolve; });
    type ReadTerminal = { status: "fulfilled"; project: Project | null } | { status: "rejected"; error: string };
    let finish!: (outcome: ReadTerminal) => void;
    const terminal = new Promise<ReadTerminal>(resolve => { finish = resolve; });
    let captured = false, released = false, settled = false;
    let uiReads = 0, rustReads = 0;
    const intercepted = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const request = args.request as { project_id?: unknown } | undefined;
      if (command !== "bootstrap" || request?.project_id !== projectId) return invokeOriginal<T>(command, args);
      uiReads += 1;
      if (captured) return invokeOriginal<T>(command, args);
      captured = true;
      const frozenArgs = JSON.parse(JSON.stringify(args)) as Record<string, unknown>;
      await gate;
      rustReads += 1;
      try {
        const result = await invokeOriginal<T>(command, frozenArgs);
        settled = true;
        finish({ status: "fulfilled", project: (result as { project?: Project | null }).project ?? null });
        return result;
      } catch (error) {
        settled = true;
        finish({ status: "rejected", error: error instanceof Error ? error.message : String(error) });
        throw error;
      }
    };
    service.invoke = intercepted;
    return {
      get counts() { return { captured, uiReads, rustReads, released, settled }; },
      async drain(): Promise<ReadTerminal | null> {
        if (!released) { released = true; releaseGate(); }
        return captured ? terminal : null;
      },
      restore() {
        if (service.invoke !== intercepted) throw new Error("Another writer replaced the Camera read gate.");
        service.invoke = previous;
      },
    };
  }

  for (const operation of ["upsert", "detach"] as const) {
    test(`a peer ${operation} with identical saved Camera values cannot acknowledge the held original`, async ({ page }, info) => {
      const service = await ScopedChangeService.create({
        databasePath: info.outputPath("store", "camera-held-" + operation + ".sqlite3"),
        desktopInvokeFixture: true,
      });
      let peer: ScopedChangeService | undefined;
      let held: HeldOriginal | undefined;
      let base: Project | undefined, winner: Project | undefined;
      try {
        base = (await service.fixtureBootstrap()).project;
        const eligible = cameraScene(base);
        if (operation === "detach") {
          // Real fixture preparation only. The detach under test is authored by UI.
          base = await service.apply(base, { type: "upsert_camera_reframe", reframe: {
            id: randomUUID(), scene_id: eligible.id, component_version: 1,
            start_center_x: eligible.camera.center_x, start_center_y: eligible.camera.center_y,
            end_center_x: eligible.camera.center_x + 12, end_center_y: eligible.camera.center_y + 4,
            duration_frames: 90,
          } });
        }
        const scene = base.scenes.find(item => item.id === eligible.id)!;
        const baseJournal = await service.history(base.id, 0);
        peer = await service.peer();
        await service.attachFixturePage(page, base.id);
        await page.goto("/");
        await openCamera(page, scene.id);
        if (operation === "upsert") {
          await page.getByLabel("End center X", { exact: true }).fill(String(scene.camera.center_x + 19));
          await page.getByLabel("Camera duration frames", { exact: true }).fill("90");
        }
        const rawApplyCountBeforeHold = rawApplies(service).length;
        held = holdOriginal(service, operation === "upsert" ? "upsert_camera_reframe" : "detach_camera_reframe");
        await button(page, operation === "upsert" ? "Attach camera reframe" : "Detach, preserve final camera").click();
        const original = await capturedOriginal(held);
        expect(original.request.project_id).toBe(base.id);
        expect(original.request.generation).toBe(base.generation);
        expect(original.request.revision).toBe(base.revision);
        const beforeCopy = await readCameraCopy(page, base, scene);
        expect(beforeCopy.draft.awaiting?.operation).toBe(operation);
        expect(beforeCopy.draft.awaiting?.revision).toBe(base.revision);
        expect(beforeCopy.draft.awaiting?.source).toEqual(original.request.change.type === "upsert_camera_reframe"
          ? original.request.change.reframe : null);
        expect(held.counts).toEqual({ uiAttempts: 1, originalRustSends: 0, settled: false, released: false });
        expect(rawApplies(service)).toHaveLength(rawApplyCountBeforeHold);
        expect(await service.project(base.id)).toEqual(base);
        expect(await service.history(base.id, 0)).toEqual(baseJournal);
        await attachJson(info, "held-original-before-peer", {
          operation, original: publicRequest(original), counts: held.counts, copy: beforeCopy, base, baseJournal,
        });

        // Use the UI-authored exact change, but a different real request and grant.
        winner = await peer.apply(base, structuredClone(original.request.change));
        const winnerJournal = await service.history(base.id, 0);
        expect(winner.revision).toBe(base.revision + 1);
        expect(winnerJournal).toHaveLength(baseJournal.length + 1);
        const peerRequests = rawApplies(peer);
        expect(peerRequests).toHaveLength(1);
        expect(applyRequest(peerRequests[0]).change).toEqual(original.request.change);
        expect(applyRequest(peerRequests[0]).request_id).not.toBe(original.request.request_id);
        const stored = winner.production_design?.camera_reframes?.find(item => item.scene_id === scene.id) ?? null;
        expect(stored).toEqual(beforeCopy.draft.awaiting!.source);

        // Reload abandons the original browser callback while its transport work
        // remains held. The new UI reads an authentic P1 with matching values.
        await page.reload();
        await openCamera(page, scene.id);
        await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + winner.revision);
        const afterCopy = await readCameraCopy(page, base, scene);
        await attachJson(info, "held-original-peer-readback", {
          operation, original: publicRequest(original), counts: held.counts,
          beforeCopy, afterCopy, winner, winnerJournal,
        });
        // These copy/UI assertions are the causal RED on the predecessor, before
        // any new button name or a terminal result can affect the test.
        expectSamePendingCopy(beforeCopy, afterCopy);
        await expectUnknown(page);
        expect(held.counts).toEqual({ uiAttempts: 1, originalRustSends: 0, settled: false, released: false });
        expect(await service.project(base.id)).toEqual(winner);
        expect(await service.history(base.id, 0)).toEqual(winnerJournal);
        expect(rawApplies(service)).toHaveLength(rawApplyCountBeforeHold);

        await inspectSavedCamera(page);
        const inspected = await readCameraCopy(page, base, scene);
        expectSamePendingCopy(beforeCopy, inspected);
        await expectUnknown(page);
        expect(held.counts.settled).toBe(false);

        const terminal = await held.drain();
        expect(terminal?.status).toBe("rejected");
        const exchange = terminalExchange(service, original.request.request_id);
        expect(exchange.response.ok).toBe(false);
        if (exchange.response.ok) throw new Error("The stale original unexpectedly succeeded.");
        // A correlated service rejection must concern the stale revision, not a
        // synthetic timeout, an absent executable or a mock transport failure.
        expect(exchange.response.error).toMatch(/revision|stale|conflict/i);
        expect(held.counts).toEqual({ uiAttempts: 1, originalRustSends: 1, settled: true, released: true });
        expect(rawApplies(service)).toHaveLength(rawApplyCountBeforeHold + 1);
        expect(await service.project(base.id)).toEqual(winner);
        expect(await service.history(base.id, 0)).toEqual(winnerJournal);
        await expectUnknown(page);
        expectSamePendingCopy(beforeCopy, await readCameraCopy(page, base, scene));
        await attachJson(info, "held-original-terminal", {
          operation, original: publicRequest(original), terminal, exchange,
          counts: held.counts, winner, winnerJournal,
          excludes: ["native Film pixels", "installed app", "human acceptance"],
        });
      } finally {
        // Even a pre-release RED must not orphan the actual original request.
        if (held) {
          const terminal = await held.drain();
          if (!winner && terminal?.status === "fulfilled") base = terminal.project;
          held.restore();
        }
        await service.finalizeClosedStore(info.outputPath("closed-camera-store"), winner ?? base,
          peer ? [peer] : [], [() => page.close()]);
      }
    });
  }

  test("a same-revision Camera inspection cannot release an original that is still held", async ({ page }, info) => {
    const service = await ScopedChangeService.create({
      databasePath: info.outputPath("store", "camera-held-same-revision.sqlite3"),
      desktopInvokeFixture: true,
    });
    let held: HeldOriginal | undefined;
    let base: Project | undefined, final: Project | undefined;
    try {
      base = (await service.fixtureBootstrap()).project;
      const scene = cameraScene(base);
      const baseJournal = await service.history(base.id, 0);
      await service.attachFixturePage(page, base.id);
      await page.goto("/");
      await openCamera(page, scene.id);
      await page.getByLabel("End center X", { exact: true }).fill(String(scene.camera.center_x + 19));
      const rawApplyCountBeforeHold = rawApplies(service).length;
      held = holdOriginal(service, "upsert_camera_reframe");
      await button(page, "Attach camera reframe").click();
      const original = await capturedOriginal(held);
      const beforeCopy = await readCameraCopy(page, base, scene);
      expect(beforeCopy.draft.awaiting?.operation).toBe("upsert");
      expect(beforeCopy.draft.awaiting?.source).toEqual(original.request.change.type === "upsert_camera_reframe"
        ? original.request.change.reframe : null);
      expect(held.counts).toEqual({ uiAttempts: 1, originalRustSends: 0, settled: false, released: false });

      await page.reload();
      await openCamera(page, scene.id);
      await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + base.revision);
      expectSamePendingCopy(beforeCopy, await readCameraCopy(page, base, scene));
      await expectUnknown(page);
      // Both labels use the existing product read path. The old spelling permits
      // a causal RED against the predecessor instead of failing on a renamed UI.
      const bootstrapRequestsBefore = service.transcript.filter(entry => entry.direction === "request"
        && "command" in entry.payload && entry.payload.command === "bootstrap").length;
      await inspectSavedCamera(page, true);
      await expect.poll(() => service.transcript.filter(entry => entry.direction === "request"
        && "command" in entry.payload && entry.payload.command === "bootstrap").length)
        .toBe(bootstrapRequestsBefore + 1);
      const afterCopy = await readCameraCopy(page, base, scene);
      await attachJson(info, "held-original-same-revision-inspection", {
        original: publicRequest(original), beforeCopy, afterCopy, counts: held.counts, base, baseJournal,
      });
      expectSamePendingCopy(beforeCopy, afterCopy);
      await expectUnknown(page);
      expect(held.counts).toEqual({ uiAttempts: 1, originalRustSends: 0, settled: false, released: false });
      expect(rawApplies(service)).toHaveLength(rawApplyCountBeforeHold);
      expect(await service.project(base.id)).toEqual(base);
      expect(await service.history(base.id, 0)).toEqual(baseJournal);

      const terminal = await held.drain();
      expect(terminal?.status).toBe("fulfilled");
      if (terminal?.status !== "fulfilled") throw new Error("The original Camera request did not commit after release.");
      final = terminal.project;
      expect(final.revision).toBe(base.revision + 1);
      const exchange = terminalExchange(service, original.request.request_id);
      expect(exchange.response.ok).toBe(true);
      const finalJournal = await service.history(base.id, 0);
      expect(finalJournal).toHaveLength(baseJournal.length + 1);
      expect(finalJournal[finalJournal.length - 1]?.change).toEqual(original.request.change);
      expect(held.counts).toEqual({ uiAttempts: 1, originalRustSends: 1, settled: true, released: true });
      expect(rawApplies(service)).toHaveLength(rawApplyCountBeforeHold + 1);

      // The harness knows the terminal result, but the callback belonged to the
      // previous browser document. Reading matching P1 is still not its ACK.
      await page.reload();
      await openCamera(page, scene.id);
      await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + final.revision);
      await expectUnknown(page);
      expectSamePendingCopy(beforeCopy, await readCameraCopy(page, base, scene));
      await inspectSavedCamera(page);
      await expectUnknown(page);
      expectSamePendingCopy(beforeCopy, await readCameraCopy(page, base, scene));
      expect(await service.project(base.id)).toEqual(final);
      expect(await service.history(base.id, 0)).toEqual(finalJournal);
      expect(held.counts.uiAttempts).toBe(1);
      await attachJson(info, "held-original-same-revision-terminal", {
        original: publicRequest(original), terminal, exchange, counts: held.counts,
        beforeCopy, finalCopy: await readCameraCopy(page, base, scene), final, finalJournal,
        excludes: ["native Film pixels", "installed app", "human acceptance"],
      });
    } finally {
      if (held) {
        const terminal = await held.drain();
        if (terminal?.status === "fulfilled") final = terminal.project;
        held.restore();
      }
      await service.finalizeClosedStore(info.outputPath("closed-camera-store"), final ?? base,
        [], [() => page.close()]);
    }
  });

  test("a late saved-Camera read for scene A preserves the exact visible author copy of scene B", async ({ page }, info) => {
    const service = await ScopedChangeService.create({
      databasePath: info.outputPath("store", "camera-late-read-scene-scope.sqlite3"),
      desktopInvokeFixture: true,
    });
    let lost: ReturnType<typeof loseOriginalReply> | undefined;
    let read: ReturnType<typeof holdCameraInspection> | undefined;
    let base: Project | undefined, saved: Project | undefined;
    try {
      base = (await service.fixtureBootstrap()).project;
      const sceneA = cameraScene(base);
      const sceneB = otherCameraScene(base, sceneA);
      const baseJournal = await service.history(base.id, 0);
      await service.attachFixturePage(page, base.id);
      await page.goto("/");
      const preparedB = await prepareOtherSceneDraft(page, base, sceneB);
      await openCamera(page, sceneA.id);
      await page.getByLabel("End center X", { exact: true }).fill(String(sceneA.camera.center_x + 19));
      const applyCountBefore = rawApplies(service).length;
      lost = loseOriginalReply(service);
      await button(page, "Attach camera reframe").click();
      await expectUnknown(page);
      await expect.poll(() => lost!.lost).toBe(true);
      const terminal = await lost.drain();
      expect(terminal?.status).toBe("fulfilled");
      if (terminal?.status !== "fulfilled" || !lost.captured) {
        throw new Error("The lost browser reply must follow an actual service commit.");
      }
      saved = terminal.project;
      const original = lost.captured;
      const exchange = terminalExchange(service, original.request.request_id);
      expect(exchange.response.ok).toBe(true);
      expect(saved.revision).toBe(base.revision + 1);
      const savedJournal = await service.history(base.id, 0);
      expect(savedJournal).toHaveLength(baseJournal.length + 1);
      expect(savedJournal[savedJournal.length - 1]?.change).toEqual(original.request.change);
      const unknownA = await readCameraCopy(page, base, sceneA);
      expect(unknownA.draft.awaiting?.source).toEqual(original.request.change.type === "upsert_camera_reframe"
        ? original.request.change.reframe : null);
      expect(unknownA.draft.awaiting?.revision).toBe(base.revision);

      // Hold only the UI's next real bootstrap. No Project is fabricated and the
      // loss of the original apply reply has already reached a terminal outcome.
      read = holdCameraInspection(service, base.id);
      const observation = await observeBrowserInvoke(page, "bootstrap");
      const bootstrapCountBefore = service.transcript.filter(entry => entry.direction === "request"
        && "command" in entry.payload && entry.payload.command === "bootstrap").length;
      // Keep the predecessor's real read reachable for a causal scope/error RED.
      await page.getByRole("button", {
        name: /^(Inspect saved Camera|I verified this operation was not saved)$/,
      }).click();
      await expect.poll(() => read!.counts.captured).toBe(true);
      expect(read.counts).toEqual({ captured: true, uiReads: 1, rustReads: 0, released: false, settled: false });
      expect(service.transcript.filter(entry => entry.direction === "request"
        && "command" in entry.payload && entry.payload.command === "bootstrap"))
        .toHaveLength(bootstrapCountBefore);

      // Scene selection is a real UI operation while read A is pending. B was
      // authored earlier so this remains meaningful under global input disabling.
      await page.getByLabel("Production working scene", { exact: true }).selectOption({ value: sceneB.id });
      await expectOtherSceneDraftVisible(page, sceneB);
      const beforeB = await readCameraCopy(page, base, sceneB);
      expectExactCopy(preparedB, beforeB);
      const controls = page.getByRole("region", { name: "Planar camera controls", exact: true });
      const beforeBAlerts = await controls.getByRole("alert").allTextContents();
      expectSamePendingCopy(unknownA, await readCameraCopy(page, base, sceneA));

      const readTerminal = await read.drain();
      expect(readTerminal?.status).toBe("fulfilled");
      if (readTerminal?.status !== "fulfilled") throw new Error("The original real Camera inspection failed.");
      expect(readTerminal.project).toEqual(saved);
      expect(await observation.wait()).toEqual({ started: 1, fulfilled: 1, rejected: 0, settled: 1 });
      expect(read.counts).toEqual({ captured: true, uiReads: 1, rustReads: 1, released: true, settled: true });
      const afterB = await readCameraCopy(page, base, sceneB);
      const afterA = await readCameraCopy(page, base, sceneA);
      expectExactCopy(beforeB, afterB);
      await expectOtherSceneDraftVisible(page, sceneB);
      expect(await controls.getByRole("alert").allTextContents()).toEqual(beforeBAlerts);
      await expect(page.getByRole("region", { name: "Unconfirmed Camera submission", exact: true })).toHaveCount(0);
      expectSamePendingCopy(unknownA, afterA);
      expect(await service.project(base.id)).toEqual(saved);
      expect(await service.history(base.id, 0)).toEqual(savedJournal);
      expect(rawApplies(service)).toHaveLength(applyCountBefore + 1);

      // A's original result remains unknown to the author after returning there.
      await page.getByLabel("Production working scene", { exact: true }).selectOption({ value: sceneA.id });
      await expectUnknown(page);
      expectSamePendingCopy(unknownA, await readCameraCopy(page, base, sceneA));
      expectExactCopy(beforeB, await readCameraCopy(page, base, sceneB));
      await attachJson(info, "late-camera-read-scene-isolation", {
        original: publicRequest(original), exchange, readCounts: read.counts,
        unknownA, afterA, preparedB, beforeB, afterB, beforeBAlerts,
        saved, savedJournal,
        excludes: ["native Film pixels", "installed app", "human acceptance"],
      });
    } finally {
      if (read) { await read.drain(); read.restore(); }
      if (lost) {
        const terminal = await lost.drain();
        if (terminal?.status === "fulfilled") saved = terminal.project;
        lost.restore();
      }
      await service.finalizeClosedStore(info.outputPath("closed-camera-store"), saved ?? base,
        [], [() => page.close()]);
    }
  });

  test("a successful original Camera reply closes only scene A while scene B keeps its exact author bytes", async ({ page }, info) => {
    const service = await ScopedChangeService.create({
      databasePath: info.outputPath("store", "camera-late-ack-scene-scope.sqlite3"),
      desktopInvokeFixture: true,
    });
    let held: HeldOriginal | undefined;
    let base: Project | undefined, saved: Project | undefined;
    try {
      base = (await service.fixtureBootstrap()).project;
      const sceneA = cameraScene(base);
      const sceneB = otherCameraScene(base, sceneA);
      const baseJournal = await service.history(base.id, 0);
      await service.attachFixturePage(page, base.id);
      await page.goto("/");
      const preparedB = await prepareOtherSceneDraft(page, base, sceneB);
      await openCamera(page, sceneA.id);
      await page.getByLabel("End center X", { exact: true }).fill(String(sceneA.camera.center_x + 19));
      const applyCountBefore = rawApplies(service).length;
      held = holdOriginal(service, "upsert_camera_reframe");
      const observation = await observeBrowserInvoke(page, "apply_change");
      await button(page, "Attach camera reframe").click();
      const original = await capturedOriginal(held);
      const pendingA = await readCameraCopy(page, base, sceneA);
      expect(pendingA.draft.awaiting?.source).toEqual(original.request.change.type === "upsert_camera_reframe"
        ? original.request.change.reframe : null);
      expect(pendingA.draft.awaiting?.revision).toBe(base.revision);
      expect(held.counts).toEqual({ uiAttempts: 1, originalRustSends: 0, settled: false, released: false });

      // Keep the same browser document: unlike reload, this genuine original
      // callback will arrive, while refs and visible fields have changed to B.
      await page.getByLabel("Production working scene", { exact: true }).selectOption({ value: sceneB.id });
      await expectOtherSceneDraftVisible(page, sceneB);
      const beforeB = await readCameraCopy(page, base, sceneB);
      expectExactCopy(preparedB, beforeB);
      expectSamePendingCopy(pendingA, await readCameraCopy(page, base, sceneA));
      expect(await service.project(base.id)).toEqual(base);
      expect(await service.history(base.id, 0)).toEqual(baseJournal);
      expect(rawApplies(service)).toHaveLength(applyCountBefore);

      const terminal = await held.drain();
      expect(terminal?.status).toBe("fulfilled");
      if (terminal?.status !== "fulfilled") throw new Error("The original Camera apply did not commit.");
      saved = terminal.project;
      expect(await observation.wait()).toEqual({ started: 1, fulfilled: 1, rejected: 0, settled: 1 });
      const exchange = terminalExchange(service, original.request.request_id);
      expect(exchange.response.ok).toBe(true);
      expect(saved.revision).toBe(base.revision + 1);
      await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + saved.revision);
      // The genuine ACK must settle the originating persisted copy, even while
      // another scope is selected. Poll its bytes, never product internals.
      await expect.poll(async () => (await readCameraCopy(page, base!, sceneA)).draft.awaiting).toBeNull();
      const acknowledgedA = await readCameraCopy(page, base, sceneA);
      const afterB = await readCameraCopy(page, base, sceneB);
      // A receipt is published through a new writer. Never consume the only
      // original recovery copy before its replacement and pointer are durable.
      expect(acknowledgedA.writerId).not.toBe(pendingA.writerId);
      expect(acknowledgedA.key).not.toBe(pendingA.key);
      const originalRawAfterAck = await page.evaluate(key => window.localStorage.getItem(key), pendingA.key);
      expect(originalRawAfterAck).toBe(pendingA.raw);
      expect(acknowledgedA.draft.baseRevision).toBe(saved.revision);
      const savedCamera = saved.production_design?.camera_reframes?.find(item => item.scene_id === sceneA.id);
      expect(savedCamera).toEqual(pendingA.draft.awaiting!.source);
      expect(acknowledgedA.draft.base).toEqual(savedCamera);
      expect(acknowledgedA.draft.fields).toEqual(pendingA.draft.fields);
      expectExactCopy(beforeB, afterB);
      await expectOtherSceneDraftVisible(page, sceneB);
      await expect(page.getByRole("region", { name: "Unconfirmed Camera submission", exact: true })).toHaveCount(0);
      expect(saved.scenes.find(item => item.id === sceneB.id)).toEqual(base.scenes.find(item => item.id === sceneB.id));
      const savedJournal = await service.history(base.id, 0);
      expect(savedJournal).toHaveLength(baseJournal.length + 1);
      expect(savedJournal[savedJournal.length - 1]?.change).toEqual(original.request.change);
      expect(await service.project(base.id)).toEqual(saved);
      expect(held.counts).toEqual({ uiAttempts: 1, originalRustSends: 1, settled: true, released: true });
      expect(rawApplies(service)).toHaveLength(applyCountBefore + 1);

      await page.getByLabel("Production working scene", { exact: true }).selectOption({ value: sceneA.id });
      await expect(page.getByRole("region", { name: "Unconfirmed Camera submission", exact: true })).toHaveCount(0);
      await expect(button(page, "Update camera reframe")).toBeVisible();
      await expect(page.getByLabel("End center X", { exact: true })).toBeEnabled();
      expect((await readCameraCopy(page, base, sceneA)).draft.awaiting).toBeNull();
      expectExactCopy(beforeB, await readCameraCopy(page, base, sceneB));
      await attachJson(info, "late-camera-ack-scene-isolation", {
        original: publicRequest(original), exchange, counts: held.counts,
        pendingA, originalRawAfterAck, acknowledgedA, preparedB, beforeB, afterB, saved, savedJournal,
        excludes: ["native Film pixels", "installed app", "human acceptance"],
      });
    } finally {
      if (held) {
        const terminal = await held.drain();
        if (terminal?.status === "fulfilled") saved = terminal.project;
        held.restore();
      }
      await service.finalizeClosedStore(info.outputPath("closed-camera-store"), saved ?? base,
        [], [() => page.close()]);
    }
  });
}
