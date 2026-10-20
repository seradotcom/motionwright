import { writeFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { ScopedChangeService } from "./support/scopedChangeService";
import { cameraPointerDelta } from "../src/canvasCamera";
import type { Project } from "../src/types";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Real selected Rust StudioService and SQLite runner required.");
test.setTimeout(90_000);

test("rapid pointer move and release commits one exact Canvas transform without losing the final delta",
  async ({page},info)=>{
  const service=await ScopedChangeService.create({
    databasePath:info.outputPath("store","studio.sqlite3"),desktopInvokeFixture:true,
  });
  let initial: Project|undefined, final: Project|undefined;
  try {
    initial=(await service.fixtureBootstrap()).project;
    const scene=initial.scenes.find(value=>value.renderer==="motion-canvas"
      && value.nodes.some(node=>node.kind==="text"));
    if(!scene) throw new Error("An original saved Canvas scene is required.");
    const node=scene.nodes.find(value=>value.kind==="text")!;
    await service.attachFixturePage(page,initial.id);
    await page.goto("/");
    await page.getByRole("button",{name:"Canvas",exact:true}).click();
    await page.locator(".tree-row").filter({
      has:page.getByText(scene.name,{exact:true}),
    }).click();
    const actor=page.getByRole("button",{name:"Canvas object "+node.name,exact:true});
    await expect(actor).toBeVisible();
    const bounds=await actor.boundingBox();
    const stage=await page.locator(".canvas-stage").evaluate(element=>({width:element.clientWidth,height:element.clientHeight}));
    if(!bounds||stage.width<=0||stage.height<=0) throw new Error("Canvas geometry unavailable");
    const start={x:bounds.x+bounds.width/2,y:bounds.y+bounds.height/2};
    const delta={x:26,y:15};
    const expected=cameraPointerDelta(scene.camera,delta.x,delta.y,stage.width,stage.height);
    if(!expected) throw new Error("Invalid camera projection");
    await page.evaluate(()=>{
      const stageElement=document.querySelector(".canvas-stage");
      if(!stageElement) throw new Error("Canvas stage missing");
      stageElement.addEventListener("pointerdown",event=>{
        (window as Window & {__gesturePointerStart?:{x:number;y:number}}).__gesturePointerStart =
          {x:event.clientX,y:event.clientY};
      },{capture:true,once:true});
    });
    await page.mouse.move(start.x,start.y);
    await page.mouse.down();
    const actualStart=await page.evaluate(()=>
      (window as Window & {__gesturePointerStart?:{x:number;y:number}}).__gesturePointerStart);
    if(!actualStart) throw new Error("The browser did not record a real pointerdown.");
    // Both dispatched synchronously within one browser task: no opportunity
    // for a React render between the last pointermove and pointerup.
    await page.evaluate(({dx,dy,start})=>{
      const active=document.querySelector("button.canvas-object.selected") as HTMLElement|null;
      if(!active) throw new Error("The native pointerdown didn't select the object");
      const last=new PointerEvent("pointermove",{bubbles:true,pointerId:1,pointerType:"mouse",
        clientX:start.x+dx,clientY:start.y+dy,buttons:1});
      const up=new PointerEvent("pointerup",{bubbles:true,pointerId:1,pointerType:"mouse",
        clientX:last.clientX,clientY:last.clientY,buttons:0});
      active.dispatchEvent(last);active.dispatchEvent(up);
    },{dx:delta.x,dy:delta.y,start:actualStart});
    await page.mouse.up();
    await expect.poll(async()=>(await service.project(initial.id)).revision,{timeout:10_000})
      .toBe(initial.revision+1);
    final=await service.project(initial.id);
    const transformed=final.scenes.find(item=>item.id===scene.id)?.nodes.find(item=>item.id===node.id);
    expect(transformed).toBeDefined();
    if(!transformed) throw new Error("Object disappeared");
    expect(transformed.x).toBeCloseTo(node.x+expected.x,5);
    expect(transformed.y).toBeCloseTo(node.y+expected.y,5);
    const history=await service.history(initial.id,initial.revision);
    expect(history.map(e=>e.change.type)).toEqual(["transform_canvas_node"]);
    await service.close();await service.start();await page.reload();
    expect(await service.project(initial.id)).toEqual(final);
    expect(await service.history(initial.id,initial.revision)).toEqual(history);
    await writeFile(info.outputPath("gesture-outcome.json"),JSON.stringify({
      originalRevision:initial.revision,finalRevision:final.revision,
      sourceNodeId:node.id,sourceXY:[node.x,node.y],savedXY:[transformed.x,transformed.y],
      source:"browser-pointer-real-studioservice-sqlite",historyTypes:history.map(e=>e.change.type),
      excludes:["native-Film-pixels","installed-UI-acceptance","human-approval"],
    },null,2));
    await info.attach("gesture-outcome",{path:info.outputPath("gesture-outcome.json"),contentType:"application/json"});
  }finally{
    await service.finalizeClosedStore(info.outputPath("closed-store"),final ?? initial,[],[()=>page.close()]);
  }
});

test("pointercancel discards the transient delta and later pointerup cannot apply it",
 async ({page},info)=>{
 const service=await ScopedChangeService.create({databasePath:info.outputPath("store","studio.sqlite3"),desktopInvokeFixture:true});
 let initial:Project|undefined;
 try {
 initial=(await service.fixtureBootstrap()).project;
 const scene=initial.scenes.find(s=>s.renderer==="motion-canvas"&&s.nodes.length>0);
 if(!scene)throw Error("Missing original Canvas scene");
 const node=scene.nodes[0];
 await service.attachFixturePage(page,initial.id);await page.goto("/");
 await page.getByRole("button",{name:"Canvas",exact:true}).click();
 await page.locator(".tree-row").filter({has:page.getByText(scene.name,{exact:true})}).click();
 const actor=page.getByRole("button",{name:"Canvas object "+node.name,exact:true});
 await expect(actor).toBeVisible();
 const bounds=await actor.boundingBox();
 if(!bounds)throw Error("Canvas actor has no bounds");
 const start={x:bounds.x+bounds.width/2,y:bounds.y+bounds.height/2};
 await page.mouse.move(start.x,start.y);await page.mouse.down();
 await page.evaluate(({start})=>{
   const actor=document.querySelector("button.canvas-object.selected");
   if(!actor)throw Error("Missing selected pointer target");
   const data={bubbles:true,pointerId:1,pointerType:"mouse",clientX:start.x+30,clientY:start.y+15,buttons:1};
   actor.dispatchEvent(new PointerEvent("pointermove",data));
   actor.dispatchEvent(new PointerEvent("pointercancel",{...data,buttons:0}));
   actor.dispatchEvent(new PointerEvent("pointerup",{...data,buttons:0}));
 },{start});
 await page.mouse.up();
 await page.evaluate(()=>new Promise<void>(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r()))));
 expect(await service.project(initial.id)).toEqual(initial);
 expect(await service.history(initial.id,initial.revision)).toEqual([]);
 const requests=service.transcript.filter(e=>e.direction==="request"&&"command"in e.payload&&e.payload.command==="apply_change");
 expect(requests).toHaveLength(0);
 }finally{await service.finalizeClosedStore(info.outputPath("closed-store"),initial,[],[()=>page.close()]);}
});

