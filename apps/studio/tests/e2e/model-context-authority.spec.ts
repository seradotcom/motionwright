import { expect, test, type Page } from "@playwright/test";
import type { ModelContextScope, RecordedPreflight } from "../fixtures/model-context-authority";

// Component regressions through real ModelContextInspector + api.ts. The named
// fixture delays only read-only transport responses; these are not service tests.
const result = (page: Page) => page.getByRole("region", { name: "Model request preflight result", exact: true });
const inspectButton = (page: Page) => page.getByRole("button", { name: "Inspect request boundary", exact: true });
const requests = (page: Page) => page.evaluate(() => window.__MODEL_CONTEXT_AUTHORITY__.requests());
const scope = (page: Page) => page.evaluate(() => window.__MODEL_CONTEXT_AUTHORITY__.scope());

async function inspect(page: Page): Promise<RecordedPreflight> {
  const previous = (await requests(page)).length;
  await inspectButton(page).click();
  await expect.poll(async () => (await requests(page)).length).toBe(previous + 1);
  return (await requests(page))[previous];
}

async function settle(page: Page, id: number, outcome: "success" | "error", marker: string) {
  await page.evaluate(async ({ id, outcome, marker }) => {
    const fixture = window.__MODEL_CONTEXT_AUTHORITY__;
    if (outcome === "success") await fixture.resolve(id, marker);
    else await fixture.reject(id, marker);
  }, { id, outcome, marker });
}

async function expectNoDisclosure(page: Page) {
  await expect(result(page)).toHaveCount(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByText("NETWORK OFF", { exact: true })).toBeVisible();
}

async function expectFresh(page: Page, request: RecordedPreflight) {
  await settle(page, request.id, "success", "fresh-B");
  await expect(result(page)).toContainText("fresh-B");
  await expect(result(page)).toContainText("r" + request.scope.revision);
  await expect(result(page)).toContainText("network dispatched: no");
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(inspectButton(page)).toBeEnabled();
  expect((await requests(page)).every((item) => item.command === "model_request_preflight")).toBe(true);
}

const editCases = [
  {
    name: "provider mode",
    edit: (page: Page) => page.getByLabel("Model provider mode", { exact: true }).selectOption("remote"),
    check: (request: RecordedPreflight) => expect(request.draft.provider_kind).toBe("remote"),
  },
  {
    name: "provider",
    edit: (page: Page) => page.getByLabel("Model provider", { exact: true }).fill("provider-B"),
    check: (request: RecordedPreflight) => expect(request.draft.provider).toBe("provider-B"),
  },
  {
    name: "model",
    edit: (page: Page) => page.getByLabel("Model identifier", { exact: true }).fill("model-B"),
    check: (request: RecordedPreflight) => expect(request.draft.model).toBe("model-B"),
  },
  {
    name: "resource",
    edit: async (page: Page) => page.getByLabel("Model context resource", { exact: true })
      .selectOption("project:" + (await scope(page)).project_id),
    check: (request: RecordedPreflight) => expect(request.draft.resource_refs).toEqual(["project:" + request.project_id]),
  },
  {
    name: "data classes",
    edit: (page: Page) => page.getByRole("checkbox", { name: "Text", exact: true }).uncheck(),
    check: (request: RecordedPreflight) => expect(request.draft.data_classes).toEqual(["metadata"]),
  },
];

test.beforeEach(async ({ page }) => {
  await page.goto("/tests/fixtures/model-context-authority.html");
  await expect(page.getByRole("heading", { name: "Model context boundary", exact: true })).toBeVisible();
});

