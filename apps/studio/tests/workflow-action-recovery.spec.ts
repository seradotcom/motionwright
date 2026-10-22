import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import type { WorkflowFixtureHarness, WorkflowFixtureInvocation } from "./fixtures/workflow-action-recovery";

// Real Workspace + api.ts, deliberately synthetic Tauri scheduling values.
// No assertion in this suite proves a native adapter, Broker, durable receipt,
// canonical fingerprint, SQLite persistence or human acceptance.
const url = "/tests/fixtures/workflow-action-recovery.html";
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
// Textarea initial text can join the wrapping label's textContent. Match the
// exact accessible textbox name; the upload uses its explicit file label.
const field = (page: Page, name: string) => name === "Import workflow author input"
  ? page.getByLabel(name, { exact: true }) : page.getByRole("textbox", { name, exact: true });
const retained = (page: Page) => page.getByRole("region", { name: "Retained original workflow action", exact: true });
const requests = (page: Page): Promise<WorkflowFixtureInvocation[]> => page.evaluate(() => window.__WORKFLOW_RECOVERY_FIXTURE__.requests());
const grants = (page: Page) => page.evaluate(() => window.__WORKFLOW_RECOVERY_FIXTURE__.grants());
const writeConsent = (page: Page) => page.getByRole("checkbox", { name: /Enable explicit workflow changes for this workspace session/ });
async function ready(page: Page) {
  await expect(page.getByRole("note")).toContainText("SYNTHETIC SCHEDULING TEST ONLY");
  await expect(page.getByRole("heading", { name: "Workflow intelligence", exact: true })).toBeVisible();
  await expect(field(page, "Candidate name")).toBeVisible();
}
async function compiler(page: Page, name = "Original candidate", description = "Original description") {
  await writeConsent(page).check(); await page.getByRole("checkbox", { name: /Trace Alpha/ }).check();
  await field(page, "Candidate name").fill(name); await field(page, "Description").fill(description);
}
async function sendCompile(page: Page) {
  await button(page, "Compile candidate").click(); await expect.poll(async () => (await requests(page)).length).toBe(1);
  return (await requests(page))[0];
}
async function candidate(page: Page, label: string) {
  const row = page.locator(".workflow-row").filter({ has: page.getByText(label, { exact: true }) });
  await row.getByRole("button", { name: /Inspect gates|Selected/, exact: true }).click();
  await expect(page.getByRole("region", { name: "Candidate verification and replay", exact: true })).toBeVisible();
}
async function ownCopy(page: Page) {
  return page.evaluate(() => {
    const p = window.__WORKFLOW_RECOVERY_FIXTURE__.project(), scope = JSON.stringify([p.id, p.generation, p.active_branch]);
    const pointerKey = "motionwright.workflow-writer.v1:" + encodeURIComponent(scope), writer = sessionStorage.getItem(pointerKey);
    if (!writer) throw new Error("No confirmed workflow writer pointer.");
    const key = "motionwright.workflow-copy.v1:" + encodeURIComponent(scope) + ":" + writer, raw = localStorage.getItem(key);
    if (!raw) throw new Error("No confirmed workflow author copy."); return { scope, pointerKey, writer, key, raw, copy: JSON.parse(raw) };
  });
}
async function nativeFailure(page: Page, request: WorkflowFixtureInvocation, known = false, state: "not_checked" | "not_observed" | "unavailable" = "unavailable", message = "Original native diagnostic") {
  await page.evaluate(({ index, requestId, known, state, message }) => window.__WORKFLOW_RECOVERY_FIXTURE__.reject(index, {
    schema: "motionwright.workflow-action-failure/1", command: "workflow.compile", request_id: requestId,
    code: "Timeout", outcome_known: known, message, receipt: { state },
  }), { index: request.index, requestId: request.request.request_id, known, state, message });
}
async function nativeRecordedFailure(page: Page, request: WorkflowFixtureInvocation, stage: string) {
  await page.evaluate(({ index, stage }) => {
    const receipt = window.__WORKFLOW_RECOVERY_FIXTURE__.syntheticResponse(index).receipt;
    receipt.identity.stage = stage;
    return window.__WORKFLOW_RECOVERY_FIXTURE__.reject(index, {
      schema: "motionwright.workflow-action-failure/1", command: "workflow.compile", request_id: receipt.identity.request_id,
      code: "PermissionDenied", outcome_known: true, message: "This invocation was rejected; retain the original receipt.", receipt,
    });
  }, { index: request.index, stage });
}
async function exportInput(page: Page) {
  const downloadPromise = page.waitForEvent("download"); await button(page, "Export workflow author input").click();
  const download = await downloadPromise, path = await download.path(); if (!path) throw new Error("The workflow input download is unavailable.");
  return readFile(path, "utf8");
}
test.beforeEach(async ({ page }) => { await page.goto(url); await ready(page); });

