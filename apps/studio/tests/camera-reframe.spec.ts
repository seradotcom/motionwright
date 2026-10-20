import {expect,test} from "@playwright/test";

test("Camera source controls refuse unsupported lens and unbounded movement",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Camera",exact:true}).click();
  await expect(page.getByRole("heading",{name:"Camera Reframe"})).toBeVisible();
  await expect(page.getByLabel("Start center X")).toBeVisible();
  await expect(page.getByLabel("End center X")).toBeVisible();
  await expect(page.getByLabel("Camera duration frames")).toHaveValue("90");
  await expect(page.getByLabel("Camera reframe playhead")).toBeVisible();
  await page.getByLabel("End center X").fill("1450");
  await expect(page.getByText(/end x is outside the admitted source canvas/)).toBeVisible();
  await expect(page.getByRole("button",{name:"Attach camera reframe"})).toBeDisabled();
  await page.getByLabel("End center X").fill("960");
  await page.getByLabel("End center Y").fill("540");
  await expect(page.getByRole("button",{name:"Attach camera reframe"})).toBeDisabled();
  await expect(page.getByText(/optical|focal length/).first()).toBeVisible();
});

test("Camera author raw input persists through Production navigation and browser reload",async({page})=>{
  await page.goto("/");
  const button=(name:string)=>page.getByRole("button",{name,exact:true});
  const camera=async()=>{
    await button("Production").click();
    await button("Camera").click();
    await expect(page.getByRole("heading",{name:"Camera Reframe"})).toBeVisible();
  };
  await camera();
  await page.getByLabel("End center X",{exact:true}).fill("1e");
  await page.getByLabel("Camera duration frames",{exact:true}).fill("");
  await expect(button("Attach camera reframe")).toBeDisabled();
  await button("Brief").click();
  await camera();
  await expect(page.getByLabel("End center X",{exact:true})).toHaveValue("1e");
  await expect(page.getByLabel("Camera duration frames",{exact:true})).toHaveValue("");
  await page.reload();
  await camera();
  await expect(page.getByLabel("End center X",{exact:true})).toHaveValue("1e");
  await expect(page.getByLabel("Camera duration frames",{exact:true})).toHaveValue("");
  await expect(button("Attach camera reframe")).toBeDisabled();
  await expect(page.getByRole("alert",{name:"Camera recovery warning"})).toHaveCount(0);
});

test("silently failed Camera backup blocks canonical command without deleting author's bytes",async({page})=>{
  await page.goto("/");
  const button=(name:string)=>page.getByRole("button",{name,exact:true});
  await button("Production").click(); await button("Camera").click();
  await page.evaluate(()=>{
    const original=Storage.prototype.setItem;
    Storage.prototype.setItem=function(key:string,value:string){
      if(this===window.sessionStorage&&key.startsWith("motionwright.camera-writer.v1:"))return;
      return original.call(this,key,value);
    };
  });
  await page.getByLabel("End center X",{exact:true}).fill("974");
  await expect(page.getByRole("alert",{name:"Camera recovery warning"})).toContainText("pointer was not confirmed");
  await expect(button("Attach camera reframe")).toBeDisabled();
  const raw=await page.evaluate(()=>{
    for(let i=0;i<window.localStorage.length;i++){
      const key=window.localStorage.key(i);
      if(!key?.startsWith("motionwright.camera-recovery.v1:"))continue;
      const item=window.localStorage.getItem(key);
      if(item&&JSON.parse(item).draft.fields.end_center_x==="974")return item;
    }
    return null;
  });
  expect(raw).not.toBeNull();
  await page.reload(); await button("Production").click(); await button("Camera").click();
  await expect(page.getByLabel("End center X",{exact:true})).not.toHaveValue("974");
  await expect(page.getByLabel("Local Camera recovery copies")).toBeVisible();
});

test("Camera inspection preserves a recovered pending copy and never authorizes replay",async({page},info)=>{
  const button=(name:string)=>page.getByRole("button",{name,exact:true});
  const open=async()=>{
    await button("Production").click();await button("Camera").click();
    await expect(page.getByRole("heading",{name:"Camera Reframe",exact:true})).toBeVisible();
  };
  await page.goto("/");await open();
  await page.getByLabel("End center X",{exact:true}).fill("974");
  // Browser-demo recovery fixture only: no service result or native success is
  // manufactured here. Real held-request cases live in the service spec.
  const pending=await page.evaluate(async()=>{
    const draftModule="/src/cameraReframeDraft.ts",fixtureModule="/src/fixture.ts";
    const model=await import(draftModule),{fixtureProject}=await import(fixtureModule);
    const pointer=Object.keys(sessionStorage).find(key=>key.startsWith(model.CAMERA_POINTER_PREFIX));
    if(!pointer)throw new Error("Camera did not write its real recovery pointer.");
    const scope=decodeURIComponent(pointer.slice(model.CAMERA_POINTER_PREFIX.length));
    const writerId=sessionStorage.getItem(pointer)!;
    const key=model.cameraRecoveryKey(scope,writerId),raw=localStorage.getItem(key)!;
    const draft=model.decodeCameraCopy(raw,scope,writerId);
    const scene=fixtureProject.scenes.find((item:{id:string})=>item.id===draft.sceneId);
    if(!scene||model.cameraScope(fixtureProject,scene)!==scope)throw new Error("Fixture scope changed.");
    const attempt=model.armCameraDraft(draft,fixtureProject,scene,"upsert",
      model.cameraDraftValue(draft),crypto.randomUUID());
    const retained=model.writeCameraCopy(localStorage,sessionStorage,attempt,writerId,raw);
    return {key,raw:retained,awaiting:attempt.awaiting,revision:fixtureProject.revision};
  });
  await page.reload();await open();
  const region=page.getByRole("region",{name:"Unconfirmed Camera submission"});
  await expect(region).toBeVisible();
  await expect(button("Attach camera reframe")).toBeDisabled();
  const inspect=page.getByRole("button",{name:/^(Inspect saved Camera|I verified this operation was not saved)$/});
  await inspect.click();
  await expect(region.getByRole("status")).toContainText("remains unconfirmed");
  const after=await page.evaluate(key=>localStorage.getItem(key),pending.key);
  expect(after).toBe(pending.raw);
  expect(JSON.parse(after!).draft.awaiting).toEqual(pending.awaiting);
  await expect(button("Attach camera reframe")).toBeDisabled();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+pending.revision);
  await expect(page.getByLabel("End center X",{exact:true})).toHaveValue("974");
  await expect(button("I verified this operation was not saved")).toHaveCount(0);
  await page.screenshot({path:info.outputPath("camera-recovery-inspection.png"),fullPage:true});
});