test("quick release with Auto-key writes a single X/Y motion pair, not a base transform",
 async ({page},info)=>{
 const service=await ScopedChangeService.create({databasePath:info.outputPath("store","studio.sqlite3"),desktopInvokeFixture:true});
 let initial:Project|undefined,final:Project|undefined;
 try{
  initial=(await service.fixtureBootstrap()).project;
  const scene=initial.scenes.find(s=>s.renderer==="motion-canvas"&&s.nodes.some(n=>n.kind==="text"));
  if(!scene)throw Error("Missing original Canvas scene");
  const node=scene.nodes.find(n=>n.kind==="text")!;
  if(node.keyframes.length)throw Error("Expected original unkeyed test object");
  await service.attachFixturePage(page,initial.id);await page.goto("/");
  await page.getByRole("button",{name:"Canvas",exact:true}).click();
  await page.locator(".tree-row").filter({has:page.getByText(scene.name,{exact:true})}).click();
  await page.getByLabel("Auto-key position",{exact:true}).check();
  const actor=page.getByRole("button",{name:"Canvas object "+node.name,exact:true});
  await expect(actor).toBeVisible();
  const box=await actor.boundingBox();
  if(!box)throw Error("Canvas object missing bounds");
  const start={x:box.x+box.width/2,y:box.y+box.height/2};
  await page.evaluate(()=>{
    const stage=document.querySelector(".canvas-stage");
    stage?.addEventListener("pointerdown",e=>{
      (window as Window & {__gestureStart?:{x:number;y:number}}).__gestureStart={x:e.clientX,y:e.clientY};
    },{capture:true,once:true});
  });
  await page.mouse.move(start.x,start.y);await page.mouse.down();
  const actualStart=await page.evaluate(()=>
    (window as Window & {__gestureStart?:{x:number;y:number}}).__gestureStart);
  if(!actualStart)throw Error("Missing observed real pointerdown");
  await page.evaluate(({start})=>{
    const selected=document.querySelector("button.canvas-object.selected");
    if(!selected)throw Error("No authored Canvas selection");
    const move=new PointerEvent("pointermove",{bubbles:true,pointerId:1,pointerType:"mouse",
      clientX:start.x+18,clientY:start.y+8,buttons:1});
    selected.dispatchEvent(move);
    selected.dispatchEvent(new PointerEvent("pointerup",{bubbles:true,pointerId:1,pointerType:"mouse",
      clientX:move.clientX,clientY:move.clientY,buttons:0}));
  },{start:actualStart});
  await page.mouse.up();
  await expect.poll(async()=>(await service.project(initial!.id)).revision,{timeout:10_000})
   .toBe(initial.revision+1);
  final=await service.project(initial.id);
  const updated=final.scenes.find(s=>s.id===scene.id)?.nodes.find(n=>n.id===node.id);
  expect(updated).toBeDefined();
  if(!updated)throw Error("Unkeyed object disappeared");
  expect(updated.x).toBe(node.x);expect(updated.y).toBe(node.y);
  const keys=updated.keyframes.filter(k=>k.property==="x"||k.property==="y");
  expect(keys.map(k=>k.property).sort()).toEqual(["x","y"]);
  expect(keys.find(k=>k.property==="x")?.value).not.toBe(node.x);
  expect(keys.find(k=>k.property==="y")?.value).not.toBe(node.y);
  const history=await service.history(initial.id,initial.revision);
  expect(history.map(e=>e.change.type)).toEqual(["set_canvas_position_keyframe"]);
  await service.close();await service.start();await page.reload();
  expect(await service.project(initial.id)).toEqual(final);
  expect(await service.history(initial.id,initial.revision)).toEqual(history);
 }finally{await service.finalizeClosedStore(info.outputPath("closed-store"),final??initial,[],[()=>page.close()]);}
});

