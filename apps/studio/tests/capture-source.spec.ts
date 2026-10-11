import { expect,test } from "@playwright/test";

test("Capture source panel requires imported original evidence and never invents a screenshot",async({page},testInfo)=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Capture sources",exact:true}).click();
  await expect(page.getByRole("heading",{name:"First-party capture provenance"})).toBeVisible();
  await expect(page.getByText("No imported SHA-256 PNG source asset is available.")).toBeVisible();
  await expect(page.getByText("No imported SHA-256 JSON capture receipt is available.")).toBeVisible();
  await expect(page.getByText("No imported SHA-256 JSON fixture-state asset is available.")).toBeVisible();
  await expect(page.getByLabel("Capture fixture-state asset")).toBeVisible();
  await expect(page.getByText("No authored synthetic first-party source claim is available.")).toBeVisible();
  await expect(page.getByRole("button",{name:"Register first-party capture source"})).toBeDisabled();
  await expect(page.getByRole("button",{name:"Record source-revision claim"})).toBeDisabled();

  await page.getByLabel("First-party capture claim")
    .fill("The original, local Studio Production UI was visible in an isolated browser.");
  await expect(page.getByRole("button",{name:"Record source-revision claim"})).toBeEnabled();
  await page.getByRole("button",{name:"Record source-revision claim"}).click();
  await expect(page.getByText("No authored synthetic first-party source claim is available.")).toHaveCount(0);
  await expect(page.getByLabel("Capture source claim").locator("option")).toHaveCount(2);
  await expect(page.getByRole("button",{name:"Register first-party capture source"})).toBeDisabled();
  await expect(page.getByText("No first-party evidence sources registered in this project.")).toBeVisible();
  await page.screenshot({path:testInfo.outputPath("capture-source-admission-empty.png"),fullPage:true});
});
