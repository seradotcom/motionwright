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


test("editable procedural paths and adaptive grid preserve human objects through whole composition edits", async ({ page }, info) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Add scene", exact: true }).click();
  await page.getByLabel("New scene name").fill("Editable path composition");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  const open = async () => {
    await page.getByRole("button", { name: "Production", exact: true }).click();
    await page.getByLabel("Production working scene", { exact: true }).selectOption({ label: "Editable path composition" });
    await page.getByRole("button", { name: "Procedural", exact: true }).click();
  };
  await open();
  await page.getByLabel("Procedural distribution", { exact: true }).selectOption("path");
  for (const [label, value] of [["Number of objects", "4"], ["Stage X", "100"], ["Stage Y", "100"],
    ["Field width", "420"], ["Field height", "220"], ["Object size", "20"],
    ["Path point 1 X (%)", "0"], ["Path point 1 Y (%)", "0"],
    ["Path point 2 X (%)", "100"], ["Path point 2 Y (%)", "0"],
    ["Path point 3 X (%)", "100"], ["Path point 3 Y (%)", "100"]] as const)
    await page.getByLabel(label, { exact: true }).fill(value);
  const study = () => page.getByRole("img", { name: /Procedural editorial preview,/ });
  await expect(study().locator("[data-canvas-node-id] > rect")).toHaveCount(4);
  expect(await study().locator("[data-canvas-node-id] > rect").evaluateAll(rects => rects.map(rect => [Number(rect.getAttribute("x")), Number(rect.getAttribute("y"))])))
    .toEqual([[100, 100], [300, 100], [500, 100], [500, 300]]);
  await page.getByRole("button", { name: "Add procedural field", exact: true }).click();
  await expect(page.getByRole("button", { name: "Update procedural field", exact: true })).toBeVisible();
  const savedStudy = page.getByRole("img", { name: "Saved procedural scene", exact: true });
  await expect(savedStudy.locator("[data-canvas-node-id]")).toHaveCount(4);
  const ids = await savedStudy.locator("[data-canvas-node-id]").evaluateAll(nodes => nodes.map(node => node.getAttribute("data-canvas-node-id")));
  await page.getByRole("button", { name: "Edit individual objects", exact: true }).click();
  await page.getByRole("button", { name: "Canvas object ProceduralField / item 000", exact: true }).click();
  await page.getByLabel("Canvas fill", { exact: true }).fill("#FFAA22");
  const beforeStyle = await page.locator(".revision-chip").first().innerText();
  await page.getByRole("button", { name: "Commit style", exact: true }).click();
  await expect(page.locator(".revision-chip").first()).not.toHaveText(beforeStyle);
  await open();
  await expect(study().locator(`[data-canvas-node-id="${ids[0]}"] > rect`)).toHaveAttribute("fill", "#FFAA22");
  await page.getByLabel("Path point 2 X (%)", { exact: true }).fill("-");
  await expect(page.getByRole("button", { name: "Update procedural field", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Production plan", exact: true }).click();
  await page.getByRole("button", { name: "Procedural", exact: true }).click();
  await expect(page.getByLabel("Path point 2 X (%)", { exact: true })).toHaveValue("-");
  await page.getByLabel("Path point 2 X (%)", { exact: true }).fill("80");
  await page.getByRole("button", { name: "Add path point", exact: true }).click();
  await page.getByLabel("Path point 4 X (%)", { exact: true }).fill("0");
  await page.getByLabel("Closed procedural path", { exact: true }).check();
  await page.getByRole("button", { name: "Move path point 4 earlier", exact: true }).click();
  await expect(page.getByLabel("Path point 3 X (%)", { exact: true })).toHaveValue("0");
  await page.getByRole("button", { name: "Move path point 3 later", exact: true }).click();
  await page.getByRole("button", { name: "Update procedural field", exact: true }).click();
  await page.getByLabel("Procedural distribution", { exact: true }).selectOption("adaptive_grid");
  await page.getByLabel("Number of objects", { exact: true }).fill("12");
  await page.getByLabel("Field width", { exact: true }).fill("200");
  await page.getByLabel("Field height", { exact: true }).fill("560");
  await expect(page.getByText(/2 columns × 6 rows, chosen from count/)).toBeVisible();
  await expect(study().locator("[data-canvas-node-id] > rect")).toHaveCount(12);
  expect((await study().locator("[data-canvas-node-id]").evaluateAll(nodes => nodes.map(node => node.getAttribute("data-canvas-node-id")))).slice(0, 4)).toEqual(ids);
  await expect(study().locator(`[data-canvas-node-id="${ids[0]}"] > rect`)).toHaveAttribute("fill", "#FFAA22");
  await expect(page.getByRole("img", { name: "Saved procedural scene", exact: true }).locator(`[data-canvas-node-id="${ids[0]}"] > rect`)).toHaveAttribute("fill", "#FFAA22");
  await page.getByRole("button", { name: "Update procedural field", exact: true }).click();
  await expect(page.getByLabel("Path point 2 X (%)", { exact: true })).toHaveValue("80");
  await expect(page.getByLabel("Path point 2 X (%)", { exact: true })).toBeDisabled();
  await page.screenshot({ path: info.outputPath("path-to-grid-preserved-human-composition.png"), fullPage: true });
  await page.getByRole("button", { name: "Discard inactive path draft", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Editable path", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Detach generator (keep objects)", exact: true }).click();
  await page.getByRole("button", { name: "Edit individual objects", exact: true }).click();
  await expect(page.getByRole("button", { name: "Canvas object ProceduralField / item 011", exact: true })).toBeVisible();
});

test("adaptive field output profiles reflow the same editable objects and expose human position conflicts", async ({ page }, info) => {
  const button = (name: string) => page.getByRole("button", { name, exact: true });
  await page.goto("/");
  await button("Add scene").click(); await page.getByLabel("New scene name").fill("Adaptive output composition"); await button("Add").click();
  const open = async () => {
    await button("Production").click();
    await page.getByLabel("Production working scene", { exact: true }).selectOption({ label: "Adaptive output composition" });
    await button("Procedural").click();
  };
  await open(); await page.getByLabel("Procedural distribution", { exact: true }).selectOption("adaptive_grid");
  await page.getByLabel("Number of objects", { exact: true }).fill("24");
  await page.getByLabel("Entrance interval (frames; 0 disables)", { exact: true }).fill("3");
  await button("Add procedural field").click(); await expect(button("Update procedural field")).toBeVisible();
  const source = page.getByRole("img", { name: "Saved procedural scene", exact: true });
  const ids = await source.locator("[data-canvas-node-id]").evaluateAll(nodes => nodes.map(node => node.getAttribute("data-canvas-node-id")));
  expect(ids).toHaveLength(24);
  await button("Edit individual objects").click();
  await page.getByRole("button", { name: "Canvas object ProceduralField / item 000", exact: true }).click();
  await page.getByLabel("Canvas fill", { exact: true }).fill("#FFAA22");
  const beforePaint = await page.locator(".command-bar .revision-chip").innerText();
  await button("Commit style").click(); await expect(page.locator(".command-bar .revision-chip")).not.toHaveText(beforePaint);
  await open();
  const savedRevision = await page.locator(".command-bar .revision-chip").innerText();
  const time = page.getByLabel("Procedural preview playhead", { exact: true });
  await time.focus(); await time.press("End");
  const selector = page.getByLabel("Editor preview timebase", { exact: true });
  const choices = await selector.locator("option").evaluateAll(options => options.map(option => ({
    value: (option as HTMLOptionElement).value, text: option.textContent ?? "",
  })));
  for (const [name, viewBox, columns, rows] of [
    ["Master 16:9", "0 0 1920 1080", 6, 4], ["Square 1:1", "0 0 1080 1080", 5, 5], ["Vertical 9:16", "0 0 1080 1920", 4, 6],
  ] as const) {
    const choice = choices.find(option => option.text.startsWith(name));
    if (!choice) throw new Error("The authored profile is unavailable: " + name);
    await selector.selectOption(choice.value);
    const output = page.getByRole("img", { name: "Procedural output profile study", exact: true });
    await expect(output).toHaveAttribute("viewBox", viewBox);
    await expect(output.locator("[data-canvas-node-id] > rect")).toHaveCount(24);
    const dimensions = await output.locator("[data-canvas-node-id] > rect").evaluateAll(rects => ({
      columns: new Set(rects.map(rect => rect.getAttribute("x"))).size,
      rows: new Set(rects.map(rect => rect.getAttribute("y"))).size,
    }));
    expect(dimensions).toEqual({ columns, rows });
    expect(await output.locator("[data-canvas-node-id]").evaluateAll(nodes => nodes.map(node => node.getAttribute("data-canvas-node-id")))).toEqual(ids);
    await expect(output.locator('[data-canvas-node-id="' + ids[0] + '"] > rect')).toHaveAttribute("fill", "#FFAA22");
    await expect(page.locator(".command-bar .revision-chip")).toHaveText(savedRevision);
  }
  await page.getByRole("img", { name: "Procedural output profile study", exact: true })
    .screenshot({ path: info.outputPath("adaptive-output-portrait-preserved-objects.png") });
  await button("Edit individual objects").click();
  await page.getByRole("button", { name: "Canvas object ProceduralField / item 000", exact: true }).click();
  const inspector = page.locator(".canvas-draft-inspector").filter({ has: page.locator('[id="canvas-draft-' + ids[0] + '-x"]') });
  await expect(inspector).toHaveCount(1);
  const x = inspector.getByLabel("Canvas X", { exact: true }), originalX = Number(await x.inputValue());
  await x.fill(String(originalX + 5)); await inspector.getByRole("button", { name: "Commit transform", exact: true }).click();
  await expect(page.locator(".command-bar .revision-chip")).not.toHaveText(savedRevision);
  const editedRevision = await page.locator(".command-bar .revision-chip").innerText();
  await open();
  await expect(page.getByRole("alert", { name: "Procedural output profile study unavailable", exact: true })).toContainText("override conflict");
  await expect(source.locator('[data-canvas-node-id="' + ids[0] + '"] > rect')).toHaveAttribute("x", String(originalX + 5));
  await expect(page.getByRole("img", { name: "Procedural output profile study", exact: true })).toHaveCount(0);
  await expect(page.locator(".command-bar .revision-chip")).toHaveText(editedRevision);
});
