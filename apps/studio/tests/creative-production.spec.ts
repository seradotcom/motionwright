import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";

test("creative workstation adds an editable component and commits a scoped proposal once",async({page},testInfo)=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Add scene",exact:true}).click();
  await page.getByLabel("New scene name").fill("Hero design study");
  await page.getByRole("button",{name:"Add",exact:true}).click();
  await expect(page.locator(".tree-row")).toHaveCount(5);
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByLabel("Production working scene").selectOption({label:"Hero design study"});
  await expect(page.getByRole("heading",{name:"ProductHeroReveal",exact:true})).toBeVisible();
  await expect(page.getByRole("button",{name:"Add to scene",exact:true})).toBeEnabled();
  await page.getByRole("button",{name:"Add to scene",exact:true}).click();
  await expect(page.getByRole("button",{name:"Update component",exact:true})).toBeVisible();
  await expect(page.getByRole("img",{name:/ProductHeroReveal 16:9/})).toBeVisible();
  await page.screenshot({path:testInfo.outputPath("production-hero-landscape.png"),fullPage:true});
  const expand=page.getByRole("button",{name:"Enlarge study",exact:true});
  await expand.click();
  await expect(page.getByRole("dialog",{name:"Composition study · 1920 × 1080"})).toBeVisible();
  await expect(page.getByRole("button",{name:"Close expanded study"})).toBeFocused();
  const enlarged=page.getByRole("img",{name:"Expanded editorial composition study"});
  expect((await enlarged.boundingBox())!.width).toBeGreaterThan(800);
  await page.screenshot({path:testInfo.outputPath("production-hero-expanded.png"),fullPage:true});
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(expand).toBeFocused();
  await page.getByRole("button",{name:"9:16",exact:true}).click();
  await expect(page.getByRole("img",{name:/ProductHeroReveal 9:16/})).toBeVisible();
  await page.screenshot({path:testInfo.outputPath("production-hero-portrait.png"),fullPage:true});
  await page.getByRole("button",{name:"Scoped changes",exact:true}).click();
  await page.getByLabel("Patch target object").selectOption({label:"ProductHeroReveal / body"});
  await page.getByLabel("Patch replacement text").fill("A deliberate human editorial decision.");
  await page.getByRole("button",{name:"Add to proposal",exact:true}).click();
  await page.getByLabel("Patch rationale").fill("Keep this line while exploring a different brand accent.");
  await page.getByRole("button",{name:"Preview scoped changes",exact:true}).click();
  await expect(page.getByText("Editorial diff prepared",{exact:true})).toBeVisible();
  await page.screenshot({path:testInfo.outputPath("production-scoped-diff.png"),fullPage:true});
  const before=await page.locator(".production-workspace-header p").innerText();
  const revision=Number(before.match(/revision (\d+)/)?.[1]);
  expect(Number.isFinite(revision)).toBeTruthy();
  await page.getByRole("button",{name:"Apply as one revision",exact:true}).click();
  await expect(page.locator(".production-workspace-header p")).toContainText(`revision ${revision+1}`);
  await expect(page.getByRole("button",{name:"Apply as one revision",exact:true})).toBeDisabled();
  await page.getByRole("button",{name:"Components",exact:true}).click();
  await page.getByLabel("Hero accent").fill("#D9A46E");
  await page.getByRole("button",{name:"Update component",exact:true}).click();
  await page.getByRole("button",{name:"Edit objects",exact:true}).click();
  await expect(page.getByRole("button",{name:"Canvas object ProductHeroReveal / body",exact:true})).toContainText("A deliberate human editorial decision.");
});

test("invalid component copy cannot be committed or leave an old preview displayed",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByLabel("Headline",{exact:true}).fill("X".repeat(65));
  await expect(page.locator(".production-inspector [role=alert]")).toContainText("layout budget");
  await expect(page.getByRole("button",{name:"Add to scene",exact:true})).toBeDisabled();
  await expect(page.locator(".production-study text")).toHaveCount(0);
});

