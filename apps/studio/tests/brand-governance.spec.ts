import { expect, test } from "@playwright/test";

test("Production persists brand policy, non-authoritative taste and a scoped campaign exception", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Brand & taste",exact:true}).click();
  await expect(page.getByRole("heading",{name:/Brand rules and creative taste/})).toBeVisible();
  const revision=page.locator(".production-workspace-header p");
  const before=Number((await revision.innerText()).match(/revision (\d+)/)![1]);

  await page.getByText("Brand identity",{exact:true}).locator("..").locator("input").fill("Pilot brand");
  await page.getByText("Forbidden copy phrases (one per line)").locator("..").locator("textarea").fill("unverified");
  await page.getByRole("button",{name:"Save brand policy"}).click();
  await expect(revision).toContainText(`revision ${before+1}`);
  await expect(page.getByRole("alert")).toHaveCount(0);

  await page.getByRole("button",{name:"Add preference"}).click();
  await page.getByText("Taste label",{exact:true}).locator("..").locator("input").fill("Editorial exploration");
  await page.getByText("Axis",{exact:true}).locator("..").locator("input").fill("pace");
  await page.getByText("Preference",{exact:true}).locator("..").locator("input").fill("Fast entry");
  await page.getByRole("button",{name:"Save taste"}).click();
  await expect(revision).toContainText(`revision ${before+2}`);

  await page.getByText("Campaign",{exact:true}).locator("..").locator("input").fill("Pilot cut");
  await page.getByText("Recorded author",{exact:true}).locator("..").locator("input").fill("Review operator");
  await page.getByText("Reason for exception",{exact:true}).locator("..").locator("textarea").fill("Specific quote");
  const savedRule=page.getByRole("combobox",{name:"Brand rule",exact:true});
  const explicitRule=await savedRule.locator("option").evaluateAll(options=>options.map(option=>(option as HTMLOptionElement).value).find(Boolean));
  expect(explicitRule).toBeTruthy();await savedRule.selectOption(explicitRule!);
  await page.getByRole("button",{name:"Record scene-scoped exception"}).click();
  await expect(revision).toContainText(`revision ${before+3}`);
  await expect(page.getByText(/Pilot cut ·/)).toBeVisible();

  await page.getByRole("button",{name:"Production plan",exact:true}).click();
  await page.getByRole("button",{name:"Brand & taste",exact:true}).click();
  await expect(page.getByText(/Pilot cut ·/)).toBeVisible();
  await page.screenshot({path:testInfo.outputPath("brand-policy-and-campaign-exception.png"),fullPage:true});
});

test("An explicitly cleared brand rule stays empty through navigation and reload", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Production", exact: true }).click();
  await page.getByRole("button", { name: "Brand & taste", exact: true }).click();
  const revision = page.locator(".production-workspace-header p");
  const before = Number((await revision.innerText()).match(/revision (\d+)/)![1]);
  await page.getByLabel("Brand identity", { exact: true }).fill("Rule selection regression");
  await page.getByLabel("Forbidden copy phrases (one per line)", { exact: true })
    .fill("unobserved_rule_selection_alpha\nunobserved_rule_selection_beta");
  await page.getByRole("button", { name: "Save brand policy", exact: true }).click();
  await expect(revision).toContainText("revision " + (before + 1));
  const rule = page.getByRole("combobox",{name:"Brand rule", exact: true });
  const choices = await rule.locator("option").evaluateAll(options => options.map(option => (option as HTMLOptionElement).value).filter(Boolean));
  expect(choices.length).toBeGreaterThanOrEqual(2);
  await rule.selectOption(choices[1]);
  await page.getByLabel("Campaign", { exact: true }).fill("Retained campaign");
  await page.getByLabel("Recorded author", { exact: true }).fill("Test author");
  await page.getByLabel("Reason for exception", { exact: true }).fill("Keep the rule deliberately unselected.");
  const submit = page.getByRole("button", { name: "Record scene-scoped exception", exact: true });
  await expect(submit).toBeEnabled();
  await rule.selectOption("");
  await expect(rule).toHaveValue("");
  await expect(submit).toBeDisabled();
  await page.getByRole("button", { name: "Production plan", exact: true }).click();
  await page.getByRole("button", { name: "Brand & taste", exact: true }).click();
  await expect(rule).toHaveValue(""); await expect(submit).toBeDisabled();
  await expect(revision).toContainText("revision " + (before + 1));
  await page.reload();
  await page.getByRole("button", { name: "Production", exact: true }).click();
  await page.getByRole("button", { name: "Brand & taste", exact: true }).click();
  await expect(rule).toHaveValue(""); await expect(submit).toBeDisabled();
  await expect(page.getByLabel("Campaign", { exact: true })).toHaveValue("Retained campaign");
});
