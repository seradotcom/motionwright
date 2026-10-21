import {expect,test} from "./support/selectedSyntheticProject";
import {fixtureBootstrap} from "../src/fixture";

test("Canvas writes one atomic cubic-out X/Y move through the saved native bridge",async({page})=>{
  const boot=structuredClone(fixtureBootstrap);
  boot.native_sdk.mode="tauri";
  const scene=boot.project.scenes[0];
  scene.beats=[];
  const node=scene.nodes[0],profile=boot.project.deliverables[0];
  await page.addInitScript(({initialBoot})=>{
    const mock=window as unknown as {
      __TAURI_INTERNALS__:{invoke:(command:string,args?:Record<string,unknown>)=>Promise<unknown>};
      __CUBIC_NATIVE_REQUESTS__:Array<Record<string,unknown>>;
    };
    let source=structuredClone(initialBoot.project);
    mock.__CUBIC_NATIVE_REQUESTS__=[];
    mock.__TAURI_INTERNALS__={
      invoke:async(command,args)=>{
        if (command === "bootstrap" || command === "project_projection_head" || command === "open_project") {
          return window.__SYNTHETIC_PROJECT_READ__(command, args, {...initialBoot,project:structuredClone(source)});
        }
        if(command==="issue_effect_grant")return {token:"synthetic-only-grant"};
        if(command==="apply_change"){
          const request=args?.request as {
            project_id:string;generation:string;revision:number;effect_grant:string;
            change:{type:string;scene_id:string;node_id:string;deliverable_id:string;
              start_x:number;start_y:number;end_frame:number};
          };
          if(request.change.type!=="set_canvas_out_cubic_position_motion")
            throw Error("The native source edit was replaced with another operation.");
          mock.__CUBIC_NATIVE_REQUESTS__.push(request as unknown as Record<string,unknown>);
          if(request.revision!==source.revision || request.generation!==source.generation)
            throw Error("Stale native creative revision");
          const scene=source.scenes.find(s=>s.id===request.change.scene_id);
          const object=scene?.nodes.find(n=>n.id===request.change.node_id);
          if(!object || object.keyframes.length || scene?.beats.length)
            throw Error("Native cubic move cannot overwrite authored keys");
          const end={num:"1",den:"1"};
          object.keyframes=[
            {at:{num:"0",den:"1"},property:"x",value:request.change.start_x,interpolation:"linear"},
            {at:{num:"0",den:"1"},property:"y",value:request.change.start_y,interpolation:"linear"},
            {at:end,property:"x",value:object.x,interpolation:"ease_out_cubic"},
            {at:end,property:"y",value:object.y,interpolation:"ease_out_cubic"},
          ];
          source.revision++;
          return structuredClone(source);
        }
        throw Error("Synthetic UI test cannot invoke "+command);
      },
    };
  },{initialBoot:boot});

  await page.goto("/");
  await page.getByRole("button",{name:"Canvas",exact:true}).click();
  const region=page.getByRole("region",{name:"Native linear position motion"});
  await expect(region.getByRole("button",{name:"Create native linear move"})).toBeVisible();
  await page.getByLabel("Native move easing").selectOption("ease_out_cubic");
  const action=region.getByRole("button",{name:"Create native ease-out cubic move"});
  const beforeRevision=await page.locator(".revision-chip").first().innerText();
  const beforeX=await page.getByLabel("Canvas X").inputValue();
  const beforeY=await page.getByLabel("Canvas Y").inputValue();
  await page.getByLabel("Native move start X").fill(String(node.x-80));
  await page.getByLabel("Native move start Y").fill(String(node.y+25));
  await page.getByLabel("Native move end frame").fill("30");
  await expect(action).toBeEnabled();
  await action.click();
  await expect(page.locator(".motion-keyframe-row")).toHaveCount(4);
  await expect(page.getByLabel("Canvas X")).toHaveValue(beforeX);
  await expect(page.getByLabel("Canvas Y")).toHaveValue(beforeY);
  await expect(page.locator(".revision-chip").first()).not.toHaveText(beforeRevision);
  await expect(action).toBeDisabled();
  await expect(page.locator(".motion-keyframe-row").last()).toContainText("ease out cubic");
  const requests=await page.evaluate(()=>
    (window as unknown as {__CUBIC_NATIVE_REQUESTS__:Array<Record<string,unknown>>})
      .__CUBIC_NATIVE_REQUESTS__);
  expect(requests).toHaveLength(1);
  expect(requests[0]).toMatchObject({
    project_id:boot.project.id,
    generation:boot.project.generation,
    revision:boot.project.revision,
    change:{
      type:"set_canvas_out_cubic_position_motion",
      scene_id:scene.id,
      node_id:node.id,
      deliverable_id:profile.id,
      start_x:node.x-80,start_y:node.y+25,end_frame:30,
    },
  });
  expect(requests[0]).not.toHaveProperty("path");
  expect(requests[0]).not.toHaveProperty("native_render");
});

test("browser demo never pretends exact native Film motion is already proven",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Canvas",exact:true}).click();
  const panel=page.getByRole("region",{name:"Native linear position motion"});
  await page.getByLabel("Native move easing").selectOption("ease_out_cubic");
  await expect(panel.getByRole("button",{name:"Create native ease-out cubic move"})).toBeDisabled();
  await expect(panel).toContainText("Film preflight still verifies native framing");
});
