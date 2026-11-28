import { expect, test } from "./support/selectedSyntheticProject";
import { EVALUATOR_SCHEMA, type CreativeEvaluatorBundle } from "../src/creativeEvaluator";

test("imports and reopens independently versioned evaluator artifacts", async ({ page }) => {
  const id = "abcd1000-0000-4000-8000-000000000001";
  const criterion = "abcd1000-0000-4000-8000-000000000002";
  const first: CreativeEvaluatorBundle = {
    schema: EVALUATOR_SCHEMA, id, version: 1, label: "Original example-aware evaluator",
    purpose: "Review the original short sequence", author_attestation: "I authored both examples and the instructions.",
    playbook: ["Review the actual scene objects."],
    criteria: [{ kind: "minimum_nodes", id: criterion, minimum: 2 }],
    artifacts: {
      instructions_version: 1,
      examples: { version: 1, cases: [{ label: "Original two-object sample", criterion_id: criterion, expected: "pass" }] },
      dependencies: { version: 1, interpreter: EVALUATOR_SCHEMA, inputs: ["canvas_node_roster"] },
    },
  };
  await page.goto("/");
  const open = async () => {
    await page.getByRole("button", { name: "Production", exact: true }).click();
    await page.getByRole("button", { name: "Evaluators", exact: true }).click();
  };
  await open();
  const field = page.getByLabel("Import evaluator JSON"), rights = page.getByLabel("Confirm evaluator source rights");
  const submit = page.getByRole("button", { name: "Import original evaluator JSON", exact: true });
  const selector = page.getByLabel("Evaluator source version");
  const artifacts = page.getByRole("region", { name: "Independent evaluator artifact versions" });
  await field.fill(JSON.stringify(first));
  await rights.check();
  await submit.click();
  await expect(selector).toHaveValue(id + ":1");
  await expect(artifacts).toContainText("Original two-object sample");

  const second = structuredClone(first);
  second.version = 2;
  second.artifacts!.examples.version = 2;
  second.artifacts!.examples.cases[0].label = "Revised object example";
  await field.fill(JSON.stringify(second));
  await rights.check();
  await submit.click();
  await expect(selector).toHaveValue(id + ":2");
  await expect(artifacts).toContainText("Revised object example");
  await page.getByRole("button", { name: "Brief", exact: true }).click();
  await open();
  await selector.selectOption(id + ":1");
  await expect(artifacts).toContainText("Original two-object sample");
  await expect(artifacts).toContainText("canvas node roster");
  await selector.selectOption(id + ":2");
  await expect(artifacts).toContainText("Revised object example");
});
