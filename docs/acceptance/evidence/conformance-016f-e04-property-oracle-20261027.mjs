/*
 * One real unit oracle for MW05-E04-02#/acceptance on immutable 016f source.
 * The complete product modules are imported, never copied into this harness.
 * Node strips only erasable TypeScript. The resolver adds explicit .ts suffixes
 * to the frozen local runtime import graph; it supplies no replacement exports.
 * Existing locked runtime dependencies, if required by the original imports,
 * must be retained as exact files. No replacement module or package install runs.
 * No Vitest, app fixture, service, SDK, renderer or browser runs.
 */
import test, { after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, lstatSync, realpathSync } from "node:fs";
import { createHash } from "node:crypto";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { registerHooks } from "node:module";

const SOURCE = "016fb46f51ca0d8eef26f34c89cbba5eecc40943";
const TREE = "de5fd1ddfcfc91a0ba7ac090b49dbcc2fa584058";
const SDK = "aa918f5b82eace7bc37ad5979f8d9eefff6a9aac";
const FIELD = "MW05-E04-02#/acceptance";
const CLAUSE = "2f5b961681adaaa033229b0b47124e43787c4f6a09ee76b4316177bd4d2260c9";
// The broker pins this exact file and places its sibling run directory inside
// the authorized private repository root. No private absolute path is embedded.
const Q = dirname(fileURLToPath(import.meta.url));
const RUN = join(Q, "e04-016f-nodeunit-original-r2");
const STUDIO = join(RUN, "source", "apps", "studio");
const EXPECTED_NODE = "v24.19.0";
const PRODUCT_PINS = {
  "src/creativeProduction.ts": {
    "bytes": 36639,
    "sha256": "5ee6199c10c113a25d44858531806eebed58e897f97ec46669a541ecfe4c8d96",
    "git_blob": "07e93de66e53dda7353b54fd0018fd0b148f4145"
  },
  "src/deliveryRecipe.ts": {
    "bytes": 24822,
    "sha256": "1dd204f9fbec4ce63ba8de8a60a7c3214b65c4ce59b0e2f9a54146fc5136f385",
    "git_blob": "e9c0ad34017c9f313e399603af10e6ce4ec24241"
  },
  "src/proceduralField.ts": {
    "bytes": 11819,
    "sha256": "3b0c179865d5db1741e25c9a407bdfbf6f0a19b46aade1bb3b0cb5cd934b8e5e",
    "git_blob": "348534588ae8695b672be5fab94f24f622be89e5"
  },
  "src/types.ts": {
    "bytes": 40539,
    "sha256": "83f2d7992d14744e6b05b53c548bedd3a8f18913fcfaf8864742f0b4308794e6",
    "git_blob": "79815ad599c4eda2b404fddee285350412a13220"
  },
  "src/audioRecipeContract.ts": {
    "bytes": 9018,
    "sha256": "2c1d6b9f669da87458c028e3635a8b58c6736d7af5b6c7095d0a85d8e23dbde9",
    "git_blob": "ee8ed3b9407c0cebe697d8acac87840302786744"
  },
  "src/deliveryRecipeDraft.ts": {
    "bytes": 54065,
    "sha256": "321a9ca2f744518bd93ee6dc837cea034cae1a53236619ab83612cbb05f5bc3e",
    "git_blob": "89b14c21a328d94612d0a2dbb80dd31fbd9513ff"
  },
  "src/projectMutation.ts": {
    "bytes": 12846,
    "sha256": "1195b5afb3e5fd2bdd1d74df6e99589f1bf60ece845bccc0da287a7c2123a614",
    "git_blob": "25239def2ab78a28274585afda75dc2ff6984304"
  },
  "package.json": {
    "bytes": 919,
    "sha256": "428526cf98577d3c960a1f13731cf89a1c1c19cb7a4e653a420d1e832c75c9b4",
    "git_blob": "2be45e9bde032f9e2c197789a8350dba017b72f0"
  },
  "package-lock.json": {
    "bytes": 83713,
    "sha256": "bf7e12b36faff3d553033f903560070974774700f404ff6704ed0942094bc994",
    "git_blob": "3603612cab7f9abbb132ec69aa267ca060f0b474"
  }
};
const DEPENDENCY_PINS = {
  "node_modules/react/package.json": {
    "bytes": 1242,
    "sha256": "6e003e4e9ed25afa7fdd55613f8aa877cefa9b359d9c13f81ee704030248e24e"
  },
  "node_modules/react/index.js": {
    "bytes": 186,
    "sha256": "60caffdecbdc5db3bc4ec4e83df9488345ccb271d07533b9210bf5750918d97e"
  },
  "node_modules/react/cjs/react.production.js": {
    "bytes": 18040,
    "sha256": "6b28feb9dbe9781ab1e1b64dea7b29a7c70f03d2dc10eaa966363053d8a9b4e5"
  },
  "node_modules/react/cjs/react.development.js": {
    "bytes": 49165,
    "sha256": "97bef56f30daa05320514ea220d7d03eb765635519ff2d5650c4887772aa5767"
  },
  "node_modules/react/LICENSE": {
    "bytes": 1088,
    "sha256": "da6d3703ed11cbe42bd212c725957c98da23cbff1998c05fa4b3d976d1a58e93"
  }
};
const CLOSURE_READY = true;
const started = new Date().toISOString();

