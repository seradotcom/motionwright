import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

// Synthetic component transport only; no native visual or SQLite claim.
test.setTimeout(20_000);
const go=async(page:Page,mode="normal")=>{
  await page.goto("/tests/fixtures/native-finding-recovery.html?mode="+mode);
  await page.getByRole("button",{name:"Inspect current native frames",exact:true}).click();
  await expect(page.getByRole("button",{name:"Inspect native frame 0",exact:true})).toBeVisible();
};
async function author(page:Page) {
  await page.getByRole("combobox",{name:"Severity",exact:true}).selectOption("blocking");
  await page.getByRole("combobox",{name:"Confidence",exact:true}).selectOption("uncertain");
  await page.getByRole("combobox",{name:"Affected object",exact:true}).selectOption({index:1});
  await page.getByLabel("Violated constraint",{exact:true}).fill("  Contrast of the authored object  ");
  await page.getByLabel("What is visible in this frame",{exact:true}).fill("Exact observation\nunfinished 1e");
  await page.getByLabel("Proposed localized repair",{exact:true}).fill("Move only the selected object after preview.");
}
async function preserved(page:Page) {
  await expect(page.getByRole("combobox",{name:"Severity",exact:true})).toHaveValue("blocking",{timeout:2000});
  await expect(page.getByRole("combobox",{name:"Confidence",exact:true})).toHaveValue("uncertain",{timeout:2000});
  await expect(page.getByLabel("Violated constraint",{exact:true})).toHaveValue("  Contrast of the authored object  ",{timeout:2000});
  await expect(page.getByLabel("What is visible in this frame",{exact:true})).toHaveValue("Exact observation\nunfinished 1e",{timeout:2000});
  await expect(page.getByLabel("Proposed localized repair",{exact:true})).toHaveValue("Move only the selected object after preview.",{timeout:2000});
}
test("six finding fields survive navigation and a document reload",async({page})=>{
  await go(page);await author(page);const object=await page.getByRole("combobox",{name:"Affected object",exact:true}).inputValue();
  await page.getByRole("button",{name:"Navigate away",exact:true}).click();await page.getByRole("button",{name:"Return to inspection",exact:true}).click();
  await preserved(page);await expect(page.getByRole("combobox",{name:"Affected object",exact:true})).toHaveValue(object);
  await page.reload();await preserved(page);await expect(page.getByRole("combobox",{name:"Affected object",exact:true})).toHaveValue(object);
  await expect(page.getByRole("button",{name:"Record source-anchored review",exact:true})).toBeDisabled();
});
test("changing native source never silently reassigns authored finding text",async({page})=>{
  await go(page);await author(page);await page.getByRole("combobox",{name:"Affected object",exact:true}).selectOption("");
  await page.getByRole("button",{name:"Switch scene",exact:true}).click();
  await page.getByRole("button",{name:"Inspect current native frames",exact:true}).click();
  await expect(page.getByRole("button",{name:/Inspect native frame/}).first()).toBeVisible();
  await expect(page.getByRole("button",{name:"Record source-anchored review",exact:true})).toBeDisabled({timeout:2000});
  await preserved(page);
  await page.getByRole("button",{name:"Use current frame for this finding",exact:true}).click();
  await expect(page.getByRole("button",{name:"Record source-anchored review",exact:true})).toBeEnabled();
  const bound=await page.getByLabel("Finding source reference",{exact:true}).textContent();
  await page.getByRole("combobox",{name:"Compare with",exact:true}).selectOption("2");
  await page.getByRole("combobox",{name:"Inspection mode",exact:true}).selectOption("difference");
  await expect(page.getByLabel("Finding source reference",{exact:true})).toHaveText(bound!);
  await expect(page.getByText("Your finding records the selected target frame only.",{exact:false})).toBeVisible();
  await preserved(page);
  await page.getByRole("button",{name:"Switch profile",exact:true}).click();
  await page.getByRole("button",{name:"Inspect current native frames",exact:true}).click();
  await expect(page.getByRole("button",{name:"Record source-anchored review",exact:true})).toBeDisabled();
  await preserved(page);
});
test("a void response leaves an unknown finding outcome instead of an implied save",async({page})=>{
  await go(page,"void");await author(page);
  await page.getByRole("button",{name:"Record source-anchored review",exact:true}).click();
  await expect(page.getByRole("region",{name:"Unconfirmed native finding",exact:true})).toBeVisible({timeout:2000});
  await preserved(page);
  expect(await page.evaluate(()=>(window as unknown as {__NATIVE_FINDING_TEST__:{calls:number}}).__NATIVE_FINDING_TEST__.calls)).toBe(1);
  await page.reload();
  await expect(page.getByRole("region",{name:"Unconfirmed native finding",exact:true})).toBeVisible();
  expect(await page.evaluate(()=>(window as unknown as {__NATIVE_FINDING_TEST__:{calls:number}}).__NATIVE_FINDING_TEST__.calls)).toBe(0);
});
test("the original delayed response retains later finding input after unmount",async({page})=>{
  await go(page,"held");await author(page);await page.getByRole("button",{name:"Record source-anchored review",exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>(window as unknown as {__NATIVE_FINDING_TEST__:{calls:number}}).__NATIVE_FINDING_TEST__.calls)).toBe(1);
  await page.getByLabel("What is visible in this frame",{exact:true}).fill("Later observation after dispatch.");
  await page.getByRole("button",{name:"Navigate away",exact:true}).click();await page.getByRole("button",{name:"Release original response",exact:true}).click();
  await page.getByRole("button",{name:"Return to inspection",exact:true}).click();
  await expect(page.getByLabel("What is visible in this frame",{exact:true})).toHaveValue("Later observation after dispatch.");
  await expect(page.getByRole("status").filter({hasText:"Saved source-anchored review"})).toBeVisible();
  await page.reload();await expect(page.getByLabel("What is visible in this frame",{exact:true})).toHaveValue("Later observation after dispatch.");
});
for(const kind of ["data","pointer"] as const)test("silent "+kind+" backup failure prevents finding dispatch",async({page})=>{
  await page.addInitScript(fault=>{
    const original=Storage.prototype.setItem;
    Storage.prototype.setItem=function(key:string,value:string){
      if((fault==="data"&&this===localStorage&&key.startsWith("motionwright.native-finding-recovery.v1:"))
        ||(fault==="pointer"&&this===sessionStorage&&key.startsWith("motionwright.native-finding-writer.v1:")))return;
      return original.call(this,key,value);
    };
  },kind);
  await go(page);await author(page);
  await expect(page.getByRole("button",{name:"Record source-anchored review",exact:true})).toBeDisabled();
  await expect(page.getByRole("button",{name:"Retry finding recovery",exact:true})).toBeVisible();
  await preserved(page);
  expect(await page.evaluate(()=>(window as unknown as {__NATIVE_FINDING_TEST__:{calls:number}}).__NATIVE_FINDING_TEST__.calls)).toBe(0);
});
test("corrupt earlier bytes are retained and block a new finding save",async({page})=>{
  await go(page);await author(page);
  const corrupt=await page.evaluate(()=>{
    const key=Object.keys(localStorage).find(value=>value.startsWith("motionwright.native-finding-recovery.v1:"))!;
    const raw='{"unreadable finding":';localStorage.setItem(key,raw);return {key,raw};
  });
  await page.reload();
  await expect(page.getByRole("button",{name:"Retry finding recovery",exact:true})).toBeVisible();
  await expect(page.getByRole("button",{name:"Record source-anchored review",exact:true})).toBeDisabled();
  expect(await page.evaluate(key=>localStorage.getItem(key),corrupt.key)).toBe(corrupt.raw);
  expect(await page.evaluate(()=>(window as unknown as {__NATIVE_FINDING_TEST__:{calls:number}}).__NATIVE_FINDING_TEST__.calls)).toBe(0);
});
test("another writer pointer is preserved while later finding text stays visible",async({page})=>{
  await go(page);await author(page);
  const foreign=await page.evaluate(()=>{
    const key=Object.keys(sessionStorage).find(value=>value.startsWith("motionwright.native-finding-writer.v1:"))!;
    const writer=crypto.randomUUID();sessionStorage.setItem(key,writer);return {key,writer};
  });
  await page.getByLabel("What is visible in this frame",{exact:true}).fill("Later input must not overwrite that writer.");
  await expect(page.getByRole("button",{name:"Record source-anchored review",exact:true})).toBeDisabled();
  expect(await page.evaluate(key=>sessionStorage.getItem(key),foreign.key)).toBe(foreign.writer);
  await expect(page.getByLabel("What is visible in this frame",{exact:true})).toHaveValue("Later input must not overwrite that writer.");
});
test("a listed UNKNOWN survives a first-read error and same-writer replacement without losing its exact downloadable bytes",async({page})=>{
  await go(page,"void");await author(page);
  await page.getByRole("button",{name:"Record source-anchored review",exact:true}).click();
  await expect(page.getByRole("region",{name:"Unconfirmed native finding",exact:true})).toBeVisible();
  const original=await page.evaluate(()=>{
    const pointerKey=Object.keys(sessionStorage).find(key=>key.startsWith("motionwright.native-finding-writer.v1:"))!,writer=sessionStorage.getItem(pointerKey)!;
    const key=Object.keys(localStorage).find(key=>key.startsWith("motionwright.native-finding-recovery.v1:")&&key.endsWith(":"+writer))!,raw=localStorage.getItem(key)!;
    return {pointerKey,writer,key,raw};
  });
  expect(JSON.parse(original.raw).draft.pending.outcome).toBe("UNKNOWN");
  await page.addInitScript(key=>{
    const get=Storage.prototype.getItem;let first=true;
    Storage.prototype.getItem=function(candidate:string){
      if(first&&this===localStorage&&candidate===key){first=false;throw new Error("First NativeFinding recovery read unavailable");}
      return get.call(this,candidate);
    };
  },original.key);
  await page.reload();
  await expect(page.getByRole("button",{name:"Retry finding recovery",exact:true})).toBeVisible();
  await page.getByText("Finding recovery copies",{exact:true}).click();
  const originalRow=page.getByRole("listitem").filter({hasText:original.writer});
  await expect(originalRow).toContainText("Readable finding input");
  await author(page);
  const later="Later input remains separate from the listed UNKNOWN.\nunfinished 1e";
  await page.getByLabel("What is visible in this frame",{exact:true}).fill(later);
  const replacementRaw=await page.evaluate(original=>{
    const replacement=JSON.parse(original.raw);replacement.draft.pending=null;const raw=JSON.stringify(replacement);
    localStorage.setItem(original.key,raw);
    window.dispatchEvent(new StorageEvent("storage",{key:original.key,oldValue:original.raw,newValue:raw,storageArea:localStorage,url:location.href}));
    return raw;
  },original);
  await page.getByRole("button",{name:"Retry finding recovery",exact:true}).click();
  // Causal RED on the original hook: it adopts pending:null, clears this error
  // and publishes a new pointer before any fresh saved-outcome inspection.
  await expect(page.getByRole("button",{name:"Retry finding recovery",exact:true})).toBeVisible({timeout:2000});
  await expect(page.getByRole("button",{name:"Record source-anchored review",exact:true})).toBeDisabled();
  expect(await page.evaluate(key=>sessionStorage.getItem(key),original.pointerKey)).toBe(original.writer);
  expect(await page.evaluate(key=>localStorage.getItem(key),original.key)).toBe(replacementRaw);
  await expect(page.getByLabel("What is visible in this frame",{exact:true})).toHaveValue(later);
  const downloading=page.waitForEvent("download");await originalRow.getByRole("button",{name:"Download copy",exact:true}).click();
  const download=await downloading,path=await download.path();if(!path)throw new Error("Original finding download was unavailable.");
  const exported=JSON.parse(await readFile(path,"utf8"));expect(exported.storage_key).toBe(original.key);expect(exported.raw).toBe(original.raw);
  await page.evaluate(original=>{localStorage.setItem(original.key,original.raw);},original);
  await page.getByRole("button",{name:"Retry finding recovery",exact:true}).click();
  await expect(page.getByRole("region",{name:"Unconfirmed native finding",exact:true})).toBeVisible();
  await expect(page.getByLabel("What is visible in this frame",{exact:true})).toHaveValue(later);
  await expect(page.getByRole("combobox",{name:"Severity",exact:true})).toHaveValue("blocking");
  await expect(page.getByRole("combobox",{name:"Confidence",exact:true})).toHaveValue("uncertain");
  await expect(page.getByLabel("Violated constraint",{exact:true})).toHaveValue("  Contrast of the authored object  ");
  await expect(page.getByLabel("Proposed localized repair",{exact:true})).toHaveValue("Move only the selected object after preview.");
  await expect(page.getByRole("combobox",{name:"Affected object",exact:true})).toHaveValue(JSON.parse(original.raw).draft.input.nodeId);
  await expect(page.getByRole("button",{name:"Record source-anchored review",exact:true})).toBeDisabled();
  expect(await page.evaluate(key=>localStorage.getItem(key),original.key)).toBe(original.raw);
  expect(await page.evaluate(()=>(window as unknown as {__NATIVE_FINDING_TEST__:{calls:number}}).__NATIVE_FINDING_TEST__.calls)).toBe(0);
});
