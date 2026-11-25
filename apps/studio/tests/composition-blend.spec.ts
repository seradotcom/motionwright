import { expect, test, type Locator } from "@playwright/test";

/** Rasterize the actual component SVG with Chromium, then compare against
 * independently calculated channels. No generated golden or Native claim. */
async function pixels(svg: Locator, points: [number,number][]) {
  return svg.evaluate(async (node,points) => {
    const copy=node.cloneNode(true) as SVGSVGElement;
    copy.setAttribute("xmlns","http://www.w3.org/2000/svg");copy.setAttribute("width","100");copy.setAttribute("height","100");
    const url=URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(copy)],{type:"image/svg+xml"}));
    try {
      const img=new Image();img.src=url;await img.decode();
      const canvas=document.createElement("canvas");canvas.width=canvas.height=100;
      const ctx=canvas.getContext("2d")!;ctx.drawImage(img,0,0,100,100);
      return points.map(([x,y])=>[...ctx.getImageData(x,y,1,1).data]);
    } finally {URL.revokeObjectURL(url);}
  },points);
}
function near(actual:number[],expected:number[]) {
  expect(actual).toHaveLength(expected.length);
  actual.forEach((value,i)=>expect(Math.abs(value-expected[i]),"channel "+i).toBeLessThanOrEqual(2));
}
test("all saved modes blend the full half-opacity object against the retained surface",async({page})=>{
  await page.goto("/tests/fixtures/composition-blend.html");
  const svg=page.getByRole("img",{name:"Authored composition blend",exact:true});
  const backdrop=[64,96,128],source=[128,192,64],a=.5;
  for(const mode of ["normal","multiply","screen","add"]) {
    await page.getByLabel("Fixture blend").selectOption(mode);
    const expected=backdrop.map((b,i)=>{
      const s=source[i];
      if(mode==="add")return Math.min(255,b+a*s);
      const blend=mode==="multiply"?b*s/255:mode==="screen"?255-(255-b)*(255-s)/255:s;
      return (1-a)*b+a*blend;
    });
    const [overlap,outside]=await pixels(svg,[[50,50],[10,10]]);
    near(overlap,[...expected,255]);near(outside,[...backdrop,255]);
    await expect(svg).toHaveCSS("isolation","isolate");
  }
});
test("clipping, layer order and camera framing remain applied before blend",async({page})=>{
  await page.goto("/tests/fixtures/composition-blend.html");
  const svg=page.getByRole("img",{name:"Authored composition blend",exact:true});
  await page.getByLabel("Fixture blend").selectOption("multiply");
  await page.getByLabel("Clip object",{exact:true}).check();
  let [inside,clipped]=await pixels(svg,[[50,50],[30,50]]);
  near(inside,[48,84,80,255]);near(clipped,[64,96,128,255]);
  await page.getByLabel("Add lower layer",{exact:true}).check();
  [inside,clipped]=await pixels(svg,[[50,50],[30,50]]);
  near(inside,[25.53,44.7,42.53,255]);near(clipped,[34,51,68,255]);
  await page.getByLabel("Move camera",{exact:true}).check();
  [inside,clipped]=await pixels(svg,[[35,50],[55,50]]);
  near(inside,[25.53,44.7,42.53,255]);near(clipped,[34,51,68,255]);
});
test("transparent retained surfaces stay independent from the surrounding page",async({page})=>{
  await page.goto("/tests/fixtures/composition-blend.html");
  const svg=page.getByRole("img",{name:"Authored composition blend",exact:true});
  await page.getByLabel("Transparent surface",{exact:true}).check();
  await page.getByLabel("Fixture blend").selectOption("multiply");
  const ab=128/255,as=.5,alpha=as+ab*(1-as),cb=[64,96,128],cs=[128,192,64];
  const expected=cb.map((b,i)=>((1-as)*ab*b+as*(1-ab)*cs[i]+as*ab*b*cs[i]/255)/alpha);
  const [overlap,outside]=await pixels(svg,[[50,50],[10,10]]);
  near(overlap,[...expected,alpha*255]);near(outside,[...cb,128]);
});
test("output profiles use the saved background and unsupported group poses show no flattened SVG",async({page})=>{
  await page.goto("/tests/fixtures/composition-blend.html");
  const output=page.getByRole("img",{name:"Saved output background",exact:true});
  await expect(output).toBeVisible();
  near((await pixels(output,[[50,50]]))[0],[64,96,128,255]);
  await page.getByLabel("Clip object",{exact:true}).check();
  await page.getByLabel("Transform group",{exact:true}).check();
  await expect(page.getByRole("alert",{name:"Authored composition blend hierarchy unavailable"})).toContainText("No flattened fallback");
  await expect(page.getByRole("img",{name:"Authored composition blend",exact:true})).toHaveCount(0);
});

test("Studio commits the chosen blend and reuses it when returning to Canvas",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Canvas",exact:true}).click();
  await page.getByLabel("Create canvas object",{exact:true}).getByRole("button",{name:"rectangle",exact:true}).click();
  const mode=page.getByLabel("Canvas blend mode",{exact:true});
  await expect(mode).toHaveValue("normal");
  await mode.selectOption("screen");
  await expect(page.getByLabel("Canvas blend compatibility")).toContainText("requires a verified Film v2 provider");
  await page.getByRole("button",{name:"Commit style",exact:true}).click();
  await expect(page.getByRole("button",{name:"Commit style",exact:true})).toBeDisabled();
  const read=async()=>page.evaluate(async()=>{
    const path="/src/api.ts";return (await (await import(/* @vite-ignore */ path)).bootstrap()).project;
  });
  const saved=await read();
  const target=saved.scenes.flatMap((scene:{nodes:{id:string;style:{blend_mode:string}}[]})=>scene.nodes)
    .find((node:{style:{blend_mode:string}})=>node.style.blend_mode==="screen");
  expect(target).toBeTruthy();
  const source=()=>page.getByRole("img",{name:"Editable Canvas source composition",exact:true})
    .locator('[data-canvas-node-id="'+target.id+'"]').locator("..");
  await expect(source()).toHaveCSS("mix-blend-mode","screen");
  await page.getByRole("button",{name:"Audio",exact:true}).click();
  await page.getByRole("button",{name:"Canvas",exact:true}).click();
  await expect(source()).toHaveCSS("mix-blend-mode","screen");
  expect(await read()).toEqual(saved);
});