test("raw recorder/compiler and separate candidate inputs survive navigation and reload without dispatch", async ({ page }) => {
  const intent = "  Preserve this recording intent\n  ", firstInputs = '  {"incomplete":1e';
  await field(page, "Recording name").fill("  An authored routine  "); await field(page, "Intent").fill(intent);
  await field(page, "Candidate name").fill("  Candidate draft  "); await field(page, "Description").fill("  Original description  ");
  await candidate(page, "Candidate Alpha"); await field(page, "Replay inputs JSON").fill(firstInputs); await field(page, "Promotion slug").fill("unfinished-");
  await candidate(page, "Candidate Beta"); await field(page, "Replay inputs JSON").fill(' {"second":"raw"'); await field(page, "Promotion slug").fill("beta-");
  await candidate(page, "Candidate Alpha"); await expect(field(page, "Replay inputs JSON")).toHaveValue(firstInputs);
  await page.evaluate(() => window.__WORKFLOW_RECOVERY_FIXTURE__.unmount()); await page.evaluate(() => window.__WORKFLOW_RECOVERY_FIXTURE__.mount());
  await ready(page); await expect(field(page, "Intent")).toHaveValue(intent); await expect(field(page, "Replay inputs JSON")).toHaveValue(firstInputs);
  await page.reload(); await ready(page); await expect(field(page, "Recording name")).toHaveValue("  An authored routine  ");
  await expect(field(page, "Candidate name")).toHaveValue("  Candidate draft  "); await expect(field(page, "Promotion slug")).toHaveValue("unfinished-");
  await candidate(page, "Candidate Beta"); await expect(field(page, "Replay inputs JSON")).toHaveValue(' {"second":"raw"');
  expect(await requests(page)).toHaveLength(0); expect(await grants(page)).toHaveLength(0);
});

test("the actual API sends the retained caller id and exact original args after a confirmed backup", async ({ page }) => {
  await compiler(page, "  Original candidate  ", "  Original description  "); const sent = await sendCompile(page), grant = (await grants(page))[0];
  const savedAtGrant = JSON.parse(grant.originalCopyAtGrant!);
  expect(savedAtGrant.draft.pending.outcome).toBe("UNKNOWN");
  expect(JSON.parse(savedAtGrant.draft.pending.requestJson)).toMatchObject({ request_id: sent.request.request_id, action: "compile",
    project_id: sent.request.project_id, generation: sent.request.generation, revision: sent.request.revision,
    args: { name: "Original candidate", description: "Original description", trace_ids: ["trace-A"] } });
  expect(Object.keys(sent.request).sort()).toEqual(["project_id", "generation", "revision", "request_id", "action", "args", "effect_grant"].sort());
  expect(grant.request.subject).toBe("workflow.compile"); expect(grant.originalCopyAtGrant).not.toContain("effect_grant");
  expect(sent.request.effect_grant).toMatch(/^[0-9a-f-]{36}$/); await expect(retained(page)).toContainText(sent.request.request_id);
  await page.evaluate(index => window.__WORKFLOW_RECOVERY_FIXTURE__.resolve(index), sent.index);
  await expect(retained(page)).toHaveCount(0); await expect(field(page, "Candidate name")).toHaveValue("");
  const receipt = page.getByRole("region", { name: "Last canonical workflow receipt", exact: true });
  await expect(receipt).toContainText(sent.request.request_id); await expect(receipt).toContainText("synthetic-broker-1");
  expect((await ownCopy(page)).copy.draft.lastOutcome.requestJson).toBe(savedAtGrant.draft.pending.requestJson);
});