assert.equal(CLOSURE_READY, true, "The frozen runtime closure has not been supplied.");
assert.equal(process.platform, "win32", "This original is for the authorized Windows executor.");
assert.equal(process.version, EXPECTED_NODE, "Use the observed exact Node version.");
assert.equal(process.env.NODE_OPTIONS, undefined, "External Node options are not part of this oracle.");
assert.equal(process.env.NODE_PATH, undefined, "External module paths are not part of this oracle.");
assert.equal(process.env.NODE_V8_COVERAGE, undefined, "No coverage output is authorized here.");
assert.equal(process.env.NODE_DISABLE_COMPILE_CACHE, "1", "The original must not write a compile cache.");
assert.equal(process.env.NODE_ENV, "production", "The selected existing React entry must be explicit.");

function digest(bytes, algorithm = "sha256") {
  return createHash(algorithm).update(bytes).digest("hex");
}
function unlinkedPath(path) {
  const absolute = resolve(path);
  const rel = relative(Q, absolute);
  assert(rel !== "" && !rel.startsWith(".." + sep) && rel !== ".." && !rel.includes(":"), "Path outside the private root.");
  for (let current = absolute; current !== Q; current = dirname(current)) {
    assert.notEqual(current, dirname(current), "Private root was not reached.");
    assert.equal(lstatSync(current).isSymbolicLink(), false, "Linked input is not allowed.");
  }
  assert.equal(lstatSync(Q).isSymbolicLink(), false, "Private root is linked.");
  assert.equal(realpathSync(absolute).toLowerCase(), absolute.toLowerCase(), "Input resolves elsewhere.");
  return absolute;
}
function productBytes(rel) {
  const expected = PRODUCT_PINS[rel] ?? DEPENDENCY_PINS[rel];
  assert(expected && Number.isInteger(expected.bytes), "Unknown source or dependency input.");
  const path = unlinkedPath(join(STUDIO, rel));
  assert.equal(lstatSync(path).isFile(), true, "Product input is not a regular file.");
  assert(lstatSync(path).size <= 256 * 1024, "Input exceeds the small-unit bound.");
  const bytes = readFileSync(path);
  const observed = {
    bytes: bytes.length,
    sha256: digest(bytes),
  };
  if (expected.git_blob !== undefined) {
    observed.git_blob = createHash("sha1").update(`blob ${bytes.length}\0`).update(bytes).digest("hex");
  }
  assert.deepEqual(observed, expected, "Product input differs from the frozen Git blob: " + rel);
  return { bytes, observed };
}
function inventory() {
  return Object.fromEntries([...Object.keys(PRODUCT_PINS), ...Object.keys(DEPENDENCY_PINS)].sort()
    .map(rel => [rel, productBytes(rel).observed]));
}
const before = inventory();
assert.equal(JSON.parse(productBytes("package.json").bytes).type, "module", "The original ESM package boundary must remain explicit.");
const moduleURLs = new Map([...Object.keys(PRODUCT_PINS), ...Object.keys(DEPENDENCY_PINS)]
  .filter(rel => rel.endsWith(".ts") || rel.endsWith(".js"))
  .map(rel => [pathToFileURL(join(STUDIO, rel)).href, rel]));
