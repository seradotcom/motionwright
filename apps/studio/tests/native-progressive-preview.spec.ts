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
      __READ_STATS__:{active:number;max:number;indices:number[];
        filmChecks:number;runtimeChecks:number;grants:number;renders:number};
    };
    w.__READ_STATS__={active:0,max:0,indices:[],
      filmChecks:0,runtimeChecks:0,grants:0,renders:0};
    w.__TAURI_INTERNALS__={
      invoke:async(command,args)=>{
        if(command==="bootstrap")return structuredClone(initialBoot);
        if(command==="production_runtime_status"){
          const request=args?.request as {project_id:string;expected_generation:string;expected_revision:number};
          if(request.project_id!==initialBoot.project.id
            || request.expected_generation!==initialBoot.project.generation
            || request.expected_revision!==initialBoot.project.revision)
            throw Error("Unscoped Semwright compatibility probe");
          w.__READ_STATS__.runtimeChecks++;
          return {
            status:"ready",reason:null,expected_version:"1.0.0",
            observed_version:"1.0.0",version_compatible:true,
            executable_sha256:"a".repeat(64),connection_identity:"b".repeat(64),
            pinned_revision:"c".repeat(40),
          };
        }
        if(command==="motion_canvas_preflight"){
          const request=args?.request as {project_id:string;generation:string;revision:number;deliverable_id:string};
          if(request.project_id!==initialBoot.project.id
            || request.generation!==initialBoot.project.generation
            || request.revision!==initialBoot.project.revision
            || request.deliverable_id!==renderReceipt.deliverable_id)
            throw Error("Unscoped Film projection check");
          w.__READ_STATS__.filmChecks++;
          return {
            project_resource:"project:"+request.project_id,
            generation:request.generation,revision:request.revision,
            deliverable_id:request.deliverable_id,
            verdict:"projection_ready",reason:null,segment_count:1,
            total_frames:450,segments:[{
              segment_id:"source-bound-progressive-test",
              scene_ids:initialBoot.project.scenes.slice(0,2).map(scene=>scene.id),
              frame_count:450,
            }],
          };
        }
        if(command==="issue_effect_grant"){
          if(w.__READ_STATS__.runtimeChecks<1 || w.__READ_STATS__.filmChecks<1)
            throw Error("One-time render grant requested without first-use checks");
          w.__READ_STATS__.grants++;
          return {token:"synthetic-approval-only"};
        }
        if(command==="render_motion_canvas"){
          if(w.__READ_STATS__.grants!==1)
            throw Error("Render invoked without exact one-time grant");
          w.__READ_STATS__.renders++;
          return structuredClone(renderReceipt);
        }
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
  const renderButton=page.getByRole("button",{name:"Render native segments"});
  await expect(renderButton).toBeDisabled();
  await page.getByRole("button",{name:"Check Film projection"}).click();
  await expect(page.getByLabel("First native production readiness"))
    .toContainText("ELIGIBLE FOR AUTHORIZED DISPATCH");
  await expect(renderButton).toBeEnabled();
  await renderButton.click();
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
  const authority=await page.evaluate(()=>(window as unknown as {
    __READ_STATS__:{filmChecks:number;runtimeChecks:number;grants:number;renders:number};
  }).__READ_STATS__);
  expect(authority.filmChecks).toBe(1);
  expect(authority.runtimeChecks).toBeGreaterThanOrEqual(2);
  expect(authority.grants).toBe(1);
  expect(authority.renders).toBe(1);
  expect(stats.indices[0]).toBe(0);
  expect(stats.indices.at(-1)).toBe(6);
  // Some intermediate frames may have been sampled while clicking, but
  // a 10Hz transport is not permitted to launch concurrent native reads.
  expect(stats.indices.length).toBeLessThanOrEqual(7);
  await expect(stage).toContainText("Settled exact source PNG");
  await expect(stage).not.toContainText("Holding previously verified");

  const revision=await page.locator(".revision-chip").first().innerText();
  expect(revision).toBe("r"+boot.project.revision);

  // Remove the source grant by switching profile WHILE a read is in flight.
  // Its later completion must never resurrect a PNG from the prior profile.
  await page.getByRole("button",{name:"Step forward one frame"}).click();
  await expect.poll(()=>page.evaluate(()=>(window as unknown as {
    __READ_STATS__:{active:number};
  }).__READ_STATS__.active)).toBe(1);
  await page.getByLabel("Editor preview timebase").selectOption(
    boot.project.deliverables[1].id,
  );
  await expect(stage).toHaveCount(0);
  await page.waitForTimeout(500);
  await expect(stage).toHaveCount(0);
  expect(await page.locator(".revision-chip").first().innerText()).toBe(revision);
});