test("incomplete replay JSON remains raw and cannot request a grant or invoke a workflow", async ({ page }) => {
  await writeConsent(page).check(); await candidate(page, "Candidate Alpha"); const raw = '  {"value":1e';
  await field(page, "Replay inputs JSON").fill(raw); await button(page, "Replay through Broker").click();
  await expect(page.getByRole("alert")).toContainText("Replay inputs must be valid JSON");
  expect(await requests(page)).toHaveLength(0); expect(await grants(page)).toHaveLength(0);
  await page.reload(); await ready(page); await expect(field(page, "Replay inputs JSON")).toHaveValue(raw);
});

test("native structured UNKNOWN diagnostics and original identity survive refresh, export and reload", async ({ page }) => {
  await compiler(page); const sent = await sendCompile(page), message = "  Original native uncertainty\n  ";
  await nativeFailure(page, sent, false, "unavailable", message);
  await expect(retained(page)).toContainText("UNKNOWN"); await expect(retained(page)).toContainText("Original native uncertainty");
  await expect(button(page, "Compile candidate")).toBeDisabled();
  const reads = await page.evaluate(() => window.__WORKFLOW_RECOVERY_FIXTURE__.overviewReads());
  await button(page, "Refresh").click(); await expect.poll(() => page.evaluate(() => window.__WORKFLOW_RECOVERY_FIXTURE__.overviewReads())).toBeGreaterThan(reads);
  expect(await requests(page)).toHaveLength(1); expect(await grants(page)).toHaveLength(1);
  const exported = JSON.parse(await exportInput(page));
  expect(JSON.parse(exported.draft.pending.requestJson).request_id).toBe(sent.request.request_id);
  expect(exported.draft.pending.diagnostic).toBe(message); expect(exported.draft.pending.failure.receipt.state).toBe("unavailable");
  await page.reload(); await ready(page); await expect(retained(page)).toContainText(sent.request.request_id);
  await expect(retained(page)).toContainText("Original native uncertainty"); await expect(button(page, "Compile candidate")).toBeDisabled();
  expect((await ownCopy(page)).copy.draft.pending.requestJson).toBe(exported.draft.pending.requestJson);
  expect(await requests(page)).toHaveLength(1); expect(await grants(page)).toHaveLength(1);
});

test("a not-observed receipt cannot clear UNKNOWN or manufacture a retry route", async ({ page }) => {
  await compiler(page); const sent = await sendCompile(page); await nativeFailure(page, sent, false, "not_observed");
  await button(page, "Refresh").click(); await expect(retained(page)).toContainText("UNKNOWN");
  await expect(button(page, "Compile candidate")).toBeDisabled();
  await expect(button(page, "Acknowledge known outcome and keep drafting")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /retry original|resend|recheck result/i })).toHaveCount(0);
  expect(await requests(page)).toHaveLength(1); expect((await ownCopy(page)).copy.draft.pending.failure.receipt.state).toBe("not_observed");
});

test("reload while the reply is lost keeps the same operation with no automatic grant or dispatch", async ({ page }) => {
  await compiler(page); const sent = await sendCompile(page), original = (await ownCopy(page)).copy.draft.pending;
  await page.reload(); await ready(page); await expect(retained(page)).toContainText(sent.request.request_id);
  await expect(retained(page)).toContainText("UNKNOWN"); await expect(button(page, "Compile candidate")).toBeDisabled();
  const exported = JSON.parse(await exportInput(page)); expect(exported.draft.pending.requestJson).toBe(original.requestJson);
  expect(exported.draft.pending.sentFields).toEqual(original.sentFields);
  expect(await requests(page)).toHaveLength(1); expect(await grants(page)).toHaveLength(1);
});

