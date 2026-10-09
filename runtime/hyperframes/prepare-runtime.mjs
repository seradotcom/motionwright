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
const browser=require('playwright').firefox.executablePath();
if(!fs.realpathSync(browser).startsWith(root+path.sep))throw new Error('Install browser into this profile root, not an ambient user cache');
files.browser={path:path.relative(root,fs.realpathSync(browser)).split(path.sep).join('/'),sha256:hash(fs.readFileSync(browser)),bytes:fs.statSync(browser).size};
const lock=fs.readFileSync(path.join(root,'package-lock.json'));
const receipt={schema:1,hyperframes:'0.8.143',gsap:'3.15.0',playwright:'1.55.1',fontkit:'2.0.4',npm_lock_sha256:hash(lock),
  capture_profile:'hyperframes-core-firefox-png-v1',platform:process.platform,architecture:process.arch,files,
  package_rights:'Owner-installed dependencies retain their own licenses. Installation is not source-code or asset redistribution permission.',
  sandbox:'Browser sandbox retained. Driver Host separately confines this process and its dependency roots. No external URL inputs.'};
fs.writeFileSync(path.join(root,'runtime.json'),JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify({runtime:'hyperframes-core',version:receipt.hyperframes,lock_sha256:receipt.npm_lock_sha256,receipt_sha256:hash(fs.readFileSync(path.join(root,'runtime.json')))}));
