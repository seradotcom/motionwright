/** The saved semantic relationship, not the editorial positions, is persisted. */
import { expect, test } from "@playwright/test";
import { ScopedChangeService } from "./support/scopedChangeService";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,"An exact-source StudioService binary is required");
test.describe.configure({timeout:90_000,retries:0});

test("saved static alignment follows target edits and survives original SQLite reopen",async({page},info)=>{
  const databasePath=info.outputPath("static-alignment","source.sqlite3");
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
    const add=page.locator(".canvas-create-row").getByRole("button",{name:"shape",exact:true});
    await add.click();
    await expect(revision).toHaveText("r"+(original.revision+1));
    const first=await service.project(original.id);
    const target=first.scenes.find(item=>item.id===scene.id)!.nodes.find(node=>!scene.nodes.some(before=>before.id===node.id))!;
    await add.click();
    await expect(revision).toHaveText("r"+(original.revision+2));
    const second=await service.project(original.id);
    const follower=second.scenes.find(item=>item.id===scene.id)!.nodes.find(node=>
      !first.scenes.find(item=>item.id===scene.id)!.nodes.some(before=>before.id===node.id))!;
    expect(target.parent_id).toBeNull();expect(follower.parent_id).toBeNull();
    expect(follower.x).not.toBe(target.x);
    const inspector=(id:string)=>page.locator(".canvas-draft-inspector").filter({
      has:page.locator("#canvas-draft-"+id+"-x"),
    });
    const followerInspector=inspector(follower.id);
    await expect(followerInspector).toHaveCount(1);
    await followerInspector.getByLabel("Canvas relation kind",{exact:true}).selectOption("align_left");
    await followerInspector.getByLabel("Canvas relation target",{exact:true}).selectOption(target.id);
    await followerInspector.getByRole("button",{name:"Add canvas relation",exact:true}).click();
    await expect(revision).toHaveText("r"+(original.revision+3));
    const related=await service.project(original.id);
    const linked=related.scenes.find(item=>item.id===scene.id)!.nodes.find(node=>node.id===follower.id)!;
    expect(linked).toEqual({...follower,relations:[{id:linked.relations[0].id,kind:"align_left",target_id:target.id}]});
    const object=(name:string)=>page.getByRole("button",{name:"Canvas object "+name,exact:true});
    const left=(name:string)=>object(name).evaluate(el=>(el as HTMLElement).style.left);
    await expect(object(follower.name)).toHaveAttribute("data-alignment-resolved","true");
    await expect.poll(()=>left(follower.name)).toBe(await left(target.name));
    await page.locator(".canvas-tree-row").filter({hasText:target.name}).click();
    const newX=target.x+150;
    const targetInspector=inspector(target.id);
    await expect(targetInspector).toHaveCount(1);
    await targetInspector.getByLabel("Canvas X",{exact:true}).fill(String(newX));
    await targetInspector.getByRole("button",{name:"Commit transform",exact:true}).click();
    await expect(revision).toHaveText("r"+(original.revision+4));
    const saved=await service.project(original.id);
    const nodes=saved.scenes.find(item=>item.id===scene.id)!.nodes;
    expect(nodes.find(node=>node.id===target.id)!.x).toBe(newX);
    expect(nodes.find(node=>node.id===follower.id)).toEqual(linked);
    await expect.poll(()=>left(follower.name)).toBe(await left(target.name));
    await page.reload();
    await page.getByRole("button",{name:"Canvas",exact:true}).click();
    await page.locator(".tree-row").filter({hasText:scene.name}).click();
    await expect(object(follower.name)).toHaveAttribute("data-alignment-resolved","true");
    await expect.poll(()=>left(follower.name)).toBe(await left(target.name));
    expect((await service.history(original.id,second.revision,8)).map(event=>event.change.type))
      .toEqual(["set_canvas_relations","transform_canvas_node"]);
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