test("a success for another generation cannot acknowledge the retained action", async ({ page }) => {
  await compiler(page, "  Preserve original  "); const sent = await sendCompile(page);
  await page.evaluate(index => {
    const reply = window.__WORKFLOW_RECOVERY_FIXTURE__.syntheticResponse(index);
    reply.receipt.identity.generation = "018f0000-0000-7000-8000-000000000920";
    return window.__WORKFLOW_RECOVERY_FIXTURE__.resolve(index, reply);
  }, sent.index);
  await expect(retained(page)).toContainText("UNKNOWN"); await expect(retained(page)).toContainText(sent.request.request_id);
  await expect(field(page, "Candidate name")).toHaveValue("  Preserve original  ");
  await expect(page.getByRole("region", { name: "Last canonical workflow receipt", exact: true })).toHaveCount(0);
});

test("an old unversioned success DTO remains unconfirmed instead of being treated as current authority", async ({ page }) => {
  await compiler(page); const sent = await sendCompile(page);
  await page.evaluate(({ index, requestId }) => window.__WORKFLOW_RECOVERY_FIXTURE__.resolve(index, {
    command: "workflow.compile", request_id: requestId, authority: null, result: { old_shape: true },
  }), { index: sent.index, requestId: sent.request.request_id });
  await expect(retained(page)).toContainText("UNKNOWN"); await expect(field(page, "Candidate name")).toHaveValue("Original candidate");
  await expect(button(page, "Compile candidate")).toBeDisabled(); expect(await requests(page)).toHaveLength(1);
});

test("a non-completed receipt cannot acknowledge a live action even when every source identity matches", async ({ page }) => {
  await compiler(page, "  Preserve original until completed  "); const sent = await sendCompile(page);
  await page.evaluate(index => {
    const reply = window.__WORKFLOW_RECOVERY_FIXTURE__.syntheticResponse(index);
    reply.receipt.identity.stage = "dispatched";
    return window.__WORKFLOW_RECOVERY_FIXTURE__.resolve(index, reply);
  }, sent.index);
  await expect(retained(page)).toContainText("UNKNOWN"); await expect(retained(page)).toContainText(sent.request.request_id);
  await expect(field(page, "Candidate name")).toHaveValue("  Preserve original until completed  ");
  await expect(page.getByRole("region", { name: "Last canonical workflow receipt", exact: true })).toHaveCount(0);
  await page.reload(); await ready(page); await expect(retained(page)).toContainText(sent.request.request_id);
  await expect(button(page, "Compile candidate")).toBeDisabled(); expect(await requests(page)).toHaveLength(1);
});

test("a known failure can be acknowledged explicitly without sending or losing the draft", async ({ page }) => {
  await compiler(page); const sent = await sendCompile(page); await nativeRecordedFailure(page, sent, "failed_known");
  await expect(retained(page)).toContainText("FAILED_KNOWN"); await expect(button(page, "Compile candidate")).toBeDisabled();
  await button(page, "Acknowledge known outcome and keep drafting").click();
  await expect(retained(page)).toHaveCount(0); await expect(field(page, "Candidate name")).toHaveValue("Original candidate");
  await expect(button(page, "Compile candidate")).toBeEnabled();
  expect(await requests(page)).toHaveLength(1); expect(await grants(page)).toHaveLength(1);
  const copy = (await ownCopy(page)).copy; expect(copy.draft.lastOutcome.kind).toBe("failed_known");
  expect(JSON.parse(copy.draft.lastOutcome.requestJson).request_id).toBe(sent.request.request_id);
});

test("a retained known-failure flag on reload does not resolve a previous UNKNOWN automatically", async ({ page }) => {
  await compiler(page); const sent = await sendCompile(page); await nativeFailure(page, sent, true);
  await page.reload(); await ready(page); await expect(retained(page)).toContainText("UNKNOWN");
  await expect(button(page, "Acknowledge known outcome and keep drafting")).toHaveCount(0);
  expect(await requests(page)).toHaveLength(1); expect(await grants(page)).toHaveLength(1);
});

test("permission rejection before invoke is a local NOT_SENT result with the original request preserved", async ({ page }) => {
  await compiler(page); await page.evaluate(() => window.__WORKFLOW_RECOVERY_FIXTURE__.holdGrants(true));
  await button(page, "Compile candidate").click(); await expect.poll(async () => (await grants(page)).length).toBe(1);
  const before = (await ownCopy(page)).copy.draft.pending.requestJson;
  await page.evaluate(() => window.__WORKFLOW_RECOVERY_FIXTURE__.releaseGrant(1, "No permission granted before workflow invoke."));
  await expect(retained(page)).toContainText("NOT_SENT"); expect(await requests(page)).toHaveLength(0);
  expect((await ownCopy(page)).copy.draft.pending.requestJson).toBe(before);
  await button(page, "Acknowledge known outcome and keep drafting").click();
  await expect(field(page, "Candidate name")).toHaveValue("Original candidate"); expect(await requests(page)).toHaveLength(0);
});

