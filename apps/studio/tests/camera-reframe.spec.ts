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