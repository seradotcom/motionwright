import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { candidateFrontendNotices } from "./frontend_dependency_notices.mjs";

const INDEX = "assets/licenses/frontend/index.json";
const SOURCE = "1".repeat(40);
const hash = (value) => createHash("sha256").update(value).digest("hex");
const fixtureParent = process.env.MOTIONWRIGHT_FRONTEND_NOTICES_FIXTURE_ROOT ??
  path.dirname(fileURLToPath(import.meta.url));

function write(filename, bytes) {
  fs.mkdirSync(path.dirname(filename), { recursive: true });
  fs.writeFileSync(filename, bytes);
}
function writeJson(filename, value) {
  write(filename, JSON.stringify(value, null, 2) + "\n");
}
function controlledFixture(t) {
  const root = fs.mkdtempSync(path.join(fixtureParent, "frontend-notices-fixture-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const project = path.join(root, "apps", "studio");
  const manifestPath = path.join(project, "package.json");
  const lockPath = path.join(project, "package-lock.json");
  const manifest = { name: "@fixture/studio", version: "1.0.0", private: true,
    dependencies: {}, devDependencies: {}, optionalDependencies: {} };
  const lock = { name: manifest.name, version: manifest.version, lockfileVersion: 3, packages: {} };
  const tracked = new Set([manifestPath, lockPath]);
  let reportedHead = SOURCE;
  const flush = () => {
    lock.packages[""] = structuredClone(manifest);
    writeJson(manifestPath, manifest);
    writeJson(lockPath, lock);
  };
  flush();
  function addPackage(name, options = {}) {
    const version = options.version ?? "1.0.0";
    const key = options.lockKey ?? "node_modules/" + name;
    const packageRoot = path.join(project, ...key.split("/"));
    const packageJson = path.join(packageRoot, "package.json");
    const modulePath = path.join(packageRoot, "index.js");
    const licensePath = path.join(packageRoot, options.licenseFile ?? "LICENSE");
    const declaration = options.licenseDeclaration ?? "MIT";
    const metadata = { name, version, license: declaration, ...options.metadata };
    writeJson(packageJson, metadata);
    write(modulePath, options.code ?? "export const fixture = 1;\n");
    if (options.withLicense !== false) write(licensePath, options.licenseBytes ?? Buffer.from("Fixture license text.\r\nNo rights are inferred by this test.\r\n"));
    if (options.noticeBytes) write(path.join(packageRoot, "NOTICE"), options.noticeBytes);
    lock.packages[key] = {
      name, version, resolved: "https://registry.npmjs.org/" + name + "/-/" + name.split("/").at(-1) + "-" + version + ".tgz",
      integrity: "sha512-" + Buffer.alloc(64, 17).toString("base64"),
      license: declaration, ...options.lock,
    };
    if (!options.lockKey) manifest[options.role ?? "dependencies"][name] = version;
    flush();
    return { name, version, key, root: packageRoot, packageJson, module: modulePath, license: licensePath };
  }
  function local(relative = "src/main.js", code = "export const local = true;\n") {
    const filename = path.join(project, ...relative.split("/"));
    write(filename, code);
    tracked.add(filename);
    return filename;
  }
  function options() {
    const trackedSha256 = {};
    for (const filename of tracked) {
      trackedSha256[path.relative(root, filename).split(path.sep).join("/")] = hash(fs.readFileSync(filename));
    }
    return { sourceRoot: root, projectRoot: project, sourceSha: SOURCE,
      fixture: { mode: "explicit-synthetic", sourceSha: SOURCE, trackedSha256, readHead: () => reportedHead } };
  }
  function start(customOptions = options()) {
    const plugin = candidateFrontendNotices(customOptions);
    const context = {
      infos: new Map(), emitted: [],
      getModuleInfo(id) { return this.infos.get(id); },
      emitFile(asset) {
        this.emitted.push({ ...asset, source: Buffer.from(asset.source) });
        return "asset-" + (this.emitted.length - 1);
      },
      getFileName(reference) { return this.emitted[Number(reference.slice(6))].fileName; },
    };
    plugin.configResolved({ command: "build", root: project });
    plugin.buildStart.call(context);
    return { plugin, context,
      observe(id, code = fs.readFileSync(id.split(/[?#]/u, 1)[0], "utf8")) {
        const info = { id, code, isExternal: false };
        context.infos.set(id, info);
        plugin.moduleParsed.call(context, info);
      },
      generate(entries, extra = {}) {
        const modules = Object.fromEntries(entries.map((entry) =>
          Array.isArray(entry) ? [entry[0], { renderedLength: entry[1] }] : [entry, { renderedLength: 1 }]));
        const bundle = { "assets/app.js": {
          type: "chunk", fileName: "assets/app.js", code: "export const built = 7;\n", modules,
        }, ...extra };
        plugin.generateBundle.handler.call(context, { format: "es" }, bundle);
        const emittedIndex = context.emitted.find((asset) => asset.fileName === INDEX);
        return { index: JSON.parse(emittedIndex.source.toString("utf8")), bundle };
      },
    };
  }
  return { root, project, manifest, lock, manifestPath, lockPath, addPackage, local, options, start, flush,
    setHead(value) { reportedHead = value; } };
}
function deniedBeforeEmission(build, entries, expression) {
  assert.throws(() => build.generate(entries), expression);
  assert.equal(build.context.emitted.length, 0, "failure must precede the first notice/index emission");
}
function overwriteSameSize(filename, replacement) {
  const before = fs.statSync(filename);
  const old = fs.readFileSync(filename);
  const next = replacement ?? Buffer.from(old);
  if (!replacement) next[0] ^= 1;
  assert.equal(next.length, old.length, "causal control must preserve file length");
  fs.writeFileSync(filename, next);
  fs.utimesSync(filename, before.atime, before.mtime);
}

test("emitted dev/optional packages retain frontend-emitted role, while zero-rendered packages are omitted", (t) => {
  const f = controlledFixture(t);
  const dev = f.addPackage("dev-rendered", { role: "devDependencies", lock: { dev: true } });
  const optional = f.addPackage("optional-rendered", { role: "optionalDependencies", lock: { optional: true } });
  const unused = f.addPackage("unused-no-license", { withLicense: false, role: "devDependencies", lock: { dev: true } });
  const build = f.start();
  [dev, optional, unused].forEach((item) => build.observe(item.module));
  const { index } = build.generate([dev.module, optional.module, [unused.module, 0]]);
  assert.deepEqual(index.components.map((item) => item.name).sort(), ["dev-rendered", "optional-rendered"]);
  assert.ok(index.components.every((item) => item.role === "frontend-emitted"));
  assert.equal(index.components.find((item) => item.name === "dev-rendered").lock_classification_informational.dev, true);
  assert.equal(index.components.find((item) => item.name === "optional-rendered").lock_classification_informational.optional, true);
  assert.equal(index.full_package_inventory, "NOT_VERIFIED");
  assert.equal(index.non_chunk_asset_dependency_closure, "NOT_VERIFIED");
});

test("original notice bytes are deduplicated without losing component provenance or choosing a license branch", (t) => {
  const f = controlledFixture(t);
  const bytes = Buffer.from("Exact fixture text.\r\nSecond line.\r\n", "utf8");
  const a = f.addPackage("first", { licenseBytes: bytes, licenseDeclaration: "MIT OR Apache-2.0" });
  const b = f.addPackage("second", { licenseBytes: bytes, licenseDeclaration: "MIT OR Apache-2.0" });
  const build = f.start();
  build.observe(a.module, "export const transformed = 'different';\n");
  build.observe(b.module);
  const { index } = build.generate([a.module, b.module]);
  assert.equal(index.generated_notice_assets.length, 1);
  const notice = build.context.emitted.find((item) => item.fileName !== INDEX);
  assert.deepEqual(notice.source, bytes);
  for (const component of index.components) {
    assert.equal(component.package_metadata.license_declaration, "MIT OR Apache-2.0");
    assert.equal(component.notices[0].sha256, hash(bytes));
    assert.equal(component.notices[0].bytes, bytes.length);
    assert.equal(component.notices[0].source_path_in_package, "LICENSE");
    assert.equal(component.source.package_archive_authentication, "NOT_VERIFIED");
  }
  const module = index.modules.find((item) => item.component_id.startsWith("npm:first@"));
  assert.equal(module.source_path_in_package, "index.js");
  assert.equal(module.raw_source.sha256, hash(fs.readFileSync(a.module)));
  assert.notEqual(module.raw_source.sha256, module.rollup_transformed_code.sha256);
});

test("same-size raw module mutation after moduleParsed is rejected even when mtime is restored", (t) => {
  const f = controlledFixture(t), pkg = f.addPackage("changing-code"), build = f.start();
  build.observe(pkg.module);
  overwriteSameSize(pkg.module);
  deniedBeforeEmission(build, [pkg.module], /SOURCE_CHANGED/);
});

test("same-size license mutation after moduleParsed is rejected even when mtime is restored", (t) => {
  const f = controlledFixture(t), pkg = f.addPackage("changing-license"), build = f.start();
  build.observe(pkg.module);
  overwriteSameSize(pkg.license);
  deniedBeforeEmission(build, [pkg.module], /SOURCE_CHANGED/);
});

test("package metadata and package lock mutations cannot be blessed at generateBundle", async (t) => {
  for (const target of ["package", "lock"]) {
    await t.test(target, (child) => {
      const f = controlledFixture(child), pkg = f.addPackage("changing-metadata"), build = f.start();
      build.observe(pkg.module);
      const filename = target === "package" ? pkg.packageJson : f.lockPath;
      overwriteSameSize(filename, Buffer.from(fs.readFileSync(filename, "utf8").replace('"MIT"', '"ISC"')));
      deniedBeforeEmission(build, [pkg.module], /SOURCE_CHANGED/);
    });
  }
});

test("a notice added after observation changes the admitted notice set", (t) => {
  const f = controlledFixture(t), pkg = f.addPackage("new-notice"), build = f.start();
  build.observe(pkg.module);
  write(path.join(pkg.root, "NOTICE"), "Additional fixture notice.\n");
  deniedBeforeEmission(build, [pkg.module], /NOTICE_SET_CHANGED/);
});

test("HEAD changes and pre-existing dirty tracked manifests remain distinct failures", (t) => {
  const f = controlledFixture(t), pkg = f.addPackage("head-bound");
  const build = f.start();
  build.observe(pkg.module);
  f.setHead("2".repeat(40));
  deniedBeforeEmission(build, [pkg.module], /HEAD_CHANGED/);
  f.setHead(SOURCE);
  const options = f.options();
  write(f.manifestPath, fs.readFileSync(f.manifestPath, "utf8").replace('"private": true', '"private": false'));
  assert.throws(() => f.start(options), /TRACKED_BYTES_MISMATCH/);
});

test("unobserved rendered module is never silently omitted", (t) => {
  const f = controlledFixture(t), pkg = f.addPackage("orphan-emitted"), build = f.start();
  deniedBeforeEmission(build, [pkg.module], /UNOBSERVED_EMITTED_MODULE/);
});

test("missing npm lock identity, package manifest, source, and license text all fail closed", async (t) => {
  for (const kind of ["lock-entry", "package-json", "source", "license"]) {
    await t.test(kind, (child) => {
      const f = controlledFixture(child), pkg = f.addPackage("unknown-package");
      if (kind === "lock-entry") { delete f.lock.packages[pkg.key]; f.flush(); }
      if (kind === "package-json") fs.unlinkSync(pkg.packageJson);
      if (kind === "source") { delete f.lock.packages[pkg.key].resolved; f.flush(); }
      if (kind === "license") {
        fs.unlinkSync(pkg.license);
        write(path.join(pkg.root, "NOTICE"), "A notice alone does not supply license text.\n");
      }
      const build = f.start();
      build.observe(pkg.module);
      deniedBeforeEmission(build, [pkg.module],
        /LOCK_PACKAGE_MISSING|SOURCE_UNREADABLE|PACKAGE_SOURCE_MISSING|LICENSE_TEXT_MISSING/);
    });
  }
});

test("physical local and virtual modules stay separate and never expose absolute virtual IDs", (t) => {
  const f = controlledFixture(t), local = f.local();
  const virtual = "\0virtual:" + path.join(f.root, "private-unpublished-name");
  const build = f.start();
  build.observe(local);
  build.observe(virtual, "export const generated = true;\n");
  const { index } = build.generate([local, virtual], {
    "assets/app.css": { type: "asset", fileName: "assets/app.css", source: "body { color: red; }\n" },
  });
  assert.equal(index.components.length, 0);
  assert.deepEqual(index.modules.map((item) => item.kind).sort(), ["local", "virtual"]);
  assert.ok(index.modules.every((item) => item.npm_license_evidence === false));
  assert.equal(index.modules.find((item) => item.kind === "virtual").virtual_origin, "NOT_VERIFIED");
  assert.equal(JSON.stringify(index).includes(f.root), false);
  const css = index.original_outputs.find((item) => item.path.endsWith(".css"));
  assert.equal(css.sha256, hash("body { color: red; }\n"));
  assert.equal(index.non_chunk_asset_dependency_closure, "NOT_VERIFIED");
});

test("a module outside the admitted root is rejected, never converted to local or virtual", (t) => {
  const f = controlledFixture(t), build = f.start();
  const outside = path.join(f.root, "..", "unadmitted-module.js");
  build.observe(outside, "export const wrong = true;");
  deniedBeforeEmission(build, [outside], /OUTSIDE_ROOT/);
});

test("transformed code changes after observation are detected independently of disk bytes", (t) => {
  const f = controlledFixture(t), pkg = f.addPackage("transformed"), build = f.start();
  build.observe(pkg.module);
  build.context.infos.set(pkg.module, { id: pkg.module, code: "export const changed = true;", isExternal: false });
  deniedBeforeEmission(build, [pkg.module], /TRANSFORMED_CODE_CHANGED/);
});

test("nested installed versions are resolved by their exact lock key, not only name", (t) => {
  const f = controlledFixture(t);
  const first = f.addPackage("same-name", { version: "1.0.0" });
  const second = f.addPackage("same-name", { version: "2.0.0", lockKey: "node_modules/outer/node_modules/same-name" });
  const build = f.start();
  build.observe(first.module); build.observe(second.module);
  const { index } = build.generate([first.module, second.module]);
  assert.equal(index.components.length, 2);
  assert.deepEqual(index.components.map((item) => item.version).sort(), ["1.0.0", "2.0.0"]);
  assert.deepEqual(index.components.map((item) => item.lock_key).sort(), [second.key, first.key].sort());
});

test("explicit license-file is retained, while similarly named application code under dist is not a notice", (t) => {
  const f = controlledFixture(t);
  const pkg = f.addPackage("explicit-license", {
    licenseFile: "legal/TERMS.txt", licenseDeclaration: "SEE LICENSE IN legal/TERMS.txt",
    metadata: { "license-file": "legal/TERMS.txt" },
  });
  write(path.join(pkg.root, "dist", "icons", "copyright.js.map"), '{"not":"a license"}\n');
  const build = f.start();
  build.observe(pkg.module);
  const { index } = build.generate([pkg.module]);
  assert.deepEqual(index.components[0].notices.map((item) => item.source_path_in_package), ["legal/TERMS.txt"]);
});

test("explicit license paths cannot escape the package or become absolute public paths", (t) => {
  const f = controlledFixture(t);
  const pkg = f.addPackage("escaping-license", { metadata: { "license-file": "../outside-license" } });
  const build = f.start();
  build.observe(pkg.module);
  deniedBeforeEmission(build, [pkg.module], /UNSAFE_PUBLIC_PATH/);
});

test("linked package declaration is rejected without pretending its target is registry authenticated", (t) => {
  const f = controlledFixture(t), pkg = f.addPackage("linked", { lock: { link: true } }), build = f.start();
  build.observe(pkg.module);
  deniedBeforeEmission(build, [pkg.module], /LINK_PACKAGE_UNSUPPORTED/);
});

test("output names cannot collide with notice assets or case-fold aliases", (t) => {
  const f = controlledFixture(t), pkg = f.addPackage("collision"), build = f.start();
  build.observe(pkg.module);
  assert.throws(() => build.generate([pkg.module], {
    [INDEX]: { type: "asset", fileName: INDEX, source: "{}" },
  }), /OUTPUT_COLLISION/);
  assert.equal(build.context.emitted.length, 0);
  assert.throws(() => build.generate([pkg.module], {
    "assets/App.js": { type: "asset", fileName: "assets/App.js", source: "different" },
  }), /OUTPUT_COLLISION/);
  assert.equal(build.context.emitted.length, 0);
});

test("index binds original output bytes without hashing itself; late chunk mutation aborts writeBundle", (t) => {
  const f = controlledFixture(t), pkg = f.addPackage("output-bound"), build = f.start();
  build.observe(pkg.module);
  const { index, bundle } = build.generate([pkg.module]);
  assert.equal(index.original_outputs[0].sha256, hash(bundle["assets/app.js"].code));
  assert.ok(index.original_outputs.every((item) => !item.path.startsWith("assets/licenses/frontend/")));
  for (const asset of build.context.emitted) bundle[asset.fileName] = { ...asset };
  build.plugin.writeBundle.handler.call(build.context, { format: "es" }, bundle);
  bundle["assets/app.js"].code += "\n// changed after the index\n";
  assert.throws(() => build.plugin.writeBundle.handler.call(build.context, { format: "es" }, bundle), /LATE_OUTPUT_CHANGED/);
});

test("build epochs cannot reuse moduleParsed observations from an earlier build", (t) => {
  const f = controlledFixture(t), pkg = f.addPackage("cached"), build = f.start();
  build.observe(pkg.module);
  build.generate([pkg.module]);
  const emittedBefore = build.context.emitted.length;
  build.plugin.buildStart.call(build.context);
  assert.equal(build.plugin.shouldTransformCachedModule(), true);
  assert.throws(() => build.generate([pkg.module]), /UNOBSERVED_EMITTED_MODULE/);
  assert.equal(build.context.emitted.length, emittedBefore);
});

test("caller-supplied completeness flags are not accepted as inventory evidence", () => {
  assert.throws(() => candidateFrontendNotices({ complete: true }), /INVALID_OPTIONS/);
});

test("explicit projectRoot must equal Vite resolved root", (t) => {
  const f = controlledFixture(t);
  const options = f.options();
  const plugin = candidateFrontendNotices(options);
  plugin.configResolved({ command: "build", root: options.sourceRoot });
  assert.throws(() => plugin.buildStart.call({}), /PROJECT_ROOT_MISMATCH/);
});


function materializedOutput(build, f, pkg, label, format = "es", code = "export const built = 7;\n") {
  const options = { dir: path.join(f.project, label), format };
  const bundle = { "assets/app.js": { type: "chunk", fileName: "assets/app.js", code,
    modules: { [pkg.module]: { renderedLength: 1 } } } };
  const start = build.context.emitted.length;
  build.plugin.generateBundle.handler.call(build.context, options, bundle);
  for (const asset of build.context.emitted.slice(start)) bundle[asset.fileName] = { ...asset };
  for (const [key, value] of Object.entries(bundle)) {
    write(path.join(options.dir, key), value.type === "chunk" ? value.code : value.source);
  }
  return { options, bundle };
}

test("custody regression: exact physical output remains accepted", (t) => {
  const f = controlledFixture(t), pkg = f.addPackage("physical-control"), build = f.start();
  build.observe(pkg.module);
  const output = materializedOutput(build, f, pkg, "physical-control");
  assert.doesNotThrow(() => build.plugin.writeBundle.handler.call(build.context, output.options, output.bundle));
});

test("custody regression: changed output fileName is rejected", (t) => {
  const f = controlledFixture(t), pkg = f.addPackage("renamed-output"), build = f.start();
  build.observe(pkg.module);
  const output = materializedOutput(build, f, pkg, "renamed-output");
  output.bundle["assets/app.js"].fileName = "assets/renamed.js";
  assert.throws(() => build.plugin.writeBundle.handler.call(build.context, output.options, output.bundle),
    /LATE_OUTPUT_CHANGED/);
});

test("custody regression: physical output mutation cannot hide behind unchanged Rollup objects", (t) => {
  const f = controlledFixture(t), pkg = f.addPackage("mutated-output"), build = f.start();
  build.observe(pkg.module);
  const output = materializedOutput(build, f, pkg, "mutated-output");
  const filename = path.join(output.options.dir, "assets/app.js");
  const original = fs.readFileSync(filename);
  const mutated = Buffer.from(original); mutated[0] ^= 1;
  fs.writeFileSync(filename, mutated);
  assert.throws(() => build.plugin.writeBundle.handler.call(build.context, output.options, output.bundle),
    /PHYSICAL_OUTPUT_CHANGED/);
});

test("custody regression: independent formats with identical relative names select their own snapshot", (t) => {
  const f = controlledFixture(t), pkg = f.addPackage("multiple-output"), build = f.start();
  build.observe(pkg.module);
  const es = materializedOutput(build, f, pkg, "out-es", "es", "export const built = 7;\n");
  const cjs = materializedOutput(build, f, pkg, "out-cjs", "cjs", "module.exports = { built: 7 };\n");
  assert.doesNotThrow(() => build.plugin.writeBundle.handler.call(build.context, es.options, es.bundle));
  assert.doesNotThrow(() => build.plugin.writeBundle.handler.call(build.context, cjs.options, cjs.bundle));
});

test("physical output namespace cannot be reused by another output.file or format", (t) => {
  const f = controlledFixture(t), pkg = f.addPackage("shared-output-directory"), build = f.start();
  build.observe(pkg.module);
  const first = materializedOutput(build, f, pkg, "shared-output");
  const count = build.context.emitted.length;
  const bundle = { "other.js": { type: "chunk", fileName: "other.js",
    code: "module.exports = 1;\n", modules: { [pkg.module]: { renderedLength: 1 } } } };
  assert.throws(() => build.plugin.generateBundle.handler.call(build.context,
    { file: path.join(first.options.dir, "other.js"), format: "cjs" }, bundle),
    /DUPLICATE_OUTPUT_DESTINATION/);
  assert.equal(build.context.emitted.length, count);
});
test("case-only physical output directories conflict before a second notice index is emitted", (t) => {
  const f = controlledFixture(t), pkg = f.addPackage("case-output-directory"), build = f.start();
  build.observe(pkg.module);
  materializedOutput(build, f, pkg, "case-output");
  const count = build.context.emitted.length;
  const bundle = { "app.js": { type: "chunk", fileName: "app.js",
    code: "export const second = 1;\n", modules: { [pkg.module]: { renderedLength: 1 } } } };
  assert.throws(() => build.plugin.generateBundle.handler.call(build.context,
    { dir: path.join(f.project, "CASE-OUTPUT"), format: "cjs" }, bundle),
    /DUPLICATE_OUTPUT_DESTINATION/);
  assert.equal(build.context.emitted.length, count);
});