test("revision drift during permission prevents dispatch without rewriting the original source stamp", async ({ page }) => {
  await compiler(page); await page.evaluate(() => window.__WORKFLOW_RECOVERY_FIXTURE__.holdGrants(true));
  await button(page, "Compile candidate").click(); await expect.poll(async () => (await grants(page)).length).toBe(1);
  const before = (await ownCopy(page)).copy.draft.pending.requestJson;
  await page.evaluate(() => window.__WORKFLOW_RECOVERY_FIXTURE__.setSource("revision", 13));
  await page.evaluate(() => window.__WORKFLOW_RECOVERY_FIXTURE__.releaseGrant(1));
  await expect(retained(page)).toContainText("NOT_SENT"); await expect(retained(page)).toContainText("revision 12");
  expect((await ownCopy(page)).copy.draft.pending.requestJson).toBe(before); expect(await requests(page)).toHaveLength(0);
});

test("leaving the editor while permission is pending prevents a late dispatch", async ({ page }) => {
  await compiler(page); await page.evaluate(() => window.__WORKFLOW_RECOVERY_FIXTURE__.holdGrants(true));
  await button(page, "Compile candidate").click(); await expect.poll(async () => (await grants(page)).length).toBe(1);
  await page.evaluate(() => window.__WORKFLOW_RECOVERY_FIXTURE__.unmount());
  await page.evaluate(() => window.__WORKFLOW_RECOVERY_FIXTURE__.releaseGrant(1));
  await page.evaluate(() => window.__WORKFLOW_RECOVERY_FIXTURE__.mount()); await ready(page);
  await expect(retained(page)).toContainText("NOT_SENT"); expect(await requests(page)).toHaveLength(0);
  await expect(field(page, "Candidate name")).toHaveValue("Original candidate");
});

test("a late reply settles only its original owner and does not replace another generation's input", async ({ page }) => {
  await compiler(page); const sent = await sendCompile(page), original = await page.evaluate(() => window.__WORKFLOW_RECOVERY_FIXTURE__.project());
  await page.evaluate(() => window.__WORKFLOW_RECOVERY_FIXTURE__.setSource("generation", "018f0000-0000-7000-8000-000000000921")); await ready(page);
  await field(page, "Candidate name").fill("New generation author input");
  await page.evaluate(index => window.__WORKFLOW_RECOVERY_FIXTURE__.resolve(index), sent.index);
  await expect(field(page, "Candidate name")).toHaveValue("New generation author input");
  await expect(page.getByRole("region", { name: "Last canonical workflow receipt", exact: true })).toHaveCount(0);
  await expect(retained(page)).toHaveCount(0); await expect(writeConsent(page)).not.toBeChecked();
  await page.evaluate(generation => window.__WORKFLOW_RECOVERY_FIXTURE__.setSource("generation", generation), original.generation); await ready(page);
  await expect(page.getByRole("region", { name: "Last canonical workflow receipt", exact: true })).toContainText(sent.request.request_id);
});

test("author changes made while a compile is pending survive acknowledgment and reload", async ({ page }) => {
  await compiler(page); const sent = await sendCompile(page);
  await field(page, "Candidate name").fill("  Later candidate edit  "); await field(page, "Description").fill("  Later description  ");
  await page.evaluate(index => window.__WORKFLOW_RECOVERY_FIXTURE__.resolve(index), sent.index);
  await expect(retained(page)).toHaveCount(0); await expect(field(page, "Candidate name")).toHaveValue("  Later candidate edit  ");
  await expect(field(page, "Description")).toHaveValue("  Later description  ");
  expect((await requests(page))[0].request.args.name).toBe("Original candidate");
  await page.reload(); await ready(page); await expect(field(page, "Candidate name")).toHaveValue("  Later candidate edit  ");
  await expect(field(page, "Description")).toHaveValue("  Later description  "); expect(await requests(page)).toHaveLength(1);
});

