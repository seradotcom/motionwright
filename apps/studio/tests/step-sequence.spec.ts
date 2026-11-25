import {expect,test} from "@playwright/test";

test("StepSequence is an editable persisted native procedure card across 16:9, 9:16, 1:1",async({page},testInfo)=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Add scene",exact:true}).click();
  await page.getByLabel("New scene name").fill("Step 02 / Inspect");
  await page.getByRole("button",{name:"Add",exact:true}).click();
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByLabel("Production working scene").selectOption({label:"Step 02 / Inspect"});
  const layout=page.getByLabel("Component layout");
  await layout.selectOption("step_sequence");
  await expect(page.getByRole("heading",{name:"StepSequence"})).toBeVisible();
  await page.getByLabel("Step heading").fill("Inspect the intended result");
  await page.getByLabel("What happens at this step").fill(
    "Read the real project state and confirm what changed before approving another action."
  );
  await page.getByLabel("Step index (e.g. 01 / 03)").fill("02 / 04");

  for(const aspect of ["16:9","9:16","1:1"]) {
    await page.getByRole("button",{name:aspect,exact:true}).click();
    const preview=page.getByRole("img",{name:new RegExp("StepSequence "+aspect)});
    // SVG wraps lines into independent tspans; textContent does not insert
    // a space between lines even though the source text contains it.
    await expect(preview).toContainText(/Inspect the\s*intended\s*result/);
    await expect(preview).toContainText("02 / 04");
    await expect(preview).toContainText("NOT AN OBSERVED WORKFLOW");
    await page.screenshot({
      path:testInfo.outputPath("step-sequence-"+aspect.replace(":","-")+".png"),
      fullPage:true,
    });
  }

  const header=page.locator(".production-workspace-header p");
  const before=Number((await header.innerText()).match(/revision (\d+)/)?.[1]);
  expect(Number.isInteger(before)).toBe(true);
  await page.getByRole("button",{name:"Add to scene",exact:true}).click();
  await expect(header).toContainText("revision "+(before+1));
  await page.getByRole("button",{name:"Production plan",exact:true}).click();
  await page.getByRole("button",{name:"Components",exact:true}).click();
  await expect(layout).toHaveValue("step_sequence");
  await page.getByRole("button",{name:"Edit objects",exact:true}).click();
  await expect(page.getByRole("button",{name:"Canvas object StepSequence / headline",exact:true}))
    .toContainText("Inspect the intended result");
  await expect(page.getByRole("button",{name:"Canvas object StepSequence / disclosure",exact:true}))
    .toContainText("NOT AN OBSERVED WORKFLOW");
});

test("StepSequence invalid copy fails closed without creating nodes or incrementing project revision",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByLabel("Component layout").selectOption("step_sequence");
  await expect(page.getByRole("heading",{name:"StepSequence"})).toBeVisible();
  const before=await page.locator(".production-workspace-header p").innerText();
  await page.getByLabel("Step heading").fill("X".repeat(24));
  await expect(page.locator(".production-inspector [role=alert]")).toContainText("wider");
  await expect(page.getByRole("button",{name:"Add to scene",exact:true})).toBeDisabled();
  await expect(page.locator(".production-study text")).toHaveCount(0);
  await expect(page.locator(".production-workspace-header p")).toHaveText(before);
  await page.getByLabel("Component layout").selectOption("product_hero_reveal");
  await expect(page.getByRole("heading",{name:"ProductHeroReveal"})).toBeVisible();
});

test("delivery StepSequence preserves its original index and disclosure while reflowing local copy",async({page})=>{
  const button=(name:string)=>page.getByRole("button",{name,exact:true});
  await page.goto("/");
  await button("Add scene").click();
  await page.getByLabel("New scene name").fill("Original step variant");
  await button("Add").click();
  const open=async()=>{
    await button("Production").click();
    await page.getByLabel("Production working scene",{exact:true}).selectOption({label:"Original step variant"});
    await button("Components").click();
  };
  await open();
  await page.getByLabel("Component layout",{exact:true}).selectOption("step_sequence");
  await page.getByLabel("Step heading",{exact:true}).fill("Inspect");
  await page.getByLabel("What happens at this step",{exact:true}).fill("Retain the original source.");
  await page.getByLabel("Step index (e.g. 01 / 03)",{exact:true}).fill("02 / 04");
  await page.getByLabel("Animate entrance",{exact:true}).uncheck();
  await button("16:9").click();
  await expect(button("Add to scene")).toBeEnabled();
  await button("Add to scene").click();
  await expect(button("Update component")).toBeVisible();
  const read=async()=>page.evaluate(async()=>{
    const path="/src/api.ts";return (await (await import(/* @vite-ignore */ path)).bootstrap()).project as import("../src/types").Project;
  });
  const original=await read();
  const scene=original.scenes.find(row=>row.name==="Original step variant")!;
  const heading=scene.nodes.find(node=>node.name==="StepSequence / headline")!;
  const body=scene.nodes.find(node=>node.name==="StepSequence / body")!;
  const index=scene.nodes.find(node=>node.name==="StepSequence / wordmark")!;
  const notice=scene.nodes.find(node=>node.name==="StepSequence / disclosure")!;
  const profile=original.deliverables[0];
  await button("Deliver").click();
  await page.locator('.delivery-profile-row[data-profile-id="'+profile.id+'"]').click();
  for(const node of [index,notice]){
    const input=page.getByLabel("Delivery text for "+node.name+" in "+scene.name,{exact:true});
    await expect(input).toHaveAttribute("readonly","");
    await button("Retain exact source text for "+node.name+" in "+scene.name).click();
    await expect(input).toHaveValue(node.text!);
  }
  const title="Revisa la fuente",copy="Conserva el origen y revisa cada detalle del material.";
  await page.getByLabel("Delivery text for "+heading.name+" in "+scene.name,{exact:true}).fill(title);
  await page.getByLabel("Delivery text for "+body.name+" in "+scene.name,{exact:true}).fill(copy);
  await expect(button("Save profile")).toBeEnabled();
  await button("Save profile").click();
  await expect(button("Save profile")).toBeDisabled();
  await open();
  await page.getByLabel("Editor preview timebase",{exact:true}).selectOption(profile.id);
  await button("9:16").click();
  await button("Settled").click();
  const study=page.getByRole("img",{name:/^StepSequence 9:16/});
  const selected=study.locator('[data-canvas-node-id="'+body.id+'"] > text');
  await expect(selected).toHaveAttribute("data-text-origin","profile");
  const lines=await selected.locator("tspan").allTextContents();
  expect(lines.length).toBeGreaterThan(1);expect(lines.join(" ")).toBe(copy);
  await expect(study.locator('[data-canvas-node-id="'+index.id+'"] > text')).toContainText("02 / 04");
  await expect(study).toContainText("NOT AN OBSERVED WORKFLOW");
  const saved=await read();
  expect(saved.scenes.find(row=>row.id===scene.id)!.nodes).toEqual(scene.nodes);
  expect(saved.deliverables.find(row=>row.id===profile.id)!.text_overrides).toMatchObject({
    [heading.id]:title,[body.id]:copy,[index.id]:index.text,[notice.id]:notice.text,
  });
});