// The first three cases preserve quick-release and cancel regressions. These
// cases add an actual peer process and a product-reachable project read while
// the original pointer gesture remains open.
for (const autoKey of [false, true]) {
  for (const ending of ["release", "cancel"] as const) {
    test(`a ${autoKey ? "keyed" : "pose"} gesture cannot overwrite a peer after same-view reopen and ${ending}`, async ({ page }, info) => {
      const service = await ScopedChangeService.create({
        databasePath: info.outputPath("store", "interleaving.sqlite3"),
        desktopInvokeFixture: true,
      });
      let peer: ScopedChangeService | undefined;
      let initial: Project | undefined, winner: Project | undefined;
      try {
        initial = (await service.fixtureBootstrap()).project;
        const scene = initial.scenes.find(value => value.renderer === "motion-canvas"
          && value.nodes.some(node => node.kind === "text" && node.property_locks.length === 0
            && node.keyframes.length === 0));
        if (!scene) throw new Error("An editable unkeyed Canvas scene is required.");
        const node = scene.nodes.find(value => value.kind === "text"
          && value.property_locks.length === 0 && value.keyframes.length === 0)!;
        const beforeJournal = await service.history(initial.id, 0);
        peer = await service.peer();
        await service.attachFixturePage(page, initial.id);
        await page.goto("/");
        await page.getByRole("button", { name: "Canvas", exact: true }).click();
        await page.locator(".tree-row").filter({
          has: page.getByText(scene.name, { exact: true }),
        }).click();
        if (autoKey) await page.getByRole("checkbox", { name: "Auto-key position" }).check();
        const actor = page.getByRole("button", { name: "Canvas object " + node.name, exact: true });
        const handle = await actor.elementHandle();
        const bounds = await actor.boundingBox();
        if (!bounds || !handle) throw new Error("Canvas pointer target is not visible.");
        const start = { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 };
        await page.mouse.move(start.x, start.y);
        await page.mouse.down();
        await page.mouse.move(start.x + 22, start.y + 11, { steps: 4 });
        expect(await handle.evaluate(element => element.hasPointerCapture(1))).toBe(true);
        expect(await service.project(initial.id)).toEqual(initial);
        expect(await service.history(initial.id, 0)).toEqual(beforeJournal);

        winner = await peer.apply(initial, {
          type: "transform_canvas_node", scene_id: scene.id, node_id: node.id,
          transform: {
            x: node.x + 71, y: node.y + 43, width: node.width, height: node.height,
            rotation_deg: node.rotation_deg, opacity: node.opacity,
          },
        });
        const winnerJournal = await service.history(initial.id, 0);
        expect(winner.revision).toBe(initial.revision + 1);
        expect(winnerJournal).toHaveLength(beforeJournal.length + 1);

        // Focus and Enter are ordinary keyboard actions. Reopen calls the real
        // library service and existing App read coordinator. It preserves the
        // selected scene and Canvas component; no state or response is injected.
        await page.getByRole("button", { name: "Open project library", exact: true }).press("Enter");
        expect(await handle.evaluate(element => element.hasPointerCapture(1))).toBe(true);
        await page.getByRole("button", { name: "Reopen " + initial.title, exact: true }).press("Enter");
        await expect(page.getByRole("dialog")).not.toBeVisible();
        await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + winner.revision);
        expect(await handle.evaluate(element => element.isConnected)).toBe(true);
        await expect(page.getByRole("button", { name: "Canvas", exact: true })).toHaveAttribute("aria-current", "page");

        if (ending === "cancel") {
          await handle.dispatchEvent("pointercancel", {
            pointerId: 1, pointerType: "mouse", bubbles: true,
            clientX: start.x + 22, clientY: start.y + 11,
          });
        }
        await page.mouse.up();
        await page.evaluate(() => new Promise<void>(resolve =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
        expect(await service.project(initial.id)).toEqual(winner);
        expect(await service.history(initial.id, 0)).toEqual(winnerJournal);
        const attempts = service.transcript.filter(item => item.direction === "request"
          && "command" in item.payload && item.payload.command === "apply_change");
        expect(attempts).toHaveLength(0);
        await expect(page.getByRole("status").filter({ hasText: "Canvas gesture canceled" })).toBeVisible();
        await expect(page.getByLabel("Canvas X", { exact: true })).toHaveValue(String(node.x + 71));
        await expect(page.getByLabel("Canvas Y", { exact: true })).toHaveValue(String(node.y + 43));
        const edited = winner.scenes.find(value => value.id === scene.id)!.nodes.find(value => value.id === node.id)!;
        expect(edited.keyframes).toEqual(node.keyframes);

        await peer.close();
        await service.close();
        await service.start();
        await page.reload();
        expect(await service.project(initial.id)).toEqual(winner);
        expect(await service.history(initial.id, 0)).toEqual(winnerJournal);
        const evidence = info.outputPath("gesture-interleaving.json");
        await writeFile(evidence, JSON.stringify({
          source: "real browser controls and two StudioService processes sharing SQLite",
          publicationPath: "Open project library / Reopen current project",
          autoKey, ending, initial, beforeJournal, winner, winnerJournal,
          gestureApplyRequests: attempts.length,
          excludes: ["native Film pixels", "installed app", "human acceptance"],
        }, null, 2));
        await info.attach("gesture-interleaving", { path: evidence, contentType: "application/json" });
      } finally {
        await peer?.close();
        await service.finalizeClosedStore(info.outputPath("closed-store"), winner ?? initial,
          [], [() => page.close()]);
      }
    });
  }
}
