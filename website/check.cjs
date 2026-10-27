#!/usr/bin/env node
// Static bundle regression checks. No dependencies and no HTTP requests.
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const root=__dirname;
const html=fs.readFileSync(path.join(root,"index.html"),"utf8");
const js=fs.readFileSync(path.join(root,"site.js"),"utf8");
const css=fs.readFileSync(path.join(root,"site.css"),"utf8");
const urls=[...html.matchAll(/(?:src|href)="(\.\/[^"]+)"/g)].map(m=>m[1]);
for(const url of urls)assert(fs.statSync(path.resolve(root,url)).isFile(),"Missing local URL: "+url);
assert(urls.length >= 10 && html.includes('id="architecture"') && html.includes('id="screens"'));
assert(html.includes('id="lightbox"') && html.includes('data-lang="pt"'));
assert(!html.includes("https://www.sherlocks.ai/"));
assert(css.includes("@media(max-width:720px)"));
assert(js.includes('changeLanguage') && js.includes('showModal'));
const pics=["app-multicausal-kind-running","app-investigation-running","app-trace-running","app-awareness-running","app-dependencies-running"];
for(const name of pics){
  const f=fs.readFileSync(path.join(root,"assets",name+".png"));
  assert(f.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])),"Invalid PNG: "+name);
  assert.equal(f.readUInt32BE(16),1440);
}
const arch=fs.readFileSync(path.join(root,"architecture","ai-sre-rca.html"),"utf8");
assert(arch.includes('name="generator" content="archify 3.1.0"'));
assert(arch.includes("<svg"),"Interactive Archify diagram absent");
console.log("Tracecroft static bundle OK:",urls.length,"relative resources,",pics.length,"real PNG captures, Archify HTML");
