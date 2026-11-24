/** UI -> original Service/SQLite -> close -> independent read-only reopen.
 * This source cycle does not claim a Native render or pixel acceptance. */
import { expect, test } from "@playwright/test";
import { ScopedChangeService } from "./support/scopedChangeService";
import type { CanvasNode } from "../src/types";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN, "An exact-source StudioService binary is required");
test.describe.configure({timeout:120_000,retries:0});

test("saves nested Clip Group Clip composition and continues the same child pose after reopen", async ({page}, info) => {
  const databasePath=info.outputPath("nested-composition","source.sqlite3");
  const service=await ScopedChangeService.create({databasePath,desktopInvokeFixture:true});
  let closed=false;
  try {
    const original=(await service.fixtureBootstrap()).project;
    const scene=original.scenes.find(item=>item.name==="Independent synthetic scene")!;
    await service.attachFixturePage(page,original.id);
    await page.goto("/");
    await page.getByRole("button",{name:"Canvas",exact:true}).click();
    await page.locator(".tree-row").filter({hasText:scene.name}).click();
    const revision=page.locator(".command-bar .revision-chip");
    let expectedRevision=original.revision;
    const savedScene=async()=>(await service.project(original.id)).scenes.find(item=>item.id===scene.id)!;
    const create=async(kind:"group"|"clip_group"|"shape")=>{
      const before=new Set((await savedScene()).nodes.map(node=>node.id));
      await page.locator(".canvas-create-row").getByRole("button",{name:kind.replace("_"," "),exact:true}).click();
      await expect(revision).toHaveText("r"+(++expectedRevision));
      return (await savedScene()).nodes.find(node=>!before.has(node.id))!;
    };
    const inspector=(node:CanvasNode)=>page.locator(".canvas-draft-inspector").filter({
      has:page.locator('[id="canvas-draft-'+node.id+'-x"]'),
    });
    const reparent=async(node:CanvasNode,parent:CanvasNode)=>{
      await page.locator(".canvas-tree-row").filter({hasText:node.name}).click();
      const panel=inspector(node);
      await expect(panel).toHaveCount(1);
      await panel.getByLabel("Canvas parent",{exact:true}).selectOption(parent.id);
      await panel.getByLabel("Canvas reparent preservation",{exact:true}).selectOption("world");
      await panel.getByRole("button",{name:"Commit hierarchy",exact:true}).click();
      await expect(revision).toHaveText("r"+(++expectedRevision));
      const saved=(await savedScene()).nodes.find(item=>item.id===node.id)!;
      expect([saved.x,saved.y,saved.width,saved.height]).toEqual([node.x,node.y,node.width,node.height]);
      expect(saved.parent_id).toBe(parent.id);
      return saved;
    };
    const outer=await create("clip_group");
    const middle=await reparent(await create("group"),outer);
    let inner=await reparent(await create("clip_group"),middle);
    const innerPanel=inspector(inner);
    for(const [label,value] of [
      ["Canvas X",outer.x+100],["Canvas Y",outer.y+70],
      ["Canvas W",outer.width-150],["Canvas H",outer.height-100],
    ] as const) await innerPanel.getByLabel(label,{exact:true}).fill(String(value));
    await innerPanel.getByRole("button",{name:"Commit transform",exact:true}).click();
    await expect(revision).toHaveText("r"+(++expectedRevision));
    inner=(await savedScene()).nodes.find(node=>node.id===inner.id)!;
    const child=await create("shape");
    expect(child.parent_id).toBe(inner.id);
    const row=page.locator(".canvas-tree-row").filter({hasText:child.name});
    await expect(row).toHaveAttribute("data-tree-depth","3");
    const pose=page.getByRole("region",{name:"Native pose animation"});
    for(const [label,value] of [
      ["Pose start x",child.x-40],["Pose start y",child.y],["Pose start width",child.width*0.8],
      ["Pose start height",child.height*0.8],["Pose start rotation",-10],["Pose end frame",30],
    ] as const) await pose.getByLabel(label,{exact:true}).fill(String(value));
    await pose.getByLabel("Pose geometry curve",{exact:true}).selectOption("ease_out_cubic");
    await pose.getByLabel("Pose opacity",{exact:true}).selectOption("in_out");
    await pose.getByLabel("Pose fade end frame",{exact:true}).fill("15");
    await pose.getByLabel("Pose fade-out start frame",{exact:true}).fill("30");
    await pose.getByLabel("Pose fade-out end frame",{exact:true}).fill("45");
    await pose.getByLabel("Pose fade-out curve",{exact:true}).selectOption("linear");
    await pose.getByRole("button",{name:"Create native pose animation",exact:true}).click();
    await expect(revision).toHaveText("r"+(++expectedRevision));
    const saved=await service.project(original.id);
    const savedNodes=saved.scenes.find(item=>item.id===scene.id)!.nodes;
    expect(savedNodes.find(node=>node.id===child.id)?.keyframes).toHaveLength(14);
    for(const container of [outer,middle,inner]) expect(savedNodes.find(node=>node.id===container.id)).toEqual(container);
    const object=page.getByRole("button",{name:"Canvas object "+child.name,exact:true});
    await expect(object).not.toHaveCSS("clip-path","none");
    await page.reload();
    await page.getByRole("button",{name:"Canvas",exact:true}).click();
    await page.locator(".tree-row").filter({hasText:scene.name}).click();
    await row.click();
    await expect(row).toHaveAttribute("data-tree-depth","3");
    await expect(pose.getByLabel("Pose start x",{exact:true})).toHaveValue(String(child.x-40));
    await expect(pose.getByLabel("Pose opacity",{exact:true})).toHaveValue("in_out");
    await expect(pose.getByLabel("Pose fade end frame",{exact:true})).toHaveValue("15");
    await expect(pose.getByLabel("Pose fade-out start frame",{exact:true})).toHaveValue("30");
    await expect(pose.getByLabel("Pose fade-out end frame",{exact:true})).toHaveValue("45");
    await expect(pose.getByLabel("Pose fade-out curve",{exact:true})).toHaveValue("linear");
    await expect(object).not.toHaveCSS("clip-path","none");
    await page.close();
    expect(await service.rawRequest({command:"prepare_read_only_handoff",args:null})).toMatchObject({
      ok:true,result:{read_only_handoff:true,journal_mode:"delete"},
    });
    await service.close();closed=true;
    expect(service.lifecycleTaints).toEqual([]);
    const reader=await ScopedChangeService.createReadOnly({binaryPath:service.binaryPath,databasePath,
      seedFixture:false,desktopInvokeFixture:false});
    try {expect(await reader.project(original.id)).toEqual(saved);} finally {await reader.close();}
  } finally {
    if(!page.isClosed()) await page.close();
    if(!closed) await service.close();
  }
});
