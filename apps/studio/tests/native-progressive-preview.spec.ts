import {expect,test} from "@playwright/test";
import {fixtureBootstrap} from "../src/fixture";

test("rapid editorial seeks coalesce delayed native reads and settle on exact source frame",async({page})=>{
  const boot=structuredClone(fixtureBootstrap);
  boot.native_sdk.mode="tauri";
  const [first,second]=boot.project.scenes;
  const profile=boot.project.deliverables[0];
  const receipt={
    project_resource:"project:"+boot.project.id,
    generation:boot.project.generation,
    revision:boot.project.revision,
    deliverable_id:profile.id,
    frame_rate:{num:30,den:1},
    segments:[{
      segment_id:"source-bound-progressive-test",
      scene_ids:[first.id,second.id],frame_count:450,
      plan_ref:"synthetic-plan-only",fingerprint:"synthetic-fingerprint-only",
      job_ref:"synthetic-job-only",artifact:{},verification:{},
    }],
    preview:[{
      token:"synthetic-grant-only",
      segment_id:"source-bound-progressive-test",
      scene_ids:[first.id,second.id],
      frame_count:450,
    }],
  };
  const png="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4XyX4HwAGkAKKXzfbLwAAAABJRU5ErkJggg==";
  await page.addInitScript(({initialBoot,renderReceipt,encodedPng})=>{
    const w=window as unknown as {
      __TAURI_INTERNALS__:{invoke:(command:string,args?:Record<string,unknown>)=>Promise<unknown>};
      __READ_STATS__:{active:number;max:number;indices:number[]};
    };
    w.__READ_STATS__={active:0,max:0,indices:[]};
    w.__TAURI_INTERNALS__={
      invoke:async(command,args)=>{
        if(command==="bootstrap")return structuredClone(initialBoot);
        if(command==="issue_effect_grant")return {token:"synthetic-approval-only"};
        if(command==="render_motion_canvas")return structuredClone(renderReceipt);
        if(command==="preview_native_frame"){
          const request=args?.request as {token:string;frame_index:number;project_id:string;revision:number};
          if(request.token!=="synthetic-grant-only" ||
            request.project_id!==initialBoot.project.id ||
            request.revision!==initialBoot.project.revision)
            throw new Error("Grant and project source mismatch");
          w.__READ_STATS__.indices.push(request.frame_index);
          w.__READ_STATS__.active++;
          w.__READ_STATS__.max=Math.max(w.__READ_STATS__.max,w.__READ_STATS__.active);
          try {
            await new Promise(resolve=>setTimeout(resolve,380));
            return Uint8Array.from(atob(encodedPng),c=>c.charCodeAt(0));
          } finally {
            w.__READ_STATS__.active--;
          }
        }
        throw new Error("Unsupported synthetic Tauri command "+command);
      },
    };
  },{initialBoot:boot,renderReceipt:receipt,encodedPng:png});
  await page.goto("/");
  await page.getByRole("button",{name:"Deliver",exact:true}).click();
  await page.getByLabel(first.name+" narrative role").selectOption("hook");
  await page.getByLabel(first.name+" motion archetype").selectOption("statement");
  await page.getByLabel(second.name+" narrative role").selectOption("problem");
  await page.getByLabel(second.name+" motion archetype").selectOption("diagram_build");
  await page.getByRole("button",{name:"Render native segments"}).click();
  await expect(page.getByLabel("Native render evidence")).toContainText("NATIVE · PASS");
  await page.getByRole("button",{name:"Timeline",exact:true}).click();
  await page.getByRole("button",{name:"Show native frames"}).click();

  // Verify first actually-received PNG before rapid transport movement.
  const stage=page.getByRole("region",{name:"Verified native PNG frame preview"});
  await expect(stage.getByRole("img",{name:/Frame 1 \/ 450/})).toBeVisible();

  for(let index=0;index<6;index++)
    await page.getByRole("button",{name:"Step forward one frame"}).click();

  await expect(stage.getByRole("img",{name:/Frame 7 \/ 450/})).toBeVisible();
  const stats=await page.evaluate(()=>(window as unknown as {
    __READ_STATS__:{active:number;max:number;indices:number[]};
  }).__READ_STATS__);
  expect(stats.max).toBe(1);
  expect(stats.indices[0]).toBe(0);
  expect(stats.indices.at(-1)).toBe(6);
  // Some intermediate frames may have been sampled while clicking, but
  // a 10Hz transport is not permitted to launch concurrent native reads.
  expect(stats.indices.length).toBeLessThanOrEqual(7);
  await expect(stage).toContainText("Settled exact source PNG");
  await expect(stage).not.toContainText("Holding previously verified");

  const revision=await page.locator(".revision-chip").first().innerText();
  expect(revision).toBe("r"+boot.project.revision);
  await page.getByRole("button",{name:"Show design"}).click();
  await expect(stage).toHaveCount(0);
});
