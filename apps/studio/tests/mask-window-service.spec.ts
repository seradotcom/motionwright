import {randomUUID} from "node:crypto";
import {expect,test} from "@playwright/test";
import {ScopedChangeService} from "./support/scopedChangeService";
import {proposalButton as button} from "./support/scopedPaintMotionJourney";
import {undoArrangement} from "./support/canvasArrangementJourney";
import type {CanvasNode} from "../src/types";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,"Requires an exact-source StudioService binary.");
test.describe.configure({timeout:180_000,retries:0});

test("Mask window preserves its real Clip, reviewable original track and exact SQLite reopen and undo",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("service","mask-window.sqlite3"),desktopInvokeFixture:true});
  let finalized=false;
  try{
    let project=(await service.fixtureBootstrap()).project;
    for(const lock of [...project.locks])project=await service.apply(project,{type:"remove_lock",lock_id:lock.id});
    // This dedicated test source uses all actual output profiles and a fresh
    // original scene; no desktop render result or project mutation is mocked.
    for(const profile of [...project.deliverables]){
      if(profile.framing_strategy!=="replan"||profile.frame_rate.num!=="30"||profile.frame_rate.den!=="1"||profile.included_scene_ids.length)
        project=await service.apply(project,{type:"upsert_deliverable",profile:{...profile,included_scene_ids:[],protected_scene_ids:[],
          framing_strategy:"replan",crop_approved:false,frame_rate:{num:"30",den:"1"}}});
    }
    const template=project.scenes[0].nodes[0],clipId=randomUUID(),childId=randomUUID(),peerId=randomUUID();
    expect(template).toBeDefined();
    project=await service.apply(project,{type:"add_scene",name:"Original mask window",objective:"Reveal original shape through static Clip",duration_seconds:6});
    const sceneId=project.scenes.at(-1)!.id;
    const style={...template.style,fill:"#86C8D1",stroke:null,stroke_width:0,font_family:null,font_size:null,font_weight:null,line_height:null,blend_mode:"normal" as const};
    const clip:CanvasNode={...template,id:clipId,name:"Original Clip",kind:"clip_group",x:800,y:340,width:320,height:240,
      parent_id:null,rotation_deg:0,opacity:1,text:null,coordinate_space:"project_pixels",z_index:0,style:{...style,fill:null},
      relations:[],property_locks:[],keyframes:[]};
    const child:CanvasNode={...clip,id:childId,name:"Original clipped child",kind:"rectangle",parent_id:clipId,
      x:880,y:410,width:160,height:100,z_index:1,style};
    const peer:CanvasNode={...child,id:peerId,name:"Original peer",parent_id:null,x:1400,y:450,z_index:3};
    for(const node of [clip,child,peer])project=await service.apply(project,{type:"add_canvas_node",scene_id:sceneId,node});
    const base=project;
    const baseScene=base.scenes.find(item=>item.id===sceneId)!;
    await service.attachFixturePage(page,base.id);await page.goto("/");
    const open=async(viaLibrary:boolean)=>{
      await button(page,"Production").click();
      await page.getByLabel("Production working scene",{exact:true}).selectOption(sceneId);
      if(viaLibrary){
        await button(page,"Craft library").click();
        await page.getByLabel("Craft family",{exact:true}).selectOption("transition");
        await page.getByRole("button",{name:/^Mask window/}).click();
        await button(page,"Open authoring editor").click();
      }else await button(page,"Scoped changes").click();
      return page.getByRole("region",{name:"Creative production workstation",exact:true});
    };
    let editor=await open(true);
    const panel=editor.locator("details").filter({has:page.locator("summary").filter({hasText:/^Mask window$/})});
    await expect(panel).toHaveCount(1);
    await panel.locator("summary").click();
    await panel.getByLabel("Mask window child",{exact:true}).selectOption(childId);
    await expect(panel.getByRole("button",{name:"Stage mask window",exact:true})).toBeEnabled();
    await panel.getByRole("button",{name:"Stage mask window",exact:true}).click();
    await expect(editor.getByRole("region",{name:"Proposed edits",exact:true}).getByLabel(/Remove proposed edit/)).toHaveCount(1);
    await editor.getByRole("button",{name:"Preview scoped changes",exact:true}).click();
    await expect(editor.getByText("Editorial diff prepared",{exact:true})).toBeVisible();
    const after=editor.getByRole("img",{name:"After scoped changes",exact:true});
    const childGroup=after.locator('[data-canvas-node-id="'+childId+'"]');
    await expect(childGroup).toHaveAttribute("data-parent-id",clipId);
    await expect(childGroup).toHaveAttribute("opacity","1");
    const clipRef=await childGroup.locator("..").getAttribute("clip-path");
    expect(clipRef).toMatch(/^url\(#.+\)$/);
    const clipDomId=clipRef!.slice(5,-1);
    await expect(after.locator('clipPath[id="'+clipDomId+'"] rect')).toHaveAttribute("x","800");
    for(const [time,x] of [["0","624"],["0.5","624"],["1","752"],["1.5","880"]]){
      await editor.getByLabel("Comparison playhead",{exact:true}).fill(time);
      await expect(childGroup.locator("rect")).toHaveAttribute("x",x);
      await expect(childGroup).toHaveAttribute("opacity","1");
      await expect(childGroup.locator("..")).toHaveAttribute("clip-path",clipRef!);
    }
    expect(await service.project(base.id)).toEqual(base);
    expect(await service.history(base.id,base.revision,8)).toEqual([]);
    await editor.getByRole("button",{name:"Apply as one revision",exact:true}).click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(base.revision+1));
    const saved=await service.project(base.id),savedScene=saved.scenes.find(item=>item.id===sceneId)!;
    expect(savedScene.nodes[0]).toEqual(baseScene.nodes[0]);expect(savedScene.nodes[2]).toEqual(baseScene.nodes[2]);
    expect({...savedScene.nodes[1],keyframes:[]}).toEqual(baseScene.nodes[1]);
    expect(savedScene.nodes[1].keyframes).toHaveLength(15);
    await service.close();await service.start();await page.reload();
    editor=await open(false);
    expect(await service.project(base.id)).toEqual(saved);
    await expect(editor.getByRole("button",{name:"Apply as one revision",exact:true})).toBeDisabled();
    await undoArrangement(page,saved.revision+1);
    const final=await service.project(base.id);
    expect(final.scenes.find(item=>item.id===sceneId)?.nodes).toEqual(baseScene.nodes);
    expect((await service.history(base.id,base.revision,8)).map(event=>event.change.type)).toEqual(["apply_creative_patch","undo_creative_patch"]);
    const closure=await service.finalizeClosedStore(info.outputPath("closed-store"),final,[],[async()=>{await page.close();}]);
    finalized=true;expect(closure.oracle?.project).toEqual(final);expect(service.lifecycleTaints).toEqual([]);
  }finally{
    if(!page.isClosed())await page.close();
    if(!finalized)await service.close();
  }
});
