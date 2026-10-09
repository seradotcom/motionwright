import {expect,test} from '@playwright/test';
test('native editor exposes its source boundary without fabricating frames or runtime installation',async({page},testInfo)=>{
  await page.goto('/');
  await page.getByRole('button',{name:'Production',exact:true}).click();
  await page.getByRole('button',{name:'Native editor',exact:true}).click();
  await expect(page.getByRole('heading',{name:'HyperFrames composition',exact:true})).toBeVisible();
  await expect(page.getByText('Desktop native service required',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Propose from current Canvas',exact:true})).toBeDisabled();
  await page.locator('.native-runtime-panel summary').click();
  await expect(page.getByRole('button',{name:'Inspect installed runtime',exact:true})).toBeDisabled();
  await expect(page.locator('.native-creative-editor img')).toHaveCount(0);
  await expect(page.getByText('Current native frames verified.',{exact:false})).toHaveCount(0);
  await page.screenshot({path:testInfo.outputPath('native-editor-browser-boundary.png'),fullPage:true});
});
