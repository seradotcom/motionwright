import { expect, test } from "@playwright/test";

test("Measured-data editor never generates unverifiable demo metrics or inferred bar axes",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Measured data",exact:true}).click();
  await expect(page.getByRole("heading",{name:"Measured data / source-bound graphics"})).toBeVisible();
  await expect(page.getByLabel("Measured source asset")).toBeVisible();
  await expect(page.getByLabel("Measured numeric rows JSON")).toHaveValue("[]");
  await expect(page.getByLabel("Measured numeric rows JSON")).toHaveAttribute("readonly","");
  await expect(page.getByLabel("Measured source file verification")).toBeVisible();
  await expect(page.getByRole("button",{name:"Register measured source"})).toBeDisabled();
  await page.getByLabel("Measured output kind").selectOption("bar");
  await expect(page.getByLabel("Measured axis minimum")).toHaveValue("");
  await expect(page.getByLabel("Measured axis maximum")).toHaveValue("");
  await expect(page.getByLabel("Measured maximum bar width")).toHaveValue("");
  await expect(page.getByRole("button",{name:"Bind measured output"})).toBeDisabled();
  await expect(page.getByText(/source file's measurements/)).toBeVisible();
});