test("same-turn duplicate clicks create one original request and one grant", async ({ page }) => {
  await compiler(page);
  await button(page, "Compile candidate").evaluate((element: HTMLButtonElement) => { element.click(); element.click(); });
  await expect.poll(async () => (await requests(page)).length).toBe(1); expect(await grants(page)).toHaveLength(1);
});

test("a silently dropped pending backup invokes neither permission nor workflow and keeps raw input exportable", async ({ page }) => {
  await compiler(page, "  Do not lose me  ");
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (this === localStorage && key.startsWith("motionwright.workflow-copy.v1:") && JSON.parse(value).draft.pending) return;
      original.call(this, key, value);
    };
    window.__WORKFLOW_RESTORE_STORAGE__ = () => { Storage.prototype.setItem = original; };
  });
  await button(page, "Compile candidate").click(); await expect(retained(page)).toContainText("NOT_SENT");
  await expect(page.getByRole("alert").filter({ hasText: "Workflow backup needs attention" })).toBeVisible();
  expect(await grants(page)).toHaveLength(0); expect(await requests(page)).toHaveLength(0);
  const exported = JSON.parse(await exportInput(page)); expect(exported.draft.fields["compile.name"].raw).toBe("  Do not lose me  ");
  const originalId = JSON.parse(exported.draft.pending.requestJson).request_id;
  await page.evaluate(() => window.__WORKFLOW_RESTORE_STORAGE__!()); await button(page, "Confirm workflow backup again").click();
  expect(JSON.parse((await ownCopy(page)).copy.draft.pending.requestJson).request_id).toBe(originalId);
  expect(await requests(page)).toHaveLength(0); expect(await grants(page)).toHaveLength(0);
});

test("final-copy corruption after pointer write cannot authorize dispatch or be overwritten by repair", async ({ page }) => {
  await compiler(page);
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      original.call(this, key, value);
      if (this === sessionStorage && key.startsWith("motionwright.workflow-writer.v1:")) {
        const copyKey = "motionwright.workflow-copy.v1:" + key.slice("motionwright.workflow-writer.v1:".length) + ":" + value;
        const raw = localStorage.getItem(copyKey);
        if (raw && JSON.parse(raw).draft.pending) original.call(localStorage, copyKey, raw + " ");
      }
    };
    window.__WORKFLOW_RESTORE_STORAGE__ = () => { Storage.prototype.setItem = original; };
  });
  await button(page, "Compile candidate").click(); await expect(retained(page)).toContainText("NOT_SENT");
  const corrupt = await ownCopy(page); expect(corrupt.raw.endsWith(" ")).toBe(true);
  await page.evaluate(() => window.__WORKFLOW_RESTORE_STORAGE__!()); await button(page, "Confirm workflow backup again").click();
  expect((await ownCopy(page)).raw).toBe(corrupt.raw); expect(await requests(page)).toHaveLength(0); expect(await grants(page)).toHaveLength(0);
  await page.reload(); await ready(page); await expect(retained(page)).toContainText("UNKNOWN"); await expect(button(page, "Compile candidate")).toBeDisabled();
});

test("importing an original UNKNOWN into another tab merges input and never dispatches it", async ({ page, context }) => {
  await compiler(page); const sent = await sendCompile(page); await nativeFailure(page, sent);
  const raw = await exportInput(page), second = await context.newPage(); await second.goto(url); await ready(second);
  await field(second, "Candidate name").fill("Later local tab input");
  await field(second, "Import workflow author input").setInputFiles({ name: "workflow-author-input.json", mimeType: "application/json", buffer: Buffer.from(raw) });
  await expect(retained(second)).toContainText(sent.request.request_id); await expect(retained(second)).toContainText("UNKNOWN");
  await expect(field(second, "Candidate name")).toHaveValue("Later local tab input");
  expect(await requests(second)).toHaveLength(0); expect(await grants(second)).toHaveLength(0);
  await expect(button(second, "Compile candidate")).toBeDisabled(); await second.close();
});