test("native inspection never substitutes editorial thumbnails for missing native evidence",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Native inspection",exact:true}).click();
  await expect(page.getByRole("heading",{name:"No current native frame grant",exact:true})).toBeVisible();
  await expect(page.locator(".native-inspection-grid img")).toHaveCount(0);
  await expect(page.getByRole("button",{name:"Inspect current native frames",exact:true})).toHaveCount(0);
  await page.getByRole("button",{name:"Open Deliver and render preview",exact:true}).click();
  await expect(page.getByRole("heading",{name:"No current native frame grant",exact:true})).not.toBeVisible();
});

test("applied scoped changes can be inspected and undone without rewinding the project",async({page},testInfo)=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Add scene",exact:true}).click();
  await page.getByLabel("New scene name").fill("Undo study");
  await page.getByRole("button",{name:"Add",exact:true}).click();
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByLabel("Production working scene").selectOption({label:"Undo study"});
  await page.getByRole("button",{name:"Add to scene",exact:true}).click();
  await expect(page.getByRole("button",{name:"Update component",exact:true})).toBeVisible();
  await page.getByRole("button",{name:"Scoped changes",exact:true}).click();
  await page.getByLabel("Patch target object").selectOption({label:"ProductHeroReveal / body"});
  const original=await page.getByLabel("Patch replacement text").inputValue();
  await page.getByLabel("Patch replacement text").fill("A reversible editorial line.");
  await page.getByRole("button",{name:"Add to proposal",exact:true}).click();
  await page.getByLabel("Patch rationale").fill("Try the shorter copy, preserving the previous line.");
  await page.getByRole("button",{name:"Preview scoped changes",exact:true}).click();
  await page.getByRole("button",{name:"Apply as one revision",exact:true}).click();
  const history=page.getByRole("region",{name:"Reversible scoped changes"});
  await expect(history.getByRole("button",{name:"Preview undo",exact:true})).toBeEnabled();
  const header=page.locator(".production-workspace-header p");
  const revision=Number((await header.innerText()).match(/revision (\d+)/)![1]);
  await history.getByRole("button",{name:"Preview undo",exact:true}).click();
  await expect(header).toContainText(`revision ${revision}`);
  await expect(page.getByRole("img",{name:"After scoped changes",exact:true})).toContainText(original.split("\n")[0]);
  await page.screenshot({path:testInfo.outputPath("production-undo-preview.png"),fullPage:true});
  await page.getByRole("button",{name:"Apply undo as new revision",exact:true}).click();
  await expect(header).toContainText(`revision ${revision+1}`);
  await expect(history.getByRole("button",{name:"Preview redo",exact:true})).toBeEnabled();
  await expect(page.getByRole("img",{name:"Before scoped changes",exact:true})).toContainText(original.split("\n")[0]);
});

