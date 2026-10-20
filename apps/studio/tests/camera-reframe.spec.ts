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
