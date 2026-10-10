// Explicit installer finalization. Never called while opening a document.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const root=path.dirname(fileURLToPath(import.meta.url)),require=createRequire(path.join(root,'package.json'));
const hash=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const pkg=name=>JSON.parse(fs.readFileSync(require.resolve(name+'/package.json'),'utf8'));
for(const [name,version] of [['@hyperframes/core','0.8.143'],['gsap','3.15.0'],['playwright','1.55.1'],['fontkit','2.0.4']]) {
  // Some packages do not expose package.json; use their installed root, never an external lookup.
  const installed=JSON.parse(fs.readFileSync(path.join(root,'node_modules',name,'package.json'),'utf8'));
  if(installed.version!==version)throw new Error('Installed dependency version differs: '+name);
}
const native={
  hyperframes_script:require.resolve('@hyperframes/core/runtime'),
  gsap_script:path.join(root,'node_modules/gsap/dist/gsap.min.js'),
  sans_font:path.join(root,'node_modules/@fontsource-variable/instrument-sans/files/instrument-sans-latin-wght-normal.woff2'),
  mono_font:path.join(root,'node_modules/@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2'),
};
const files={};
for(const [key,file] of Object.entries(native)) {
  const resolved=fs.realpathSync(file);if(!resolved.startsWith(root+path.sep))throw new Error('Dependency escaped runtime root');
  const bytes=fs.readFileSync(resolved);files[key]={path:path.relative(root,resolved).split(path.sep).join('/'),sha256:hash(bytes),bytes:bytes.length};
}
const browserIndex=JSON.parse(fs.readFileSync(path.join(root,'node_modules/playwright-core/browsers.json'),'utf8'));
const revision=browserIndex.browsers.find(entry=>entry.name==='chromium-headless-shell')?.revision;
if(!/^[0-9]+$/.test(revision || ''))throw new Error('Pinned headless browser revision is absent');
const browser=path.join(root,'.browsers','chromium_headless_shell-'+revision,'chrome-linux','headless_shell');
if(!fs.realpathSync(browser).startsWith(root+path.sep))throw new Error('Install browser into this profile root, not an ambient user cache');
files.browser={path:path.relative(root,fs.realpathSync(browser)).split(path.sep).join('/'),sha256:hash(fs.readFileSync(browser)),bytes:fs.statSync(browser).size};
const inventoryRows=[];let totalBytes=0;
function inventoryTree(directory) {
  for(const entry of fs.readdirSync(directory,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name,'en'))) {
    if(entry.name==='.bin')continue;
    const file=path.join(directory,entry.name),relative=path.relative(root,file).split(path.sep).join('/');
    if(entry.isSymbolicLink())throw new Error('Installed runtime code must not traverse a symlink: '+relative);
    if(entry.isDirectory()){inventoryTree(file);continue;}
    if(!entry.isFile())throw new Error('Installed runtime contains a non-regular program dependency');
    const size=fs.statSync(file).size;totalBytes+=size;
    if(inventoryRows.length>=20000||size>512*1024*1024||totalBytes>2*1024*1024*1024)throw new Error('Runtime inventory exceeds the admitted profile');
    inventoryRows.push({path:relative,sha256:hash(fs.readFileSync(file)),bytes:size});
  }
}
inventoryTree(path.join(root,'node_modules'));inventoryTree(path.dirname(browser));
const inventoryBody=JSON.stringify({schema:1,files:inventoryRows},null,2)+'\n';
if(Buffer.byteLength(inventoryBody)>8*1024*1024)throw new Error('Runtime inventory manifest exceeds its budget');
fs.writeFileSync(path.join(root,'runtime-files.json'),inventoryBody,{flag:'wx'});
const inventory={path:'runtime-files.json',sha256:hash(Buffer.from(inventoryBody)),files:inventoryRows.length,bytes:totalBytes};
const lock=fs.readFileSync(path.join(root,'package-lock.json'));
const receipt={schema:1,hyperframes:'0.8.143',gsap:'3.15.0',playwright:'1.55.1',fontkit:'2.0.4',npm_lock_sha256:hash(lock),
  capture_profile:'hyperframes-core-chromium-png-v2',platform:process.platform,architecture:process.arch,files,inventory,
  package_rights:'Owner-installed dependencies retain their own licenses. Installation is not source-code or asset redistribution permission.',
  sandbox:'Chromium userns sandbox outside Host; inside verified rootless no-new-privileges, capability-free, AppArmor-enforced Semwright bwrap, the outer sandbox replaces nested Chromium userns. No network, imported code or ambient filesystem access.'};
fs.writeFileSync(path.join(root,'runtime.json'),JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify({runtime:'hyperframes-core',version:receipt.hyperframes,lock_sha256:receipt.npm_lock_sha256,receipt_sha256:hash(fs.readFileSync(path.join(root,'runtime.json'))),files}));