// Node may inspect the alternate CJS branch while discovering named exports.
// Retain its original bytes without requiring or claiming its execution.
const optionalLoaderObservation = "node_modules/react/cjs/react.development.js";
const requiredLoaderObservations = [...moduleURLs.values()].filter(rel => rel !== optionalLoaderObservation);
const entryURL = pathToFileURL(join(STUDIO, "src", "creativeProduction.ts")).href;
const loaded = new Set();
const importEdges = new Set();
const resolutionObservations = [];
const hooks = registerHooks({
  resolve(specifier, context, nextResolve) {
    if (moduleURLs.has(context.parentURL)) {
      const parent = moduleURLs.get(context.parentURL);
      let forwarded = specifier;
      let expected;
      if (specifier === "react" && parent.endsWith(".ts")) {
        expected = pathToFileURL(join(STUDIO, "node_modules", "react", "index.js")).href;
      } else {
        assert(specifier.startsWith("./"), "Unexpected runtime import outside the inspected graph: " + specifier);
        // CommonJS .js specifiers remain intact. Only extensionless TypeScript
        // edges need an explicit .ts suffix for Node's default ESM resolver.
        if (parent.endsWith(".ts") && !/\.(?:ts|js)$/.test(specifier)) forwarded += ".ts";
        expected = new URL(forwarded, context.parentURL).href;
      }
      assert(moduleURLs.has(expected), "Runtime import is outside the frozen graph: " + specifier);
      const resolved = nextResolve(forwarded, context);
      assert.equal(resolved.url, expected, "Node resolved a different product input.");
      importEdges.add(parent + " -> " + moduleURLs.get(resolved.url));
      resolutionObservations.push({ parent, specifier, forwarded_specifier: forwarded,
        target: moduleURLs.get(resolved.url), format: resolved.format ?? null });
      return resolved;
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (moduleURLs.has(url)) {
      const rel = moduleURLs.get(url);
      productBytes(rel);
      loaded.add(rel);
    }
    // The default loader reads the unchanged TS/JS file and performs Node's own
    // type stripping where needed. No source rewrite or replacement is returned.
    return nextLoad(url, context);
  },
});
const { validateNativePropertyFidelity: validate } = await import(entryURL);
assert.equal(typeof validate, "function", "The actual exported product validator must be imported.");
const harnessBytes = readFileSync(unlinkedPath(fileURLToPath(import.meta.url)));
process.stdout.write("# MW_E04_SOURCE " + JSON.stringify({
  pid: process.pid, node: process.version, executable: process.execPath,
  argv: process.argv, execArgv: process.execArgv, started_at_utc: started,
  source: SOURCE, tree: TREE, sdk: SDK, criterion_id: FIELD, source_clause_sha256: CLAUSE,
  harness_sha256: digest(harnessBytes), inputs_before: before,
  scope: "Actual pure classification units on complete byte-exact 016f TypeScript modules and exact retained runtime dependencies; Node type stripping, not Vitest or a product build.",
  historical_execution_relabelled: false,
}) + "\n");

function freeze(value) {
  if (value && typeof value === "object") {
    for (const child of Object.values(value)) freeze(child);
    Object.freeze(value);
  }
  return value;
}
function entry(path, fidelity, loss_note = null) {
  return { path, fidelity, loss_note };
}
function acceptsUnchanged(rows) {
  const beforeInput = structuredClone(rows);
  freeze(rows);
  assert.doesNotThrow(() => validate(rows));
  assert.deepEqual(rows, beforeInput, "Validation must not normalize or replace the declared categories.");
}
function rejectsUnchanged(rows, message) {
  const beforeInput = structuredClone(rows);
  freeze(rows);
  assert.throws(() => validate(rows), message);
  assert.deepEqual(rows, beforeInput, "A rejection must not rewrite caller-owned classifications.");
}

test("E04 accepts five distinct categories without coercion", () => {
  const categories = ["native", "translated", "baked", "approximate", "unavailable"];
  const rows = categories.map((category, index) => entry("source.property" + index, category,
    index >= 2 ? "The authored source remains; this category does not grant editable output." : null));
  acceptsUnchanged(rows);
  assert.deepEqual(rows.map(row => row.fidelity), categories);
  assert.equal(new Set(rows.map(row => row.fidelity)).size, 5);
});

test("E04 native and translated permit an absent loss note", () => {
  for (const category of ["native", "translated"]) {
    acceptsUnchanged([entry("source.mesh", category)]);
    acceptsUnchanged([entry("source.mesh", category, "Explicit author qualification retained.")]);
  }
});

test("E04 baked approximate and unavailable require meaningful loss notes", () => {
  for (const category of ["baked", "approximate", "unavailable"]) {
    rejectsUnchanged([entry("animation.keyframes", category)], /need a specific loss note/);
    rejectsUnchanged([entry("animation.keyframes", category, "")], /Invalid property loss note/);
    rejectsUnchanged([entry("animation.keyframes", category, "   ")], /Invalid property loss note/);
    acceptsUnchanged([entry("animation.keyframes", category, "Imported interpolation cannot be edited as original keyframes.")]);
  }
});

test("E04 unknown category values cannot become a supported state", () => {
  for (const category of ["translate", "bake", "approximation", "NATIVE", "", "native ", "verified", null, 0]) {
    rejectsUnchanged([entry("source.mesh", category, "An unknown category remains unsupported.")], /Unknown native property fidelity/);
  }
});

test("E04 one semantic property cannot carry duplicate classifications", () => {
  rejectsUnchanged([entry("camera.zoom", "native"), entry("camera.zoom", "unavailable", "No supported importer.")], /Duplicate/);
  rejectsUnchanged([entry("camera.zoom", "native"), entry("camera.zoom", "native")], /Duplicate/);
  acceptsUnchanged([entry("camera.zoom", "native"), entry("camera.focal_length", "unavailable", "No supported importer.")]);
});

test("E04 malformed paths and prototype names are rejected within exact bounds", () => {
  for (const path of ["", ".camera", "camera.", "geometry..mesh", "0camera.zoom", "camera-value",
    "a.b.c.d.e.f", "__proto__", "camera.__proto__", "a.constructor", "a.prototype",
    "native/source/scene", "x".repeat(129)]) {
    rejectsUnchanged([entry(path, "native")], /path is invalid/);
  }
  acceptsUnchanged([entry("a.b.c.d.e", "native"), entry("x".repeat(128), "translated")]);
});

test("E04 zero and sixty-four classifications are accepted but sixty-five are rejected", () => {
  acceptsUnchanged([]);
  acceptsUnchanged(Array.from({ length: 64 }, (_, index) => entry("camera.p" + index, "native")));
  rejectsUnchanged(Array.from({ length: 65 }, (_, index) => entry("camera.p" + index, "native")), /At most 64/);
});

test("E04 loss notes enforce UTF-8 byte limits and reject control characters", () => {
  acceptsUnchanged([entry("source.mesh", "approximate", "x".repeat(500))]);
  acceptsUnchanged([entry("source.mesh", "approximate", "\u{1f642}".repeat(125))]);
  rejectsUnchanged([entry("source.mesh", "approximate", "x".repeat(501))], /Invalid property loss note/);
  rejectsUnchanged([entry("source.mesh", "approximate", "\u{1f642}".repeat(126))], /Invalid property loss note/);
  for (const text of ["loss\0unknown", "loss\tunknown", "loss\u007funknown"]) {
    rejectsUnchanged([entry("source.mesh", "approximate", text)], /Invalid property loss note/);
  }
});

after(() => {
  const afterInputs = inventory();
  assert.deepEqual(afterInputs, before, "Exact product input bytes changed during the unit invocation.");
  assert(requiredLoaderObservations.every(rel => loaded.has(rel)), "A required original runtime module was not observed by the loader.");
  assert([...loaded].every(rel => requiredLoaderObservations.includes(rel) || rel === optionalLoaderObservation), "An unbound runtime module was observed.");
  process.stdout.write("# MW_E04_CLOSURE " + JSON.stringify({
    pid: process.pid, ended_at_utc: new Date().toISOString(),
    criterion_id: FIELD, execution_kind: "unit", execution_profile: "e04e05-property-classification-studio",
    runtime_modules_loaded: [...loaded].sort(), runtime_import_edges: [...importEdges].sort(),
    runtime_resolution_observations: resolutionObservations,
    CommonJS_specifiers_preserved: true, dependency_exports_synthesized: false,
    required_loader_observations: [...requiredLoaderObservations].sort(),
    optional_loader_observation: optionalLoaderObservation,
    loader_observations_are_not_function_coverage: true,
    inputs_after: afterInputs, inputs_stable: true,
    selected_case_count: 8, counts_and_exit_require_original_runner_terminal: true,
    resource_usage: process.resourceUsage(), memory_usage: process.memoryUsage(),
    no_claims: ["Vitest rerun", "TypeScript typechecking", "applyChange", "fixture persistence", "SQLite",
      "Host authority", "native media", "browser UI", "whole requirement acceptance", "release authorization"],
  }) + "\n");
  hooks.deregister();
});
