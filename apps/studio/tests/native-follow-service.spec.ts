/** A real source transaction stores Follow, not the derived renderer keys. */
import { expect, test } from "@playwright/test";
import { ScopedChangeService } from "./support/scopedChangeService";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,"An exact-source StudioService binary is required");
test.describe.configure({timeout:90_000,retries:0});

test("saved Follow inherits translation at each playhead and retains original source on reopen",async({page},info)=>{
  const databasePath=info.outputPath("native-follow","source.sqlite3");
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
    const native=page.getByRole("region",{name:"Native linear position motion"});
    await native.getByLabel("Native move start X",{exact:true}).fill(String(target.x-80));
    await native.getByLabel("Native move start Y",{exact:true}).fill(String(target.y+25));
    await native.getByLabel("Native move end frame",{exact:true}).fill("30");
    await native.getByLabel("Native move easing",{exact:true}).selectOption("linear");
    await native.getByRole("button",{name:"Create native linear move",exact:true}).click();
    await expect(revision).toHaveText("r"+(original.revision+2));
    const animated=await service.project(original.id);
    const savedTarget=animated.scenes.find(item=>item.id===scene.id)!.nodes.find(node=>node.id===target.id)!;
    expect(savedTarget.keyframes).toHaveLength(4);
    await add.click();
    await expect(revision).toHaveText("r"+(original.revision+3));
    const created=await service.project(original.id);
    const follower=created.scenes.find(item=>item.id===scene.id)!.nodes.find(node=>
      !animated.scenes.find(item=>item.id===scene.id)!.nodes.some(before=>before.id===node.id))!;
    expect(follower.parent_id).toBeNull();
    const followerInspector=page.locator(".canvas-draft-inspector").filter({
      has:page.locator("#canvas-draft-"+follower.id+"-x"),
    });
    await expect(followerInspector).toHaveCount(1);
    await followerInspector.getByLabel("Canvas relation kind",{exact:true}).selectOption("follow");
    await followerInspector.getByLabel("Canvas relation target",{exact:true}).selectOption(target.id);
    await followerInspector.getByRole("button",{name:"Add canvas relation",exact:true}).click();
    await expect(revision).toHaveText("r"+(original.revision+4));
    const saved=await service.project(original.id);
    const nodes=saved.scenes.find(item=>item.id===scene.id)!.nodes;
    const linked=nodes.find(node=>node.id===follower.id)!;
    expect(linked).toEqual({...follower,relations:[{id:linked.relations[0].id,kind:"follow",target_id:target.id}]});
    expect(nodes.find(node=>node.id===target.id)).toEqual(savedTarget);
    const object=(name:string)=>page.getByRole("button",{name:"Canvas object "+name,exact:true});
    const x=(name:string)=>object(name).evaluate(el=>parseFloat((el as HTMLElement).style.left)*1920/100);
    const y=(name:string)=>object(name).evaluate(el=>parseFloat((el as HTMLElement).style.top)*1080/100);
    const playhead=page.getByLabel("Motion playhead",{exact:true});
    const verify=async(t:number)=>{
      await playhead.fill(String(t));
      await expect(object(follower.name)).toHaveAttribute("data-follow-resolved","true");
      await expect.poll(async()=>Math.abs((await x(follower.name))-(await x(target.name))-(follower.x-target.x))).toBeLessThan(.02);
      await expect.poll(async()=>Math.abs((await y(follower.name))-(await y(target.name))-(follower.y-target.y))).toBeLessThan(.02);
    };
    await verify(0);
    await expect.poll(async()=>Math.abs(await x(target.name)-(target.x-80))).toBeLessThan(.02);
    await verify(.5);
    await verify(1);
    await page.reload();
    await page.getByRole("button",{name:"Canvas",exact:true}).click();
    await page.locator(".tree-row").filter({hasText:scene.name}).click();
    await verify(.5);
    expect((await service.history(original.id,created.revision,8)).map(event=>event.change.type)).toEqual(["set_canvas_relations"]);
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
