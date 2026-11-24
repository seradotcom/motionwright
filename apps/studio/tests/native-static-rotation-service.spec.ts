/** Static shape rotation is an ordinary saved transform, never extra source keys. */
import { expect, test } from "@playwright/test";
import { ScopedChangeService } from "./support/scopedChangeService";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,"An exact-source StudioService binary is required");
test.describe.configure({timeout:90_000,retries:0});

test("saved shape rotation persists without derived motion keys on original reopen",async({page},info)=>{
  const databasePath=info.outputPath("native-static-rotation","source.sqlite3");
  const service=await ScopedChangeService.create({databasePath,desktopInvokeFixture:true});
  let closed=false;
  try {
    const original=(await service.fixtureBootstrap()).project;
    const scene=original.scenes.find(item=>item.name==="Independent synthetic scene")!;
    expect(scene).toBeDefined();
    await service.attachFixturePage(page,original.id);
    await page.goto("/");
    await page.getByRole("button",{name:"Canvas",exact:true}).click();
    await page.locator(".tree-row").filter({hasText:scene.name}).click();
    const revision=page.locator(".command-bar .revision-chip");
    await page.locator(".canvas-create-row").getByRole("button",{name:"shape",exact:true}).click();
    await expect(revision).toHaveText("r"+(original.revision+1));
    const created=await service.project(original.id);
    const shape=created.scenes.find(item=>item.id===scene.id)!.nodes.find(node=>!scene.nodes.some(before=>before.id===node.id))!;
    const transform=page.locator(".canvas-draft-inspector").filter({has:page.locator("#canvas-draft-"+shape.id+"-x")});
    await expect(transform).toHaveCount(1);
    await transform.getByLabel("Canvas Rotation",{exact:true}).fill("-33");
    await transform.getByRole("button",{name:"Commit transform",exact:true}).click();
    await expect(revision).toHaveText("r"+(original.revision+2));
    const saved=await service.project(original.id);
    const actual=saved.scenes.find(item=>item.id===scene.id)!.nodes.find(node=>node.id===shape.id)!;
    expect(actual).toEqual({...shape,rotation_deg:-33});
    expect(actual.keyframes).toEqual([]);
    const object=page.getByRole("button",{name:"Canvas object "+shape.name,exact:true});
    await expect.poll(()=>object.evaluate(el=>(el as HTMLElement).style.transform)).toBe("rotate(-33deg)");
    const playhead=page.getByLabel("Motion playhead",{exact:true});
    await playhead.focus();
    await playhead.press("End");
    expect(Number(await playhead.inputValue())).toBeGreaterThan(0);
    await expect.poll(()=>object.evaluate(el=>(el as HTMLElement).style.transform)).toBe("rotate(-33deg)");
    await page.reload();
    await page.getByRole("button",{name:"Canvas",exact:true}).click();
    await page.locator(".tree-row").filter({hasText:scene.name}).click();
    await page.locator(".canvas-tree-row").filter({hasText:shape.name}).click();
    await expect(transform).toHaveCount(1);
    await expect(transform.getByLabel("Canvas Rotation",{exact:true})).toHaveValue("-33");
    expect((await service.history(original.id,created.revision,8)).map(event=>event.change.type)).toEqual(["transform_canvas_node"]);
    await page.close();
    expect(await service.rawRequest({command:"prepare_read_only_handoff",args:null})).toMatchObject({ok:true,result:{read_only_handoff:true,journal_mode:"delete"}});
    await service.close();closed=true;
    expect(service.lifecycleTaints).toEqual([]);
    const reader=await ScopedChangeService.createReadOnly({binaryPath:service.binaryPath,databasePath,seedFixture:false,desktopInvokeFixture:false});
    try {expect(await reader.project(original.id)).toEqual(saved);}
    finally {await reader.close();}
  } finally {
    if(!page.isClosed()) await page.close();
    if(!closed) await service.close();
  }
});
