import { expect, test } from "@playwright/test";
import { createContextLabelJourney, verifyContextLabelJourney } from "./support/contextLabelJourney";

// Real browser interaction with the product's synthetic fixture mode only.
// Durable Store/Service reopening and Native realization have separate cases.
test("a context label follows an authored curved and held path with independent editable text and opacity",async({page},info)=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Add scene",exact:true}).click();
  await page.getByLabel("New scene name",{exact:true}).fill("Context label composition");
  await page.getByRole("button",{name:"Add",exact:true}).click();
  await page.getByRole("button",{name:"Canvas",exact:true}).click();
  await page.locator(".tree-row").filter({hasText:"Context label composition"}).click();
  const created=await createContextLabelJourney(page);
  await verifyContextLabelJourney(page,created.labelName,created.sourceName);
  await page.getByRole("button",{name:"Timeline",exact:true}).click();
  const program=page.getByRole("img",{name:"Program Canvas editorial study: not native output",exact:true});
  const label=program.locator('[data-canvas-node-id="'+created.labelId+'"]');
  await expect(label).toHaveAttribute("opacity","0.25");
  await expect(label.locator("text")).toHaveAttribute("x","612.5");
  await expect(label).toContainText("Measured result");
  await page.getByRole("button",{name:"Canvas",exact:true}).click();
  await page.locator(".canvas-tree-row").filter({hasText:created.labelName}).click();
  const inspector=page.locator(".canvas-draft-inspector").filter({has:page.locator('[id="canvas-draft-'+created.labelId+'-x"]')});
  await expect(inspector).toHaveCount(1);
  await expect(inspector.getByLabel("Canvas text",{exact:true})).toHaveValue("Measured result");
  await expect(page.getByRole("region",{name:"Typed motion keyframes",exact:true}).locator(".motion-keyframe-row")).toHaveCount(2);
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+created.revision);
  await page.getByLabel("Motion playhead",{exact:true}).fill("1.5");
  const sourceStudy=page.getByRole("img",{name:"Editable Canvas source composition",exact:true});
  const paintedLabel=sourceStudy.locator('[data-canvas-node-id="'+created.labelId+'"] text');
  await expect(paintedLabel).toHaveText("Measured result");
  await expect(paintedLabel).toHaveAttribute("font-size","32");
  await expect(sourceStudy.locator('[data-canvas-node-id="'+created.sourceId+'"] rect')).toHaveAttribute("width","240");
  for(const width of [1440,1024,1920]){
    await page.setViewportSize({width,height:940});
    const paintedShape=sourceStudy.locator('[data-canvas-node-id="'+created.sourceId+'"] rect');
    const hitTarget=page.getByRole("button",{name:"Canvas object "+created.sourceName,exact:true});
    await expect.poll(async()=>{
      const [paint,hit]=await Promise.all([paintedShape.boundingBox(),hitTarget.boundingBox()]);
      return paint && hit ? Math.max(...(["x","y","width","height"] as const).map(key=>Math.abs(paint[key]-hit[key]))) : Infinity;
    }).toBeLessThan(1);
    const metrics=await paintedLabel.evaluate(element=>{
      const box=(element as SVGGraphicsElement).getBBox();
      return {x:box.x,y:box.y,width:box.width,height:box.height};
    });
    await info.attach("source-label-metrics-"+width,{body:JSON.stringify(metrics),contentType:"application/json"});
    // Browser font boxes include ascenders above the editorial baseline. The
    // source text must retain one complete line at its authored size in every
    // viewport; Native FixedTxt owns final glyph metrics and admission.
    expect(metrics.width).toBeLessThanOrEqual(320);
    expect(metrics.height).toBeLessThanOrEqual(56);
    await expect(paintedLabel).toHaveAttribute("x","680");
  }
  await page.screenshot({path:info.outputPath("editable-context-label-follow.png")});
});