for (const outcome of ["success", "error"] as const) {
  for (const change of editCases) {
    test(`late ${outcome} is discarded after editing ${change.name}; fresh B remains usable`, async ({ page }) => {
      const requestA = await inspect(page);
      await change.edit(page);
      await settle(page, requestA.id, outcome, "late-A");
      await expectNoDisclosure(page);

      const requestB = await inspect(page);
      change.check(requestB);
      await expectFresh(page, requestB);
      expect(await requests(page)).toHaveLength(2);
    });
  }

  for (const field of ["project_id", "generation", "revision", "branch_id", "scene_id"] as const) {
    test(`late ${outcome} is discarded after ${field} changes with other identity fields stable`, async ({ page }) => {
      const original = await scope(page);
      const changed: ModelContextScope = {
        ...original,
        [field]: field === "revision" ? original.revision + 1 : String(original[field]) + "-B",
      };
      const requestA = await inspect(page);
      await page.evaluate((next) => window.__MODEL_CONTEXT_AUTHORITY__.setScope(next), changed);
      await settle(page, requestA.id, outcome, "late-A");
      await expectNoDisclosure(page);
      await expect(page.getByLabel("Model context resource", { exact: true })).toHaveValue("scene:" + changed.scene_id);

      const requestB = await inspect(page);
      expect(requestB.scope).toEqual(changed);
      expect(requestB.project_id).toBe(changed.project_id);
      expect(requestB.draft.resource_refs).toEqual(["scene:" + changed.scene_id]);
      await expectFresh(page, requestB);
    });
  }

  test(`late ${outcome} cannot release fresh B's pending indicator`, async ({ page }) => {
    const requestA = await inspect(page);
    await page.getByLabel("Model identifier", { exact: true }).fill("model-B");
    const requestB = await inspect(page);
    await settle(page, requestA.id, outcome, "late-A");
    await expectNoDisclosure(page);
    await expect(page.getByRole("button", { name: "Inspecting…", exact: true })).toBeDisabled();
    await expectFresh(page, requestB);
  });

  test(`late ${outcome} cannot overwrite fresh B's completed result`, async ({ page }) => {
    const requestA = await inspect(page);
    await page.getByLabel("Model identifier", { exact: true }).fill("model-B");
    const requestB = await inspect(page);
    await expectFresh(page, requestB);
    await settle(page, requestA.id, outcome, "late-A");
    await expect(result(page)).toContainText("fresh-B");
    await expect(result(page)).not.toContainText("late-A");
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(inspectButton(page)).toBeEnabled();
  });

  test(`returning to the original draft does not revive an abandoned ${outcome}`, async ({ page }) => {
    const provider = page.getByLabel("Model provider", { exact: true });
    const original = await provider.inputValue();
    const requestA = await inspect(page);
    await provider.fill("provider-B");
    await provider.fill(original);
    await settle(page, requestA.id, outcome, "late-A");
    await expectNoDisclosure(page);
    await expectFresh(page, await inspect(page));
  });

  test(`returning to the original project does not revive an abandoned ${outcome}`, async ({ page }) => {
    const original = await scope(page);
    const requestA = await inspect(page);
    await page.evaluate((id) => window.__MODEL_CONTEXT_AUTHORITY__.setScope({ project_id: id }), original.project_id + "-B");
    await page.evaluate((next) => window.__MODEL_CONTEXT_AUTHORITY__.setScope(next), original);
    await settle(page, requestA.id, outcome, "late-A");
    await expectNoDisclosure(page);
    await expectFresh(page, await inspect(page));
  });

  test(`an unmounted inspector's late ${outcome} does not affect a new mounted inspector`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const requestA = await inspect(page);
    await page.evaluate(() => window.__MODEL_CONTEXT_AUTHORITY__.unmount());
    await expect(page.getByRole("heading", { name: "Model context boundary", exact: true })).toHaveCount(0);
    await page.evaluate(() => window.__MODEL_CONTEXT_AUTHORITY__.mount());
    const requestB = await inspect(page);
    await settle(page, requestA.id, outcome, "late-A");
    await expectNoDisclosure(page);
    await expect(page.getByRole("button", { name: "Inspecting…", exact: true })).toBeDisabled();
    await expectFresh(page, requestB);
    expect(errors).toEqual([]);
  });
}

for (const change of editCases) {
  test(`editing ${change.name} also removes an already displayed error`, async ({ page }) => {
    const requestA = await inspect(page);
    await settle(page, requestA.id, "error", "current-error-A");
    await expect(page.getByRole("alert")).toHaveText("current-error-A");
    await change.edit(page);
    await expectNoDisclosure(page);
    const requestB = await inspect(page);
    change.check(requestB);
    await expectFresh(page, requestB);
  });
}

for (const field of ["project_id", "generation", "base_revision"] as const) {
  test(`a response with a foreign ${field} is refused before displaying disclosure`, async ({ page }) => {
    const request = await inspect(page);
    const patch = field === "base_revision"
      ? { base_revision: request.scope.revision + 1 }
      : { [field]: "foreign-" + field };
    await page.evaluate(({ id, patch }) => window.__MODEL_CONTEXT_AUTHORITY__.resolve(id, "foreign-A", patch), {
      id: request.id, patch,
    });
    await expect(result(page)).toHaveCount(0);
    await expect(page.getByRole("alert")).toContainText("different project snapshot");
    await expect(inspectButton(page)).toBeEnabled();
    await expectFresh(page, await inspect(page));
  });
}
