import {test,expect} from "@playwright/test";

test("browser-only Audio cannot forge a narration take approval or a source snapshot",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Audio",exact:true}).click();
  const section=page.locator(".audio-narration-source-review");
  await expect(section).toBeVisible();
  await section.locator("summary").click();
  await expect(section).toContainText("The browser demo cannot invent a measured or approved source take.");
  await expect(section.getByRole("button",{name:/Review measured source baseline/})).toBeDisabled();
  await expect(section.getByRole("button",{name:/Record and protect this source revision/})).toHaveCount(0);
  await expect(section.getByRole("button",{name:/Release exact recorded source/})).toHaveCount(0);
  await expect(section.getByRole("region",{name:"Narration edit impact"})).toHaveCount(0);
});

test("a local-only Audio preview never fabricates transcript impact on an empty project",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Audio",exact:true}).click();
  const details=page.locator(".audio-narration-source-review");
  await details.locator("summary").click();
  await expect(details.getByRole("button",{name:/Compare newer revision/})).toBeDisabled();
  await expect(details.getByRole("region",{name:"Original narration source snapshot"})).toHaveCount(0);
  await expect(details.getByRole("region",{name:"Narration source dependency map"})).toHaveCount(0);
});