test("a corrupt pointed recovery copy stays exportable and blocks new action identity", async ({ page }) => {
  await compiler(page); const sent = await sendCompile(page), copy = await ownCopy(page), corrupt = '{"original-action-copy-truncated":';
  await page.evaluate(({ key, corrupt }) => localStorage.setItem(key, corrupt), { key: copy.key, corrupt });
  await page.reload(); await ready(page);
  await expect(page.getByRole("alert").filter({ hasText: "original selected copy is unreadable" })).toBeVisible();
  await expect(button(page, "Compile candidate")).toBeDisabled();
  const downloadPromise = page.waitForEvent("download"); await button(page, "Export original unreadable copy").click();
  const download = await downloadPromise, path = await download.path(); if (!path) throw new Error("No original-copy download.");
  expect(await readFile(path, "utf8")).toBe(corrupt); expect((await requests(page))[0].request.request_id).toBe(sent.request.request_id);
  expect(await requests(page)).toHaveLength(1); expect(await grants(page)).toHaveLength(1);
});

test("an import finishing during an original invocation reports that it was not applied and preserves custody", async ({ page }) => {
  await compiler(page); const exported = await exportInput(page);
  await page.evaluate(() => {
    const original = File.prototype.text;
    File.prototype.text = function() {
      return new Promise<string>((resolve, reject) => {
        window.__WORKFLOW_RELEASE_FILE__ = () => {
          File.prototype.text = original; void original.call(this).then(resolve, reject);
        };
      });
    };
  });
  await field(page, "Import workflow author input").setInputFiles({ name: "workflow-author-input.json", mimeType: "application/json", buffer: Buffer.from(exported) });
  await expect(page.getByRole("status").filter({ hasText: "Reading author input" })).toBeVisible();
  const sent = await sendCompile(page), pending = (await ownCopy(page)).copy.draft.pending.requestJson;
  await page.evaluate(() => window.__WORKFLOW_RELEASE_FILE__!());
  await expect(page.getByRole("alert").filter({ hasText: "imported or selected input was not applied" })).toBeVisible();
  expect((await ownCopy(page)).copy.draft.pending.requestJson).toBe(pending);
  await expect(retained(page)).toContainText(sent.request.request_id); await expect(field(page, "Candidate name")).toHaveValue("Original candidate");
  expect(await requests(page)).toHaveLength(1); expect(await grants(page)).toHaveLength(1);
});

test("plan-only uses the same original caller protocol without a mutation grant", async ({ page }) => {
  await button(page, "Plan only").click(); await expect.poll(async () => (await requests(page)).length).toBe(1);
  const sent = (await requests(page))[0]; expect(sent.request.action).toBe("proposal_plan"); expect(sent.request.effect_grant).toBeNull();
  expect(await grants(page)).toHaveLength(0); expect(JSON.parse((await ownCopy(page)).copy.draft.pending.requestJson).request_id).toBe(sent.request.request_id);
  await page.evaluate(index => window.__WORKFLOW_RECOVERY_FIXTURE__.resolve(index), sent.index); await expect(retained(page)).toHaveCount(0);
});

for (const observation of ["not_checked", "not_observed", "unavailable", "dispatching", "outcome_unknown", "future-stage", "completed"] as const) {
  test(`a known rejection with ${observation} cannot release the original workflow action`, async ({ page }) => {
    await compiler(page); const sent = await sendCompile(page), original = (await ownCopy(page)).copy.draft.pending.requestJson;
    if (observation === "not_checked" || observation === "not_observed" || observation === "unavailable")
      await nativeFailure(page, sent, true, observation);
    else await nativeRecordedFailure(page, sent, observation);
    await expect(retained(page)).toContainText("UNKNOWN");
    await expect(button(page, "Acknowledge known outcome and keep drafting")).toHaveCount(0);
    await expect(button(page, "Compile candidate")).toBeDisabled();
    await button(page, "Refresh").click();
    expect((await ownCopy(page)).copy.draft.pending.requestJson).toBe(original);
    const exported = JSON.parse(await exportInput(page));
    expect(exported.draft.pending.outcome).toBe("UNKNOWN");
    expect(exported.draft.pending.requestJson).toBe(original);
    await page.reload(); await ready(page); await writeConsent(page).check();
    await expect(retained(page)).toContainText(sent.request.request_id);
    await expect(retained(page)).toContainText("UNKNOWN");
    await expect(button(page, "Compile candidate")).toBeDisabled();
    expect((await ownCopy(page)).copy.draft.pending.requestJson).toBe(original);
    expect(await requests(page)).toHaveLength(1); expect(await grants(page)).toHaveLength(1);
  });
}