test("assistant reviews only current scoped edits and keeps draft replacement explicit", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Add scene", exact: true }).click();
  await page.getByLabel("New scene name").fill("Assistant study");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await page.getByRole("button", { name: "Production", exact: true }).click();
  await page.getByLabel("Production working scene").selectOption({ label: "Assistant study" });
  await page.getByRole("button", { name: "Add to scene", exact: true }).click();
  await expect(page.getByRole("button", { name: "Update component", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Edit objects", exact: true }).click();

  const body = page.getByRole("button", { name: "Canvas object ProductHeroReveal / body", exact: true });
  const original = (await body.innerText()).trim();
  await body.click();
  const assistant = page.getByRole("button", { name: "Assistant", exact: true });
  await assistant.click();
  const dock = page.getByRole("complementary", { name: "Assistant", exact: true });
  await expect(dock.getByLabel("Editing scope")).toHaveValue("selection");
  await dock.getByLabel("Editing instruction").fill("Shorten the body copy.");
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    dock.getByRole("button", { name: "Download context", exact: true }).click(),
  ]);
  const path = await download.path();
  if (!path) throw new Error("Missing assistant context download");
  const request = JSON.parse(await readFile(path, "utf8"));
  expect(request.schema).toBe("motionwright.assistant-request/1");
  expect(request.scope).toHaveLength(1);
  expect(request.scope[0].name).toBe("ProductHeroReveal / body");
  const target = request.scope[0];
  const proposal = {
    schema: "motionwright.assistant-proposal/1",
    source: request.source,
    rationale: "Tighten the body copy and try a slight offset.",
    edits: [
      { kind: "text", node_id: target.id, text: "An approved assistant edit." },
      { kind: "transform", node_id: target.id, transform: { ...target.transform, x: target.transform.x + 10 } },
    ],
  };
  const paste = dock.getByLabel("Proposal JSON");
  const requestReview = dock.getByRole("button", { name: "Review proposal", exact: true });
  await paste.fill(JSON.stringify({ ...proposal, source: { ...request.source, revision: request.source.revision - 1 } }));
  await requestReview.click();
  await expect(dock.getByRole("alert")).toContainText("different project, branch, scene or revision");
  await paste.fill(JSON.stringify({ ...proposal, edits: [{ ...proposal.edits[0], node_id: "other-object" }] }));
  await requestReview.click();
  await expect(dock.getByRole("alert")).toContainText("outside the selected scope");

  const review = page.getByRole("region", { name: "Proposal review", exact: true });
  await dock.getByRole("button", { name: "Open scoped changes", exact: true }).click();
  await review.getByLabel("Patch rationale").fill("Keep this recovered manual draft.");
  await review.getByRole("button", { name: "Return to editor", exact: true }).click();
  await assistant.click();
  await paste.fill(JSON.stringify(proposal));
  await requestReview.click();
  await expect(review.getByLabel("Patch rationale")).toHaveValue("Keep this recovered manual draft.");
  await review.getByRole("button", { name: "Replace draft with assistant proposal", exact: true }).click();
  const queue = review.getByRole("region", { name: "Proposed edits", exact: true });
  const preview = queue.getByRole("button", { name: "Preview scoped changes", exact: true });
  const apply = review.getByRole("button", { name: "Apply as one revision", exact: true });
  await expect(queue).toContainText("ProductHeroReveal / body / transform");
  await expect(apply).toBeDisabled();
  await preview.click();
  await expect(apply).toBeEnabled();
  await queue.getByRole("button", { name: "Remove proposed edit 2", exact: true }).click();
  await expect(apply).toBeDisabled();
  await preview.click();
  await expect(review.getByRole("img", { name: "Before scoped changes", exact: true })).toContainText(target.text.split("\n")[0]);
  await expect(review.getByRole("img", { name: "After scoped changes", exact: true })).toContainText("An approved assistant edit.");
  await review.getByText("Exact changed object values", { exact: true }).click();
  const diff = JSON.parse((await review.locator("details pre").last().innerText()));
  expect(diff[0].before.text).toBe(target.text);
  expect(diff[0].after.x).toBe(diff[0].before.x);
  await apply.click();
  await expect(review.getByRole("button", { name: "Preview undo", exact: true })).toBeEnabled();
  await expect(apply).toBeDisabled();

  await review.getByRole("button", { name: "Preview undo", exact: true }).click();
  await expect(review.getByRole("img", { name: "After scoped changes", exact: true })).toContainText(target.text.split("\n")[0]);
  await review.getByRole("button", { name: "Apply undo as new revision", exact: true }).click();
  await expect(review.getByRole("button", { name: "Preview redo", exact: true })).toBeEnabled();
  await review.getByRole("button", { name: "Return to editor", exact: true }).click();
  await expect(body).toContainText(original);

  const savedRevision = await page.locator(".command-bar .revision-chip").innerText();
  await assistant.click();
  await dock.getByLabel("Editing scope").selectOption("object:" + target.id);
  await dock.getByRole("button", { name: "Review comments", exact: true }).click();
  await expect(page.getByLabel("Review resource scope")).toHaveValue("object");
  await expect(page.locator(".narrative-sheet").getByText("ProductHeroReveal / body", { exact: true })).toBeVisible();
  const comment = page.getByLabel("Review comment", { exact: true });
  await expect(comment).toBeFocused();
  await comment.fill("Check this body copy at its saved object anchor.");
  await page.getByRole("button", { name: "Add anchored review", exact: true }).click();
  const note = page.locator("[data-review-id]").filter({ hasText: "Check this body copy at its saved object anchor." });
  await expect(note).toContainText("canvas-node:" + target.id);
  await expect(note).toContainText(savedRevision);
});
