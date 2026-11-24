import { expect, test } from "@playwright/test";

test("Production procedural designer creates and revises real editable Canvas objects", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.getByRole("button",{name:"Add scene",exact:true}).click();
  await page.getByLabel("New scene name").fill("Procedural design study");
  await page.getByRole("button",{name:"Add",exact:true}).click();
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByLabel("Production working scene").selectOption({label:"Procedural design study"});
  await page.getByRole("button",{name:"Procedural",exact:true}).click();

  await expect(page.getByRole("heading",{name:"Procedural field / repetition"})).toBeVisible();
  const count=page.getByLabel("Number of objects");
  await count.fill("18");
  await page.getByLabel("Procedural distribution").selectOption("staggered");
  await page.getByLabel("Entrance interval (frames; 0 disables)").fill("3");
  await expect(page.getByLabel("Procedural preview playhead")).toHaveValue("2");
  const study=page.getByRole("img",{name:/Procedural editorial preview, staggered distribution, 18 editable objects/});
  await expect(study).toBeVisible();
  await page.screenshot({path:testInfo.outputPath("procedural-study-staggered.png"),fullPage:true});

  await page.getByRole("button",{name:"Add procedural field"}).click();
  await expect(page.getByRole("button",{name:"Update procedural field"})).toBeVisible();

  await count.fill("65");
  await expect(page.getByRole("alert")).toContainText("budget exceeded");
  await expect(page.getByRole("button",{name:"Update procedural field"})).toBeDisabled();

  await count.fill("8");
  await expect(page.getByRole("img",{name:/8 editable objects/})).toBeVisible();
  await page.getByRole("button",{name:"Update procedural field"}).click();
  await page.getByRole("button",{name:"Detach generator (keep objects)"}).click();
  await expect(page.getByRole("button",{name:"Add procedural field"})).toBeVisible();
  await page.getByRole("button",{name:"Edit individual objects"}).click();
  await expect(page.getByRole("button",{name:"Canvas object ProceduralField / item 000",exact:true})).toBeVisible();
});

test("Production procedural circles and orbital layouts remain editable",async({page},testInfo)=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Add scene",exact:true}).click();
  await page.getByLabel("New scene name").fill("Orbital design study");
  await page.getByRole("button",{name:"Add",exact:true}).click();
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByLabel("Production working scene").selectOption({label:"Orbital design study"});
  await page.getByRole("button",{name:"Procedural",exact:true}).click();
  await page.getByLabel("Procedural primitive").selectOption("circle");
  await page.getByLabel("Procedural distribution").selectOption("radial");
  await expect(page.getByRole("img",{name:/Procedural editorial preview, radial distribution, 12 editable objects/})).toBeVisible();
  await page.screenshot({path:testInfo.outputPath("procedural-circle-radial.png"),fullPage:true});
  await page.getByRole("button",{name:"Add procedural field"}).click();
  await expect(page.getByRole("button",{name:"Update procedural field"})).toBeVisible();
  await page.getByLabel("Procedural distribution").selectOption("spiral");
  await expect(page.getByRole("img",{name:/Procedural editorial preview, spiral distribution, 12 editable objects/})).toBeVisible();
  await page.getByRole("button",{name:"Update procedural field"}).click();
  await page.getByRole("button",{name:"Edit individual objects"}).click();
  await expect(page.getByRole("button",{name:"Canvas object ProceduralField / item 000",exact:true})).toBeVisible();
});

test("procedural influence component edits complete geometry and color while retaining source objects", async ({ page }, info) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Add scene", exact: true }).click();
  await page.getByLabel("New scene name").fill("Influence component");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await page.getByRole("button", { name: "Production", exact: true }).click();
  await page.getByLabel("Production working scene").selectOption({ label: "Influence component" });
  await page.getByRole("button", { name: "Procedural", exact: true }).click();
  await page.getByLabel("Procedural distribution", { exact: true }).selectOption("grid");
  await page.getByLabel("Number of objects", { exact: true }).fill("3");
  await page.getByLabel("Columns", { exact: true }).fill("3");
  for (const [label, value] of [["Stage X", "200"], ["Stage Y", "200"], ["Field width", "300"], ["Field height", "100"], ["Object size", "40"]] as const)
    await page.getByLabel(label, { exact: true }).fill(value);
  await page.getByLabel("Procedural fill", { exact: true }).fill("#000000");
  await page.getByLabel("Enable influence field", { exact: true }).check();
  for (const [label, value] of [["Influence center X", "350"], ["Influence center Y", "250"], ["Influence radius (px)", "200"],
    ["Influence offset X", "20"], ["Influence offset Y", "-10"], ["Influence center scale (%)", "150"],
    ["Influence static orientation (degrees)", "30"], ["Influence center fill", "#FFFFFF"]] as const)
    await page.getByLabel(label, { exact: true }).fill(value);
  await expect(page.getByRole("img", { name: /Procedural editorial preview, grid distribution, 3 editable objects/ })).toBeVisible();
  await page.getByRole("button", { name: "Add procedural field", exact: true }).click();
  await expect(page.getByRole("button", { name: "Update procedural field", exact: true })).toBeVisible();
  await page.getByLabel("Influence offset X", { exact: true }).fill("-");
  await expect(page.getByRole("button", { name: "Update procedural field", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Production plan", exact: true }).click();
  await page.getByRole("button", { name: "Procedural", exact: true }).click();
  await expect(page.getByLabel("Influence offset X", { exact: true })).toHaveValue("-");
  await page.getByLabel("Influence offset X", { exact: true }).fill("-20");
  await page.getByLabel("Influence falloff", { exact: true }).selectOption("smoothstep");
  await page.getByRole("button", { name: "Update procedural field", exact: true }).click();
  await page.getByLabel("Entrance interval (frames; 0 disables)", { exact: true }).fill("3");
  await expect(page.getByRole("button", { name: "Update procedural field", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Update procedural field", exact: true }).click();
  await expect(page.getByLabel("Influence static orientation (degrees)", { exact: true })).toHaveValue("30");
  await page.getByLabel("Enable influence field", { exact: true }).uncheck();
  await page.getByRole("button", { name: "Update procedural field", exact: true }).click();
  await expect(page.getByLabel("Influence offset X", { exact: true })).toHaveValue("-20");
  await expect(page.getByLabel("Influence offset X", { exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Detach generator (keep objects)", exact: true }).click();
  await page.getByRole("button", { name: "Edit individual objects", exact: true }).click();
  await expect(page.getByRole("button", { name: "Canvas object ProceduralField / item 000", exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("influence-component-editable-objects.png"), fullPage: true });
});