for (const mode of ["silent", "throw", "write-then-throw"] as const) {
  test(`an imported UNKNOWN survives a ${mode} first adoption write and later author edits`, async ({ page, context }, testInfo) => {
    await compiler(page); const sent = await sendCompile(page); await nativeFailure(page, sent);
    const raw = await exportInput(page), source = await ownCopy(page), second = await context.newPage();
    await second.goto(url); await ready(second);
    // An untouched second editor has no confirmed writer. The first adoption
    // write must therefore be observable even when storage drops it silently.
    expect(await second.evaluate(key => sessionStorage.getItem(key), source.pointerKey)).toBeNull();
    await second.evaluate(mode => {
      const original = Storage.prototype.setItem; let writes = 0;
      Storage.prototype.setItem = function(key, value) {
        if (this === localStorage && key.startsWith("motionwright.workflow-copy.v1:")) {
          writes++; document.documentElement.dataset.workflowAdoptionWrites = String(writes);
          if (writes === 1) {
            if (mode === "write-then-throw") original.call(this, key, value);
            if (mode !== "silent") throw new Error("Injected first adoption write failure");
            return;
          }
        }
        original.call(this, key, value);
      };
      window.__WORKFLOW_RESTORE_STORAGE__ = () => { Storage.prototype.setItem = original; };
    }, mode);
    await field(second, "Import workflow author input").setInputFiles({ name: "workflow-author-input.json", mimeType: "application/json", buffer: Buffer.from(raw) });
    await expect(second.getByRole("alert").filter({ hasText: "Workflow backup needs attention" })).toBeVisible();
    expect(await second.evaluate(() => document.documentElement.dataset.workflowAdoptionWrites)).toBe("1");
    await expect(retained(second)).toContainText(sent.request.request_id); await expect(retained(second)).toContainText("UNKNOWN");
    await field(second, "Candidate name").fill("  Later input after failed import backup  ");
    const unconfirmed = JSON.parse(await exportInput(second));
    expect(unconfirmed.draft.pending.requestJson).toBe(source.copy.draft.pending.requestJson);
    expect(unconfirmed.draft.fields["compile.name"].raw).toBe("  Later input after failed import backup  ");
    await second.evaluate(() => window.__WORKFLOW_RESTORE_STORAGE__!());
    await button(second, "Confirm workflow backup again").click();
    await expect(second.getByRole("alert").filter({ hasText: "Workflow backup needs attention" })).toHaveCount(0);
    const confirmed = await ownCopy(second);
    expect(confirmed.writer).not.toBe(source.writer);
    expect(confirmed.copy.draft.pending).toEqual(unconfirmed.draft.pending);
    expect(await second.evaluate(key => localStorage.getItem(key), source.key)).toBe(source.raw);
    await second.reload(); await ready(second); await writeConsent(second).check();
    await expect(retained(second)).toContainText(sent.request.request_id); await expect(retained(second)).toContainText("UNKNOWN");
    await expect(field(second, "Candidate name")).toHaveValue("  Later input after failed import backup  ");
    await expect(button(second, "Compile candidate")).toBeDisabled();
    expect((await ownCopy(second)).copy.draft.pending.requestJson).toBe(source.copy.draft.pending.requestJson);
    expect(await second.evaluate(key => localStorage.getItem(key), source.key)).toBe(source.raw);
    expect(await requests(second)).toHaveLength(0); expect(await grants(second)).toHaveLength(0);
    expect(await requests(page)).toHaveLength(1); expect(await grants(page)).toHaveLength(1);
    if (mode === "silent") await testInfo.attach("workflow-unknown-after-reopen", { body: await second.screenshot(), contentType: "image/png" });
    await second.close();
  });
}
