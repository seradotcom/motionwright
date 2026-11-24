/** Original Service/SQLite source cycle, without a new Native effect. */
import { expect, test } from "@playwright/test";
import { ScopedChangeService } from "./support/scopedChangeService";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,"An exact-source StudioService binary is required");
test.describe.configure({timeout:90_000,retries:0});

test("creates a static ordinary group and saves an unclipped editable child pose",async({page},info)=>{
  const databasePath=info.outputPath("static-native-group","source.sqlite3");
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
    await page.locator(".canvas-create-row").getByRole("button",{name:"group",exact:true}).click();
    await expect(revision).toHaveText("r"+(original.revision+1));
    const grouped=await service.project(original.id);
    const group=grouped.scenes.find(item=>item.id===scene.id)!.nodes.find(node=>node.kind==="group"&&!scene.nodes.some(before=>before.id===node.id))!;
    await page.locator(".canvas-create-row").getByRole("button",{name:"shape",exact:true}).click();
    await expect(revision).toHaveText("r"+(original.revision+2));
    const created=await service.project(original.id);
    const child=created.scenes.find(item=>item.id===scene.id)!.nodes.find(node=>node.parent_id===group.id)!;
    expect(child).toBeDefined();
    expect(child.x).toBe(group.x+(group.width-child.width)/2);
    expect(child.y).toBe(group.y+(group.height-child.height)/2);
    const transform = page.locator(".canvas-draft-inspector").filter({
      has: page.locator("#canvas-draft-" + child.id + "-x"),
    });
    await expect(transform).toHaveCount(1);
    await transform.getByLabel("Canvas X",{exact:true}).fill("1000");
    await transform.getByRole("button",{name:"Commit transform",exact:true}).click();
    await expect(revision).toHaveText("r"+(original.revision+3));
    const positioned=await service.project(original.id);
    const pose=page.getByRole("region",{name:"Native pose animation"});
    for(const [label,value] of [
      ["Pose start x","920"],["Pose start y","410"],["Pose start width","280"],
      ["Pose start height","160"],["Pose start rotation","-9"],["Pose end frame","30"],
    ]) await pose.getByLabel(label,{exact:true}).fill(value);
    await pose.getByLabel("Pose geometry curve",{exact:true}).selectOption("linear");
    await pose.getByRole("button",{name:"Create native pose animation",exact:true}).click();
    await expect(revision).toHaveText("r"+(positioned.revision+1));
    const saved=await service.project(original.id);
    const savedScene=saved.scenes.find(item=>item.id===scene.id)!;
    expect(savedScene.nodes.find(node=>node.id===group.id)).toEqual(group);
    const savedChild=savedScene.nodes.find(node=>node.id===child.id)!;
    expect(savedChild.parent_id).toBe(group.id);
    expect(savedChild.keyframes).toHaveLength(10);
    expect(savedChild.x).toBe(1000);
    const object=page.getByRole("button",{name:"Canvas object "+child.name,exact:true});
    await expect(object).toHaveCSS("clip-path","none");
    await page.reload();
    await page.getByRole("button",{name:"Canvas",exact:true}).click();
    await page.locator(".tree-row").filter({hasText:scene.name}).click();
    await page.locator(".canvas-tree-row").filter({hasText:child.name}).click();
    await expect(pose.getByLabel("Pose start x",{exact:true})).toHaveValue("920");
    await expect(object).toHaveCSS("clip-path","none");
    expect((await service.history(original.id,positioned.revision,8)).map(event=>event.change.type)).toEqual(["set_canvas_motion_keyframes"]);
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
