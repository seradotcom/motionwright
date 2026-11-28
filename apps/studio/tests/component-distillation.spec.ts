import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";
import { applyDistillationChange, DISTILLATION_RENDERER } from "../src/componentDistillation";
import { emptyProductionDesign } from "../src/creativeProduction";
import { fixtureProject } from "../src/fixture";

test("Distillation requires genuine source variations and negative evidence before proposing",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Distillation",exact:true}).click();
  await expect(page.getByRole("heading",{name:"Component distillation"})).toBeVisible();
  await expect(page.getByRole("button",{name:"Propose original component"})).toBeDisabled();
  await expect(page.getByRole("button",{name:"Record review only"})).toBeDisabled();
  await expect(page.getByText(/no automatic installation/i)).toBeVisible();
  await expect(page.getByLabel("Distillation source A")).toBeVisible();
  await expect(page.getByLabel("Distillation negative scene")).toBeVisible();
  await page.getByLabel("Distillation label").fill("Proposed original editable panel");
  await page.getByLabel("Distillation author", { exact: true }).fill("Original designer");
  await page.getByLabel("Distillation rights").fill("The original roles belong to this project.");
  await page.getByLabel("Distillation requirements").fill("Two varied source scenes");
  await expect(page.getByRole("button",{name:"Propose original component"})).toBeDisabled();
  await expect(page.getByText(/No proposals exist/)).toBeVisible();
});

test("a reviewed variant becomes normal editable Canvas objects with source provenance", async ({page}) => {
  const project = structuredClone(fixtureProject);
  project.schema_version = 2;
  project.production_design = emptyProductionDesign();
  project.locks = [];
  const [left, right] = project.scenes.filter(scene => scene.renderer === "motion-canvas");
  const negative = project.scenes.find(scene => scene.renderer !== "motion-canvas")!;
  const origin = project.scenes.flatMap(scene => scene.nodes).find(node => node.kind === "text")!;
  const positives = [left, right].map((scene, index) => {
    const first = { ...structuredClone(origin), id: randomUUID(), kind: "rectangle", name: "Distilled panel " + index,
      x: 560, text: null, parent_id: null, property_locks: [], relations: [], keyframes: [] };
    const second = { ...structuredClone(first), id: randomUUID(), kind: "text", name: "Distilled title " + index,
      y: 300, text: index === 0 ? "Original title A" : "Original title B" };
    scene.nodes.push(first, second);
    return { scene_id: scene.id, node_ids: [first.id, second.id] };
  });
  const proposalId = randomUUID();
  applyDistillationChange(project, { type: "propose_component_distillation", input: {
    id: proposalId, label: "Reviewed title card", author: "Original editor",
    original_rights_attestation: "These source objects were authored for this project.",
    source_project_id: project.id, source_generation: project.generation, source_revision: project.revision,
    roles: [{ key: "panel", kind: "rectangle" }, { key: "title", kind: "text" }],
    positives, negatives: [{ scene_id: negative.id, node_id: null, rejection: "non_motion_canvas" }],
    requirements: ["Preserve title and background as separate editable objects"],
  } });
  applyDistillationChange(project, { type: "review_component_distillation", id: proposalId, review: {
    reviewer: "Independent reviewer", rationale: "The title and the background are present in both variations.",
    verdict: "compatible_proposal", compatibility: DISTILLATION_RENDERER, source_revision: project.revision,
  } });
  await page.route("**/src/fixture.ts", async route => {
    const response = await route.fetch();
    await route.fulfill({response, body: (await response.text()) + "\nfixtureBootstrap.project = " + JSON.stringify(project) + ";\n"});
  });
  await page.goto("/");
  await page.getByRole("button", {name: "Production", exact: true}).click();
  await page.getByRole("button", {name: "Distillation", exact: true}).click();
  await page.getByLabel("Distillation review candidate", {exact: true}).selectOption(proposalId);

  const editor = page.getByRole("region", {name: "Use reviewed component"});
  await editor.getByLabel("Frozen variant").selectOption(right.id);
  await editor.getByLabel("Destination Canvas scene").selectOption(left.id);
  const title = editor.getByLabel("Text for Distilled title 1 (title)");
  await expect(title).toHaveAttribute("placeholder", "Original title B");
  await title.fill("Editable instance wording");
  await editor.getByRole("button", {name: "Create editable instance"}).click();
  await expect(editor.getByRole("list", {name: "Saved editable instances"})).toContainText("Variant 2");
  await expect(editor.getByRole("list", {name: "Saved editable instances"})).toContainText("2 of 2 Canvas objects");
  await editor.getByRole("button", {name: "Open " + left.name + " in Canvas", exact: true}).click();
  await page.getByRole("button", {name: "Canvas object Distilled title 1", exact: true}).click();
  await expect(page.getByLabel("Canvas text")).toHaveValue("Editable instance wording");
  await page.getByLabel("Canvas text").fill("Edited with the regular Canvas inspector");
  await page.getByRole("button", {name: "Commit text", exact: true}).click();
  await expect(page.getByRole("button", {name: "Commit text", exact: true})).toBeDisabled();

  await page.getByRole("button", {name: "Production", exact: true}).click();
  await page.getByRole("button", {name: "Distillation", exact: true}).click();
  await expect(editor.getByRole("list", {name: "Saved editable instances"})).toContainText("2 of 2 Canvas objects");
  await expect(editor).toContainText("source revision " + project.revision);
  await expect(page.getByRole("region", {name: "Distillation source evidence"})).toContainText("Preserve title and background as separate editable objects");
});
